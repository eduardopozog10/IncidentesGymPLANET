"""Email-only presentation regressions; no database writes or external deliveries."""
from contextlib import nullcontext
from copy import deepcopy
from unittest.mock import Mock, patch

from django.core import mail
from django.test import SimpleTestCase, override_settings

from incidents.services.incident_notification_service import dispatch


@override_settings(
    EMAIL_NOTIFICATIONS_ENABLED=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='sender@example.test', APP_BASE_URL='http://localhost:8000',
)
class RequestEmailPresentationTests(SimpleTestCase):
    def payload(self):
        items = [
            dict(official_name='Amonio Cuaternario 5 LTS', original_name='amonio', quantity='1', presentation='5 LTS', unit='', outside_catalog=False),
            dict(official_name='Limpia Pisos Bidón 5LTS', original_name='limpia pisos', quantity='2', presentation='Bidón 5LTS', unit='bidón', outside_catalog=False),
            dict(official_name='Bolsa Basura Grande 90x110cm (10ud)', original_name='bolsas grandes', quantity='3', presentation='90x110cm (10ud)', unit='paquete', outside_catalog=False),
        ]
        return dict(code='GP-QA', kind='Solicitud operativa', equipment='Aseo y limpieza',
            description='Solicitud de artículos: 1 Amonio Cuaternario 5 LTS; 2 bidón Limpia Pisos; 3 paquete bolsas grandes',
            items=items, documents=[], priority='Media', status='Reportado',
            branch='Sucursal sintética', area='', category='', justification='Reposición', created='21/10/2026')

    def render_email(self, payload):
        before = deepcopy(payload)
        row = Mock(pk=1, report_id=1, status='pending', attempts=0, next_attempt_at=None,
                   recipient='recipient@example.test', payload=payload)
        with patch('incidents.services.incident_notification_service.NotificationOutbox') as model, \
             patch('incidents.services.incident_notification_service.transaction.atomic', return_value=nullcontext()):
            model.objects.select_for_update.return_value.get.return_value = row
            self.assertTrue(dispatch(1))
        self.assertEqual(payload, before)
        return mail.outbox[-1]

    def test_cleaning_request_has_only_structured_product_list_in_both_alternatives(self):
        payload = self.payload()
        message = self.render_email(payload)
        html = message.alternatives[0].content
        self.assertIn('Equipo o requerimiento: Aseo y limpieza', message.body)
        self.assertIn('<strong>Equipo o requerimiento:</strong> Aseo y limpieza</p>', html)
        for content in (html, message.body):
            self.assertNotIn('Solicitud de artículos:', content)
            for item in payload['items']:
                self.assertEqual(content.count(item['official_name']), 1)
                self.assertIn(item['presentation'], content)
            self.assertIn('Reposición', content)
            self.assertIn('Media', content)
            self.assertIn('Ver reporte', content)
        for item in payload['items']:
            self.assertIn(f"<td>{item['quantity']}</td><td>{item['unit']}</td>", html)
            self.assertIn(f"{item['quantity']} {item['unit']} · {item['presentation']}", message.body)
        self.assertIn('<th align="left">Producto</th>', html)

    def test_other_requests_incidents_and_nonduplicated_descriptions_are_preserved(self):
        for changes in (
            dict(kind='Incidencia', equipment='Trotadora', items=[], description='No enciende.'),
            dict(equipment='Servicio de limpieza', items=[], description='Limpieza de camarines.'),
            dict(equipment='Materiales e implementos'),
            dict(description='Reposición para limpieza de camarines.'),
            dict(items=[]),
        ):
            with self.subTest(changes=changes):
                payload = dict(self.payload(), **changes)
                message = self.render_email(payload)
                self.assertIn(payload['description'], message.body)
                self.assertIn(payload['description'], message.alternatives[0].content)
