"""Form-only absence markers and custom items. Synthetic documents, isolated DB."""
import io
import zipfile
import xml.etree.ElementTree as ET
from unittest.mock import Mock

from django.core import mail
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from incidents.models import Incident, IncidentDraft, NotificationOutbox, RequestDocument, RequestProduct, ProductAlias
from incidents.services.request_documents import analyze_original, DocumentInputError, original_bytes
from incidents.services.request_items import quantity, ItemInputError
from incidents.services.incoming_file_service import IncomingFileService
from incidents.services.incident_notification_service import dispatch
from incidents.test_lot11_requests import Lot11Requests, docx, digital_pdf


def six_item_form(extra='2 lapices y 2 stick fixxs', *, table_section=False):
    left=docx([['Producto','Cantidad'],['Bolsa Basura Grande 90x110cm (10ud)','2'],['Bolsa Basura Mediana 70x90cm (10ud)','2'],['Cloro','0'],['Jabón','X']])
    right=docx([['Producto','Cantidad'],['Guante Talla L (100 ud.)','2'],['PPL Confort (por paquete)','3'],['Alcohol Gel','-'],['CIF','']])
    ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    with zipfile.ZipFile(io.BytesIO(left)) as z:
        entries={n:z.read(n) for n in z.namelist()}
    tree=ET.fromstring(entries['word/document.xml']); body=tree.find(ns+'body')
    with zipfile.ZipFile(io.BytesIO(right)) as z:
        body.append(ET.fromstring(z.read('word/document.xml')).find('.//'+ns+'tbl'))
    section=docx([['Artículos de oficina (lápices, papel, carpetas, etc.)'],[extra]]) if table_section else docx([],['Artículos de oficina (lápices, papel, carpetas, etc.)',extra])
    with zipfile.ZipFile(io.BytesIO(section)) as z:
        for child in ET.fromstring(z.read('word/document.xml')).find(ns+'body'): body.append(child)
    entries['word/document.xml']=ET.tostring(tree)
    result=io.BytesIO()
    with zipfile.ZipFile(result,'w') as z:
        for name,value in entries.items(): z.writestr(name,value)
    return result.getvalue()


@override_settings(AI_PROVIDER='mock',GEMINI_API_KEY='',TELEGRAM_BOT_TOKEN='',EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',EMAIL_NOTIFICATIONS_ENABLED=False,INCIDENT_NOTIFICATION_EMAIL='qa@example.test',DEFAULT_FROM_EMAIL='sender@example.test',APP_BASE_URL='http://localhost:8001',REQUEST_DOCUMENT_VISION_ENABLED=False)
class RequestFormRegressions(TestCase):
    setUpTestData=classmethod(Lot11Requests.setUpTestData.__func__)
    setUp=Lot11Requests.setUp
    send=Lot11Requests.send
    file=Lot11Requests.file

    def test_absence_markers_are_skipped_only_in_forms(self):
        for marker in ('0','0,0','0.000','-','–','—','x','X','','  ','No','N/A','No necesita','Sin solicitar'):
            with self.subTest(marker=marker):
                result=analyze_original(docx([['Cloro',marker],['Jabón','2']]),'form.docx')
                self.assertEqual([(r['original_name'],r['quantity']) for r in result['items']],[('Jabón','2')])
        for amount in ('0','-1'):
            with self.assertRaises(ItemInputError): quantity(amount)

    def test_all_unrequested_cannot_create_an_empty_request(self):
        result,_=self.file(docx([['Cloro','0'],['Jabón','X']]))
        self.assertEqual(result['processing']['action'],'document_rejected')
        self.send('Sí')
        self.assertFalse(Incident.objects.exists()); self.assertFalse(NotificationOutbox.objects.exists())

    def test_two_tables_and_office_text_have_exactly_six_lines(self):
        for table_section in (False,True):
            with self.subTest(table_section=table_section):
                rows=analyze_original(six_item_form(table_section=table_section),'form.docx')['items']
                self.assertEqual([r['quantity'] for r in rows],['2','2','2','3','2','2'])
                self.assertEqual([r['original_name'] for r in rows[-2:]],['lapices','stick fixxs'])
                self.assertTrue(all(r['outside_catalog'] and r['product_id'] is None and not r['unit'] for r in rows[-2:]))
        self.assertEqual(RequestProduct.objects.count(),34)

    def test_other_sections_and_same_line_heading_are_supported(self):
        result=analyze_original(docx([['Cloro','1']],['Otros artículos no incluidos en la lista: 2 extensiones y 3 carpetas']),'extra.docx')
        self.assertEqual([r['quantity'] for r in result['items']],['1','2','3'])

    def test_invalid_quantities_and_partial_free_text_require_clarification(self):
        for amount in ('-2','2 o 3','muchos','1.1234','+','???'):
            with self.subTest(amount=amount),self.assertRaises(DocumentInputError):
                analyze_original(docx([['Cloro','1'],['Jabón',amount]]),'bad.docx')
        for extra in ('2 lapices y stick fixxs','lapices','2 o 3 carpetas'):
            with self.subTest(extra=extra),self.assertRaises(DocumentInputError):
                analyze_original(six_item_form(extra),'bad.docx')
        with self.assertRaises(DocumentInputError):
            analyze_original(docx([['Cloro','2','','Jabón','muchos']]),'bad-second-column.docx')

    def test_duplicate_message_and_invalid_replacement_preserve_valid_draft(self):
        result,_=self.file(six_item_form()); draft=IncidentDraft.objects.get(); before=draft.extracted_data
        message=result['message']; download=Mock()
        duplicate=IncomingFileService(self.service.incident_processor).process(provider='telegram',external_user_id='lot11',external_chat_id='lot11',external_message_id=message.external_message_id,filename='form.docx',media_type=None,declared_size=1,download=download,verified_transport=True)
        self.assertTrue(duplicate['duplicate']); download.assert_not_called()
        bad,_=self.file(six_item_form('2 lapices y pegamento'))
        self.assertEqual(bad['processing']['action'],'document_rejected')
        draft.refresh_from_db(); self.assertEqual(draft.extracted_data,before)
        self.file(six_item_form()); self.assertEqual(RequestDocument.objects.filter(is_current=True).count(),1)
        self.assertEqual(len(IncidentDraft.objects.get().extracted_data['_request_items']),6)

    def test_confirmed_custom_items_reach_dashboard_email_and_original(self):
        self.assertEqual(connection.settings_dict['NAME'],'test_incidentes_gymplanet_db')
        alias_count=ProductAlias.objects.count(); content=six_item_form()
        result,_=self.file(content,caption='No nos quedan artículos. Prioridad media.')
        reply=result['processing']['reply']
        self.assertIn('lapices',reply); self.assertIn('stick fixxs',reply)
        self.assertFalse(Incident.objects.exists()); self.assertFalse(NotificationOutbox.objects.exists()); self.assertEqual(len(mail.outbox),0)
        self.send('Sí'); report=Incident.objects.get()
        self.assertEqual(report.request_items.count(),6)
        self.assertEqual(report.request_items.filter(outside_catalog=True,product__isnull=True).count(),2)
        self.assertEqual(original_bytes(report.request_documents.get()),content)
        self.client.force_login(self.manager)
        page=self.client.get(reverse('report-detail',args=[report.pk]))
        self.assertContains(page,'stick fixxs'); self.assertContains(page,'lapices')
        with override_settings(EMAIL_NOTIFICATIONS_ENABLED=True): self.assertTrue(dispatch(report.creation_notification.pk))
        email=mail.outbox[-1]
        self.assertIn('stick fixxs',email.body); self.assertIn('lapices',email.alternatives[0].content)
        self.assertEqual(next(a.content for a in email.attachments if getattr(a,'filename',None)=='solicitud.docx'),content)
        self.assertEqual((RequestProduct.objects.count(),ProductAlias.objects.count()),(34,alias_count))

    def test_pdf_digital_keeps_positive_items_and_omits_zero(self):
        with self.assertRaises(DocumentInputError): analyze_original(digital_pdf('Cloro: 0'),'zero.pdf')
        self.assertEqual(analyze_original(digital_pdf('Cloro: 2'),'positive.pdf')['items'][0]['quantity'],'2')
