"""Real-message regressions with synthetic Word files and no external transports."""
import io
import tempfile
from unittest.mock import Mock

from django.core import mail
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings

from incidents.ai.mock_provider import MockAIProvider
from incidents.models import (
    Branch, Incident, IncidentDraft, NotificationOutbox, RequestDocument,
    Technician, TechnicianChannel,
)
from incidents.services.gymplanet_conversation import GymPlanetContext
from incidents.services.incident_processor import IncidentProcessor
from incidents.services.incoming_file_service import IncomingFileService
from incidents.services.incoming_message_service import IncomingMessageService
from incidents.test_lot11_requests import docx


ANNOUNCEMENT = (
    'Necesito realizar una solicitud de artículos de aseo para la sucursal Quilín. '
    'Tengo un documento Word con los productos y las cantidades que necesito solicitar.'
)
TEST_REASON = (
    'Esta es una solicitud de prueba para verificar el funcionamiento del sistema '
    'de pedidos de artículos de aseo. No corresponde a una necesidad real ni tiene '
    'impacto operacional en la sucursal.'
)


@override_settings(
    AI_PROVIDER='mock', GEMINI_API_KEY='', TELEGRAM_BOT_TOKEN='',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_NOTIFICATIONS_ENABLED=False, INCIDENT_NOTIFICATION_EMAIL='qa@example.test',
    DEFAULT_FROM_EMAIL='sender@example.test', APP_BASE_URL='http://localhost:8000',
    REQUEST_DOCUMENT_VISION_ENABLED=False,
)
class Lot11RequestRegressions(TestCase):
    @classmethod
    def setUpTestData(cls):
        assert connection.settings_dict['NAME'] == 'test_incidentes_gymplanet_db'
        cls.branch = Branch.objects.create(code='QUILIN', name='Quilín')
        cls.other = Branch.objects.create(code='PENALOLEN', name='Peñalolén')
        cls.reporter = Technician.objects.create(full_name='Cuenta de regresión simulada')
        cls.account = TechnicianChannel.objects.create(
            technician=cls.reporter, branch=cls.branch, provider='telegram',
            external_user_id='regression-user', external_chat_id='regression-chat',
        )
        call_command('seed_request_products', apply=True, stdout=io.StringIO())

    def setUp(self):
        self.counter = 0
        provider = Mock(wraps=MockAIProvider())
        self.service = IncomingMessageService(incident_processor=IncidentProcessor(ai_provider=provider))
        self.provider = provider
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        override = override_settings(REQUEST_PRIVATE_ROOT=folder.name)
        override.enable()
        self.addCleanup(override.disable)

    def send(self, text):
        self.counter += 1
        return self.service.process_text(
            provider='telegram', external_user_id='regression-user',
            external_chat_id='regression-chat', external_message_id=f'text-{self.counter}',
            text=text, raw_payload={}, verified_transport=True,
        )['processing']

    def document(self, *, paragraphs=(), caption=''):
        self.counter += 1
        content = docx([['Producto', 'Cantidad'], ['Cloro Bidón 5LTS', '2']], paragraphs)
        return IncomingFileService(self.service.incident_processor).process(
            provider='telegram', external_user_id='regression-user',
            external_chat_id='regression-chat', external_message_id=f'file-{self.counter}',
            filename='solicitud_sintetica.docx', media_type=None, declared_size=len(content),
            download=Mock(return_value=content), caption=caption, verified_transport=True,
        )['processing']

    def assert_nothing_created(self):
        self.assertFalse(Incident.objects.exists())
        self.assertFalse(NotificationOutbox.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_generic_branch_references_do_not_claim_another_identity(self):
        for text in (
            TEST_REASON, 'No tiene impacto en la sucursal.',
            'Esta sucursal no necesita una reposición real.',
            'La sucursal tiene stock; esta es una prueba.',
            'Solo es una prueba en la sede.', 'La sede no tiene impacto operacional.',
        ):
            with self.subTest(text=text):
                context = GymPlanetContext(text, branch=self.branch)
                self.assertIsNone(context.branch_issue)
                self.assertEqual(context.branch, self.branch)
                self.assertEqual(context.parse_text, text)

    def test_explicit_authorized_branch_continues(self):
        result = self.send('Solicito artículos de aseo para la sucursal Quilín.')
        self.assertEqual(result['extracted_data']['branch_id'], self.branch.pk)
        self.assertEqual(result['extracted_data']['report_type'], 'request')
        self.assertIn('request_items', result['missing_fields'])
        self.assert_nothing_created()

    def test_foreign_branch_still_blocks_and_generic_reference_cannot_clear_conflict(self):
        self.document()
        result = self.send('La solicitud corresponde a la sede Peñalolén.')
        self.assertEqual(result['action'], 'branch_mismatch')
        self.assertEqual(self.send(TEST_REASON)['action'], 'branch_mismatch')
        self.assertFalse(self.send('Sí')['incident_created'])
        self.assertEqual(IncidentDraft.objects.get().extracted_data['branch_id'], self.branch.pk)
        self.assert_nothing_created()

    def test_unknown_explicit_branch_is_still_rejected(self):
        for text in ('Sucursal INEXISTENTE;', 'Cambiar a sucursal desconocida', 'Sede INEXISTENTE.'):
            with self.subTest(text=text):
                self.assertIsNotNone(GymPlanetContext(text, branch=self.branch).branch_issue)

    def test_cleaning_request_announcing_word_asks_for_file_or_items_before_priority(self):
        result = self.send(ANNOUNCEMENT)
        self.assertEqual(result['extracted_data']['report_type'], 'request')
        self.assertIn('request_items', result['missing_fields'])
        self.assertIn('solicitud', result['reply'].lower())
        self.assertIn('word', result['reply'].lower())
        self.assertNotIn('Baja', result['reply'])
        self.provider.extract_incident.assert_not_called()
        self.send('Sí')
        self.assert_nothing_created()

    def test_word_then_real_reason_then_priority_requires_final_confirmation(self):
        self.send(ANNOUNCEMENT)
        result = self.document(paragraphs=['Sucursal: Quilín'])
        self.assertIn('operational_impact', result['missing_fields'])
        self.assert_nothing_created()
        result = self.send(TEST_REASON)
        self.assertEqual(result['missing_fields'], ['priority'])
        self.assertEqual(result['extracted_data']['branch_id'], self.branch.pk)
        self.assertEqual(result['extracted_data']['_request_items'][0]['quantity'], '2')
        self.assertIn('no corresponde a una necesidad real', result['extracted_data']['operational_impact'].lower())
        result = self.send('Prioridad baja')
        self.assertEqual(result['missing_fields'], [])
        self.assertIn('¿Confirmas', result['reply'])
        self.assert_nothing_created()
        self.send('Sí')
        report = Incident.objects.get()
        self.assertEqual((report.branch_id, report.report_type), (self.branch.pk, 'request'))
        self.assertEqual(report.request_items.get().quantity, 2)
        self.assertEqual(report.request_documents.count(), 1)
        self.assertEqual(NotificationOutbox.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)
        self.send('Sí')
        self.assertEqual(Incident.objects.count(), 1)

    def test_generic_reference_in_word_caption_is_not_a_branch_claim(self):
        result = self.document(caption='Formulario para la sucursal. No nos quedan artículos de aseo.')
        self.assertNotEqual(result['action'], 'branch_mismatch')
        self.assertEqual(result['extracted_data']['branch_id'], self.branch.pk)
        self.assert_nothing_created()

    def test_generic_word_metadata_does_not_supply_identity(self):
        result = self.document(paragraphs=['Sucursal: esta sucursal'])
        self.assertNotEqual(result['action'], 'branch_mismatch')
        self.assertEqual(RequestDocument.objects.get().branch_id, self.branch.pk)
        self.assert_nothing_created()

    def test_foreign_word_metadata_is_rejected(self):
        result = self.document(paragraphs=['Sucursal: Peñalolén'])
        self.assertEqual(result['action'], 'branch_mismatch')
        self.assertFalse(RequestDocument.objects.exists())
        self.assert_nothing_created()

    def test_account_reassociation_does_not_move_draft_or_original(self):
        self.document()
        self.account.branch = self.other
        self.account.save()
        self.assertEqual(self.send(TEST_REASON)['action'], 'branch_mismatch')
        self.assertEqual(IncidentDraft.objects.get().extracted_data['branch_id'], self.branch.pk)
        self.assertEqual(RequestDocument.objects.get().branch_id, self.branch.pk)
        self.assert_nothing_created()

    def test_technical_incident_stays_separate_from_requests(self):
        result = self.send('La trotadora de Cardio no enciende y quedó fuera de servicio.')
        self.assertEqual(result['extracted_data']['report_type'], 'incident')
        self.assertNotIn('request_items', result['missing_fields'])
        self.assert_nothing_created()

    def test_word_announcement_does_not_merge_with_pending_technical_incident(self):
        self.send('La trotadora de Cardio no enciende y quedó fuera de servicio.')
        before = IncidentDraft.objects.get().extracted_data
        result = self.send(ANNOUNCEMENT)
        self.assertEqual(result['action'], 'pending_report')
        self.assertIn('/cancelar', result['reply'])
        self.assertEqual(IncidentDraft.objects.get().extracted_data, before)
        self.assert_nothing_created()
