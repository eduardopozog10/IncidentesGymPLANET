"""Reporter-grounded product quantities. AI never supplies quantities or catalogue IDs."""
import re
from decimal import Decimal, InvalidOperation
from incidents.presentation import format_quantity
from django.conf import settings
from django.core.exceptions import ValidationError
from incidents.models import RequestProduct, RequestItem
from incidents.services.gymplanet_conversation import normalize

NUMBERS = {"un":"1", "una":"1", "uno":"1", "dos":"2", "tres":"3", "cuatro":"4", "cinco":"5", "seis":"6", "siete":"7", "ocho":"8", "nueve":"9", "diez":"10"}
NUMBER = r"(?:-?\d+(?:[.,]\d+)?|un[oa]?|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)"
UNITS = {"bidon":"bidón", "bidones":"bidón", "paquetes":"paquete", "paquete":"paquete", "unidad":"unidad", "unidades":"unidad", "litros":"litro", "litro":"litro", "cajas":"caja", "caja":"caja", "resmas":"resma", "resma":"resma"}
GENERIC = {"aseo", "articulos de aseo", "productos de limpieza", "materiales", "implementos", "utiles de oficina", "articulos de oficina", "insumos", "productos", "solicitud", "pedido", "reporte"}


class ItemInputError(ValueError):
    def __init__(self,message,*,pending_name=None):
        super().__init__(message)
        self.pending_name=pending_name


def quantity(value):
    try:
        literal=NUMBERS.get(normalize(value), str(value).replace(",", ".").strip())
        if not re.fullmatch(r'-?\d+(?:\.\d+)?',literal):
            raise InvalidOperation()
        result = Decimal(literal)
    except (InvalidOperation, TypeError):
        raise ItemInputError("Indica una cantidad numérica clara por producto.") from None
    if not result.is_finite() or result <= 0 or result >= Decimal('1000000000') or result.as_tuple().exponent < -3:
        raise ItemInputError("Cada cantidad debe ser positiva, mayor que cero y tener hasta tres decimales.")
    return format(result.normalize(), 'f')


def product_candidates(name):
    key = normalize(name)
    rows = list(RequestProduct.objects.prefetch_related('aliases').all())
    exact = [r for r in rows if key in {normalize(r.name), normalize(r.code), *(a.normalized for a in r.aliases.all())}]
    if exact:
        return exact
    return [r for r in rows if len(key) >= 4 and re.search(r'\b'+re.escape(key)+r'\b', normalize(r.name))]


def make_item(name, amount, unit='', *, from_form=False):
    amount=quantity(amount)
    name = ' '.join(str(name).split()).strip(' .')
    if not name or len(name) > 120 or normalize(name) in GENERIC:
        raise ItemInputError("Indica el nombre concreto de cada artículo, su cantidad y presentación.")
    candidates = product_candidates(name)
    if len(candidates) > 1:
        raise ItemInputError("El producto es ambiguo. Indica el código y presentación: " + '; '.join(f'{r.code}: {r.name}' for r in candidates[:4]))
    row = candidates[0] if candidates else None
    if row and not row.is_active:
        raise ItemInputError(f"{row.name} está inactivo. Consulta al administrador o elige otro artículo.")
    unit = UNITS.get(normalize(unit), str(unit).strip())
    if len(unit) > 40:
        raise ItemInputError("La unidad de compra es demasiado larga.")
    pack=re.search(r'\b(\d+)\s*ud\b',normalize(row.presentation)) if row else None
    if pack and int(pack[1])>1 and not from_form and unit in ('','unidad'):
        raise ItemInputError(f'{row.name} incluye {pack[1]} unidades por presentación. Indica cantidad y unidad de compra; no convertiré unidades sueltas.',pending_name=row.name)
    if row and unit in ('litro','kilogramo') and re.search(r'\d',row.presentation):
        raise ItemInputError(f'La presentación documentada es {row.presentation}. Indica cuántos envases o presentaciones necesitas, sin convertir medidas.',pending_name=row.name)
    if row and row.purchase_unit and unit and row.purchase_unit != unit:
        raise ItemInputError(f"{row.name} se solicita por {row.purchase_unit}. Aclara la cantidad de esa presentación; no convertiré unidades.",pending_name=row.name)
    if row and row.purchase_unit == 'paquete' and not unit and not from_form:
        raise ItemInputError(f"¿Cuántos paquetes de {row.name} necesitas? Aclara unidades o paquetes.",pending_name=row.name)
    return dict(product_id=row.pk if row else None, official_name=row.name if row else '', original_name=name,
                quantity=quantity(amount), unit=unit or (row.purchase_unit if row else ''),
                presentation=row.presentation if row else '', outside_catalog=row is None)


def parse_text_items(text):
    # Split only list separators; decimal commas and product pack sizes remain intact.
    text = re.split(r'\b(?:porque|ya que|debido a|prioridad|no nos quedan|no quedan)\b', text, maxsplit=1, flags=re.I)[0]
    if re.search(r'\b'+NUMBER+r'\s+(?:o|a|hasta|y)\s+'+NUMBER+r'\b|\b\d+\s*[-–]\s*\d+\b',text,re.I):
        raise ItemInputError('La cantidad es ambigua. Indica una sola cantidad exacta por artículo.')
    parts = re.split(r',(?=\s|[^\d])|(?<!\d),|;|\n|\s+y\s+(?='+NUMBER+r'\b)', text, flags=re.I)
    result = []
    for part in parts:
        match = re.search(r'(?:^|\b(?:necesito|necesitamos|solicito|solicitamos|requiero|quiero|quisiera|pedimos|agrega|añade|son|reponer|comprar|faltan|reposici[oó]n de|solicitud de|pedido de))\s*('+NUMBER+r')\s+(.+)', part.strip(), re.I)
        if not match:
            continue
        name = match[2].strip(' .')
        unit = ''
        tokens = name.split()
        if tokens and normalize(tokens[0]) in UNITS:
            unit = UNITS[normalize(tokens.pop(0))]
            name = ' '.join(tokens)
        name = re.sub(r'^(?:de |del )', '', name, flags=re.I)
        name = re.split(r'\.\s+|\s+(?:para|en el area|en la zona)\s+', name, maxsplit=1, flags=re.I)[0]
        result.append(make_item(name, match[1], unit))
    return result


def item_key(item):
    return ('catalog', item['product_id']) if item.get('product_id') else ('free', normalize(item['original_name']), item.get('unit',''))


def display_quantity(item):
    unit=item.get('unit','')
    if Decimal(item['quantity']) != 1 and unit:
        unit={'bidón':'bidones','unidad':'unidades'}.get(unit,unit+'s')
    return f"{format_quantity(item['quantity'])} {unit}".strip()


def merge_items(existing, incoming, *, add=False):
    rows = [dict(row) for row in existing]
    for item in incoming:
        same = [i for i,r in enumerate(rows) if item_key(r) == item_key(item)]
        if same:
            index = same[0]
            if rows[index].get('unit') != item.get('unit'):
                raise ItemInputError("Aclara la presentación antes de combinar cantidades.")
            if add:
                item = dict(item, quantity=quantity(Decimal(rows[index]['quantity'])+Decimal(item['quantity'])))
            elif rows[index]['quantity'] != item['quantity']:
                raise ItemInputError("El artículo aparece con dos cantidades. Indica la cantidad final con «Cambia … de … a …».")
            rows[index] = item
        else:
            rows.append(item)
    if len(rows) > min(settings.REQUEST_MAX_ITEMS, 20):
        raise ItemInputError("El piloto admite hasta 20 artículos por solicitud. Divide la lista antes de confirmar.")
    return rows


def validate_items(items, *, lock=False):
    if not items or len(items) > min(settings.REQUEST_MAX_ITEMS, 20):
        raise ItemInputError("La solicitud necesita al menos un artículo con cantidad válida.")
    keys = set()
    for item in items:
        quantity(item.get('quantity'))
        if not item.get('original_name') or len(item['original_name']) > 120:
            raise ItemInputError("Falta un nombre válido de artículo.")
        if item_key(item) in keys:
            raise ItemInputError("Hay artículos duplicados; revisa sus cantidades.")
        keys.add(item_key(item))
        if item.get('product_id'):
            qs = RequestProduct.objects.select_for_update() if lock else RequestProduct.objects
            product = qs.filter(pk=item['product_id'], is_active=True).first()
            if not product or product.name != item.get('official_name') or product.presentation != item.get('presentation'):
                raise ItemInputError("Cambió el catálogo. Vuelve a indicar el artículo y revisa su presentación antes de confirmar.")
            if product.purchase_unit and item.get('unit') != product.purchase_unit:
                raise ItemInputError("Revisa la unidad de compra del catálogo.")
        elif not item.get('outside_catalog'):
            raise ItemInputError("La referencia de catálogo es inconsistente.")
    return items


def persist_items(report, data):
    for position, item in enumerate(validate_items(data['_request_items'], lock=True), 1):
        RequestItem.objects.create(report=report, position=position, **item)


def product_intent(text, previous=None):
    key = normalize(text)
    if previous and previous.get('_products_required'):
        return True
    if re.search(r'\b(?:servicio|mantencion|mantenimiento|reparacion|revision preventiva|proveedor externo|apoyo de)\b', key) and not re.search(r'\b(?:materiales|insumos|articulos|productos|comprar|compra|reponer)\b', key):
        return False
    return bool(re.search(r'\b(?:aseo|articulos|productos|pedido|oficina|material|materiales|insumos|implementos|reponer|reposicion|compra|comprar|colchonetas|papel higienico)\b', key) or re.search(r'\b'+NUMBER+r'\s+(?!(?:solicitud|incidencia|reporte|pedido)\b)\w+', key))


def change_items(items, text):
    key = normalize(text)
    if re.search(r'\b(?:elimina|quita|borra) todos los articulos\b', key):
        return [], True
    remove = re.match(r'(?:quita|elimina|borra)\s+(?:el |la |los |las )?(.+)', text, re.I)
    correction = re.search(r'(?:me equivoqu[eé](?: en la cantidad)?|la cantidad correcta(?: es)?|corrige la cantidad)\s*[:,]?\s*(?:(?:necesitamos|necesito|eran?|son)\s+)?('+NUMBER+r')\s+(.+?)(?=\s+y\s+(?:la\s+)?prioridad|[,;.!?]|$)',text,re.I)
    if correction:
        target = normalize(correction[2])
        selected = [i for i,r in enumerate(items) if target in normalize(r['original_name']) or target in normalize(r.get('official_name'))]
        if len(selected) != 1:
            raise ItemInputError('Indica el artículo exacto y su cantidad final para evitar cambiar otro producto.')
        result = [dict(row) for row in items]
        result[selected[0]]['quantity'] = quantity(correction[1])
        return result, True
    change = re.match(r'(?:cambia|corrige)\s+(?:el |la |los |las )?(.+?)\s+de\s+('+NUMBER+r')\s+a\s+('+NUMBER+r')(?:\s+(\w+))?[.!]?$', text, re.I)
    short = re.match(r'(?:son|eran?)\s+('+NUMBER+r')\s+(\w+),?\s+no\s+('+NUMBER+r')[.!]?$', text, re.I)
    if remove or change or short:
        target = (remove or change)[1] if not short else None
        selected = [i for i,r in enumerate(items) if target is None or normalize(target) in normalize(r['original_name']) or normalize(target) in normalize(r.get('official_name'))]
        if len(selected) != 1:
            raise ItemInputError("Indica el nombre exacto del artículo que deseas modificar.")
        index = selected[0]
        result = [dict(row) for row in items]
        if remove:
            result.pop(index)
        else:
            old, new, unit = (short[3], short[1], short[2]) if short else (change[2],change[3],change[4])
            if quantity(old) != result[index]['quantity']:
                raise ItemInputError("La cantidad anterior no coincide. Indica el artículo y su cantidad final.")
            if unit and UNITS.get(normalize(unit), unit) != result[index].get('unit'):
                raise ItemInputError("La unidad no coincide; aclara la presentación sin convertir cantidades.")
            result[index]['quantity'] = quantity(new)
        return result, True
    return merge_items(items, parse_text_items(text), add=bool(re.match(r'\s*(?:agrega|añade|suma)', text, re.I))), False
