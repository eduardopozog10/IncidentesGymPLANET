from decimal import Decimal
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from incidents.models import Incident, RequestCategory, RequestProduct
from incidents.presentation import format_quantity
from incidents.services.incident_notification_service import dispatch
from incidents.test_lot11_requests import Lot11Requests


@override_settings(AI_PROVIDER='mock',GEMINI_API_KEY='',TELEGRAM_BOT_TOKEN='',EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',EMAIL_NOTIFICATIONS_ENABLED=False,INCIDENT_NOTIFICATION_EMAIL='qa@example.test',DEFAULT_FROM_EMAIL='sender@example.test',APP_BASE_URL='http://localhost:8001',REQUEST_DOCUMENT_VISION_ENABLED=False)
class RequestDisplayTests(TestCase):
    setUpTestData=classmethod(Lot11Requests.setUpTestData.__func__)
    setUp=Lot11Requests.setUp
    send=Lot11Requests.send
    file=Lot11Requests.file

    def test_decimal_display_without_rounding_or_grouping(self):
        for value,expected in [('1.000','1'),('2.000','2'),('3.000','3'),('5.000','5'),('1.500','1,5'),('2.250','2,25'),('0.125','0,125'),('1000.000','1000')]:
            with self.subTest(value=value):
                self.assertEqual(format_quantity(value),expected)
                self.assertEqual(format_quantity(Decimal(value)),expected)

    def test_quantities_agree_in_summary_email_and_dashboard_without_mutating_payload(self):
        response=self.send('Solicito 2,25 bidones de cloro y 3 lápices. No nos quedan artículos de aseo. Prioridad media.')
        self.assertIn('2,25',response['reply'])
        self.send('Sí'); report=Incident.objects.get(); row=report.creation_notification
        before=row.payload.copy()
        with override_settings(EMAIL_NOTIFICATIONS_ENABLED=True): self.assertTrue(dispatch(row.pk))
        message=mail.outbox[-1]
        for content in (message.body,message.alternatives[0].content):
            self.assertIn('2,25',content); self.assertNotIn('2.250',content)
            self.assertNotIn('Solicitud de artículos:',content)
            self.assertIn('lápices',content)
        row.refresh_from_db(); self.assertEqual(row.payload,before)
        self.client.force_login(self.manager)
        page=self.client.get(reverse('report-detail',args=[report.pk]))
        self.assertContains(page,'2,25'); self.assertContains(page,'<td>3</td>',html=True)
        self.assertContains(page,'<td>—</td>',html=True)
        self.assertContains(page,'bidón')
        self.assertEqual(report.request_items.first().quantity,Decimal('2.250'))

    def test_registered_cleaning_category_is_linked_on_creation(self):
        category=RequestCategory.objects.create(code='ASEO',name='Aseo y limpieza')
        self.send('Solicito 2 bidones de cloro. No nos quedan artículos de aseo. Prioridad media.')
        self.send('Sí')
        self.assertEqual(Incident.objects.get().request_category_id,category.pk)
        self.assertEqual(RequestCategory.objects.count(),1)

    def test_inactive_or_ambiguous_category_is_not_guessed(self):
        RequestCategory.objects.create(code='ASEO',name='Aseo y limpieza',is_active=False)
        self.send('Solicito 2 bidones de cloro. No nos quedan artículos de aseo. Prioridad media.')
        self.send('Sí'); self.assertIsNone(Incident.objects.get().request_category_id)

    def test_historical_category_label_is_honest_and_read_only(self):
        report=Incident.objects.create(branch=self.branch,technician=self.reporter,report_type='request',request_kind='Aseo y limpieza',description='Registro histórico')
        before=Incident.objects.values().get(pk=report.pk)
        self.client.force_login(self.manager)
        page=self.client.get(reverse('report-detail',args=[report.pk]))
        self.assertContains(page,'Aseo y limpieza (tipo identificado; sin categoría vinculada)')
        self.assertEqual(Incident.objects.values().get(pk=report.pk),before)

    def test_unknown_purchase_units_are_not_inferred_from_pack_sizes(self):
        for code in ('GP-ASEO-002','GP-ASEO-014'):
            self.assertEqual(RequestProduct.objects.get(code=code).purchase_unit,'')
