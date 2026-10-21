from copy import deepcopy
from django.core import mail
from django.test import TestCase, override_settings
from incidents.models import Incident, IncidentDraft, NotificationOutbox
from incidents.services.product_conversation import contextual_stock_reason
from incidents.test_lot11_requests import Lot11Requests


@override_settings(AI_PROVIDER='mock',GEMINI_API_KEY='',TELEGRAM_BOT_TOKEN='',EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',EMAIL_NOTIFICATIONS_ENABLED=False,INCIDENT_NOTIFICATION_EMAIL='qa@example.test',DEFAULT_FROM_EMAIL='sender@example.test',APP_BASE_URL='http://localhost:8001',REQUEST_DOCUMENT_VISION_ENABLED=False)
class RequestStockReasonTests(TestCase):
    setUpTestData=classmethod(Lot11Requests.setUpTestData.__func__)
    setUp=Lot11Requests.setUp
    send=Lot11Requests.send
    file=Lot11Requests.file

    def test_natural_stock_answers_preserve_original_reason_items_and_explicit_priority(self):
        phrases=(
            'Ya no nos queda nada de eso', 'No nos queda de eso', 'Porque estamos sin stock',
            'No tenemos stock', 'Se nos acabaron', 'Ya se acabaron los productos',
            'Nos estamos quedando sin artículos', 'Queda muy poco', 'Se están terminando',
            'Necesitamos reponer', 'Es para reponer el stock', 'Nos faltan estos materiales',
            'Ya no tenemos de esos productos',
            'Ya no nos queda nada de eso. Utilizamos los últimos materiales esta semana y necesitamos abastecer los camarines.',
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.file()
                draft=IncidentDraft.objects.get(status='collecting')
                items=deepcopy(draft.extracted_data['_request_items'])
                result=self.send(phrase)
                self.assertEqual(result['missing_fields'],['priority'])
                self.assertEqual(result['extracted_data']['operational_impact'],phrase)
                self.assertEqual(result['extracted_data']['_request_items'],items)
                self.assertFalse(result['extracted_data'].get('_priority_explicit'))
                self.assertIn('Selecciona la prioridad',result['reply'])
                self.assertFalse(Incident.objects.exists()); self.assertFalse(NotificationOutbox.objects.exists())
                self.assertEqual(len(mail.outbox),0)
                self.send('/cancelar')

    def test_ambiguous_answer_still_requires_justification(self):
        self.file()
        for text in ('eso','no sé','quizás','ok'):
            result=self.send(text)
            self.assertIn('operational_impact',result['missing_fields'])
            self.assertFalse(result['extracted_data'].get('operational_impact'))
        self.assertFalse(Incident.objects.exists()); self.assertFalse(NotificationOutbox.objects.exists())

    def test_each_priority_requires_summary_and_final_confirmation(self):
        for choice,value in (('Baja','low'),('Media','medium'),('Alta','high'),('Crítica','critical')):
            with self.subTest(choice=choice):
                self.file(); self.send('No nos queda de eso')
                result=self.send(choice)
                self.assertEqual(result['extracted_data']['priority'],value)
                self.assertEqual(result['missing_fields'],[])
                self.assertIn('¿Confirmas',result['reply'])
                self.assertIn('Cloro',result['reply'])
                self.assertFalse(Incident.objects.exists()); self.assertFalse(NotificationOutbox.objects.exists())
                self.send('/cancelar')
        self.file(); self.send('Se nos acabaron'); self.send('Baja')
        self.assertFalse(Incident.objects.exists())
        self.send('Sí')
        self.assertEqual(Incident.objects.count(),1); self.assertEqual(NotificationOutbox.objects.count(),1)
        self.assertEqual(len(mail.outbox),0)

    def test_contextual_rule_does_not_apply_without_items_or_when_asking_another_field(self):
        self.file(); draft=IncidentDraft.objects.get()
        data=dict(draft.extracted_data)
        self.assertFalse(contextual_stock_reason('No nos queda de eso',None,data))
        self.assertFalse(contextual_stock_reason('No nos queda de eso',draft,dict(data,_request_items=[])))
        draft.missing_fields=['priority']
        self.assertFalse(contextual_stock_reason('No nos queda de eso',draft,data))

    def test_technical_incident_does_not_gain_stock_impact(self):
        self.send('En Cardio la trotadora no enciende')
        draft=IncidentDraft.objects.get()
        self.assertEqual(draft.extracted_data['report_type'],'incident')
        self.assertFalse(contextual_stock_reason('No nos queda de eso',draft,dict(draft.extracted_data,_request_items=[{}])))
