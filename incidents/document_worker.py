"""Bounded, offline document extraction in a short-lived subprocess. No Django or credentials."""
import io
import json
import re
import sys
import warnings
import zipfile
import xml.etree.ElementTree as ET

MAX_EXPANDED = 8 * 1024 * 1024
NS = {'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


def docx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = archive.infolist()
        if len(info) > 150 or sum(i.file_size for i in info) > MAX_EXPANDED:
            raise ValueError('El documento excede los límites de complejidad.')
        for item in info:
            name = item.filename.lower()
            if item.flag_bits & 1 or '..' in name.split('/') or name.startswith('/') or '\\' in name or any(s in name for s in ('vbaproject', 'embeddings/', 'activex/', 'macros')):
                raise ValueError('No se admiten documentos cifrados o con contenido activo.')
            if item.file_size > 2_000_000 or (item.file_size > 100000 and item.file_size > max(item.compress_size, 1)*150):
                raise ValueError('Compresión o tamaño de componente no admitidos.')
            if name.endswith(('.xml', '.rels')):
                xml = archive.read(item)
                if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper() or re.search(br'TargetMode\s*=\s*["\x27]External', xml, re.I):
                    raise ValueError('El documento contiene entidades o vínculos externos no admitidos.')
        content_types = archive.read('[Content_Types].xml')
        if b'macroEnabled' in content_types or b'wordprocessingml.document.main+xml' not in content_types:
            raise ValueError('El archivo no es un DOCX ordinario.')
        tree = ET.fromstring(archive.read('word/document.xml'))
        if sum(1 for _ in tree.iter()) > 12000:
            raise ValueError('El documento tiene demasiados elementos.')
        def text(cell):
            paragraphs=[cell] if cell.tag=='{'+NS['w']+'}p' else cell.findall('.//w:p',NS)
            values=[]
            for paragraph in paragraphs:
                fragments=[]
                for node in paragraph.iter():
                    if node.tag=='{'+NS['w']+'}t': fragments.append(node.text or '')
                    elif node.tag in ('{'+NS['w']+'}br','{'+NS['w']+'}tab'): fragments.append(' ')
                values.append(''.join(fragments))
            return ' '.join(values).strip()
        rows = []
        section_lines = []
        for table in tree.findall('.//w:tbl',NS):
            if table.find('.//w:tbl',NS) is not None:
                raise ValueError('Usa tablas simples, sin tablas anidadas.')
            vertical = {}
            for row in table.findall('w:tr', NS):
                cells=[]
                column=0
                for cell in row.findall('w:tc',NS):
                    value=text(cell)
                    merge=cell.find('w:tcPr/w:vMerge',NS)
                    span=cell.find('w:tcPr/w:gridSpan',NS)
                    width=int(span.get('{'+NS['w']+'}val','1')) if span is not None else 1
                    if width<1 or width>20:
                        raise ValueError('Combinación de celdas demasiado compleja.')
                    if merge is not None:
                        if merge.get('{'+NS['w']+'}val')=='restart': vertical[column]=value
                        elif not value: value=vertical.get(column,'')
                    else:
                        vertical.pop(column,None)
                    cells.append(value)
                    column+=width
                rows.append(cells)
                if len(cells)==1:
                    section_lines.extend(text(p) for p in row.findall('w:tc/w:p', NS))
        if len(rows) > 250:
            raise ValueError('El documento tiene demasiadas filas.')
        paragraphs = [text(p) for p in tree.findall('.//w:body/w:p', NS)]
        return {'rows':rows, 'text':'\n'.join(paragraphs)[:20000], 'section_lines':section_lines, 'format':'docx'}


def pdf(data):
    from pypdf import PdfReader, overwrite_configuration
    # pypdf's decompression guards also apply inside this disposable worker.
    overwrite_configuration(disable_legacy_handling=True,maximum_declared_stream_length=MAX_EXPANDED,
        array_based_stream_maximum_output_length=MAX_EXPANDED,array_maximum_element_count=10000,dictionary_maximum_element_count=10000,
        zlib_maximum_output_length=MAX_EXPANDED,lzw_maximum_output_length=MAX_EXPANDED,run_length_maximum_output_length=MAX_EXPANDED,
        jbig2_maximum_output_length=MAX_EXPANDED,brotli_maximum_output_length=MAX_EXPANDED,stream_filters_maximum_length=4,
        stream_decoding_work_maximum_length=MAX_EXPANDED*2,stream_parsing_work_maximum_count=50000)
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted or len(reader.pages) > 6:
        raise ValueError('PDF cifrado o con más de seis páginas.')
    if re.search(br'/(?:JavaScript|JS|Launch|EmbeddedFile|OpenAction|RichMedia)\b', data):
        raise ValueError('No se admite contenido activo en PDF.')
    if any(key in reader.root_object for key in ('/OpenAction','/AA','/AcroForm')):
        raise ValueError('No se admiten acciones o formularios activos en PDF.')
    names=reader.root_object.get('/Names')
    if names and any(key in names.get_object() for key in ('/JavaScript','/EmbeddedFiles')):
        raise ValueError('No se admiten scripts o adjuntos en PDF.')
    texts = []
    for page in reader.pages:
        if '/AA' in page:
            raise ValueError('No se admiten acciones de página.')
        for reference in page.get('/Annots',[]):
            annotation=reference.get_object()
            if '/A' in annotation or '/AA' in annotation or annotation.get('/Subtype')=='/RichMedia':
                raise ValueError('No se admiten anotaciones activas.')
        text = page.extract_text(extraction_mode='layout') or ''
        if len(text) > 30000:
            raise ValueError('El PDF supera el límite de texto.')
        texts.append(text)
    text = '\n'.join(texts)
    if not text.strip():
        raise ValueError('El PDF no contiene texto legible. Envía un DOCX o escribe los artículos y cantidades.')
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in re.split(r'\s{2,}|\t', line.strip()) if c.strip()]
        if len(cells) > 1:
            rows.append(cells)
        else:
            match = re.fullmatch(r'(.+?)\s*:\s*(-?\d+(?:[.,]\d+)?|[-–—])', line.strip())
            if match:
                rows.append([match[1],match[2]])
    return {'rows':rows, 'text':text[:20000], 'format':'pdf'}


def image(data):
    from PIL import Image
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ('JPEG','PNG') or im.width*im.height > 12_000_000 or getattr(im,'n_frames',1) != 1:
                raise ValueError('Imagen demasiado grande, animada o incompatible.')
            kind = im.format
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            im.load()
    return {'rows':[], 'text':'', 'format':'jpg' if kind=='JPEG' else 'png', 'manual':True}


def run(kind, data):
    if not data or len(data) > 5*1024*1024:
        raise ValueError('Archivo vacío o mayor que 5 MB.')
    if kind == 'docx' and data[:2] == b'PK':
        return docx(data)
    if kind == 'pdf' and data.startswith(b'%PDF-'):
        return pdf(data)
    if kind in ('jpg','jpeg','png'):
        return image(data)
    raise ValueError('El contenido real no coincide con el formato permitido.')


if __name__ == '__main__':
    import logging
    logging.disable(logging.CRITICAL)
    try:
        result = run(sys.argv[1], sys.stdin.buffer.read(5*1024*1024+1))
        print(json.dumps({'ok':True, **result},ensure_ascii=True))
    except Exception:
        # Do not expose parser internals, content, paths or credentials.
        print(json.dumps({'ok':False, 'error':'No puedo leer el archivo con seguridad. Usa un DOCX/PDF digital sin protección o indica productos y cantidades por texto.'}))
