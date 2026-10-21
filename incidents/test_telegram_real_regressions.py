"""Nivel C: regresiones con las respuestas de Gemini observadas en la ronda A."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import TestCase, override_settings
from django.db import connection

from incidents.ai.gemini_provider import GeminiAIProvider
from incidents.models import Branch, BranchArea, Technician, TechnicianChannel, Incident, IncidentDraft
from incidents.services.incoming_message_service import IncomingMessageService
from incidents.services.incident_processor import IncidentProcessor
from incidents.services.gym_language import priority_choice


@override_settings(AI_PROVIDER="mock", GEMINI_API_KEY="", TELEGRAM_BOT_TOKEN="",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", EMAIL_NOTIFICATIONS_ENABLED=False)
class RecordedTelegramRegressions(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(code="QUILIN", name="Quilín")
        cls.other = Branch.objects.create(code="PENALOLEN", name="Peñalolén")
        for code, name in (("CARDIO","Cardio"),("MUSCULACION","Musculación"),("CAMARINES","Camarines"),("RECEPCION","Recepción"),("CLASES","Clases grupales")):
            BranchArea.objects.create(branch=cls.branch,code=code,name=name)
        cls.reporter = Technician.objects.create(full_name="Cuenta QA simulada")
        cls.account = TechnicianChannel.objects.create(technician=cls.reporter,branch=cls.branch,provider="telegram",external_user_id="qa-recorded",external_chat_id="qa-recorded")
        root = Path(__file__).resolve().parents[1]
        cls.recordings = json.loads((root/"incidents"/"fixtures"/"telegram_initial_extractions.json").read_text(encoding="utf-8"))
        cls.cases = json.loads((root/"incidents"/"fixtures"/"telegram_initial_extractions.json").read_text(encoding="utf-8"))["cases"]

    def setUp(self):
        self.assertEqual(connection.settings_dict["NAME"],"test_incidentes_gymplanet_db")
        self.counter = 0

    def configure(self, number, followups=()):
        case = self.cases[number-1]
        initial = next(e for e in self.recordings["exchanges"] if e["case_id"]==case["id"] and e["input"]==case["initial_message"])
        raw = initial["gemini_calls"][0]["output"]
        payloads = [raw] + [json.dumps(p,ensure_ascii=False) for p in followups]
        provider = GeminiAIProvider.__new__(GeminiAIProvider)
        provider.model = "recorded-no-network"
        responses = iter(payloads)
        provider._create_interaction_with_retry = Mock(side_effect=lambda **kwargs: SimpleNamespace(output_text=next(responses,payloads[-1])))
        self.provider = provider
        self.service = IncomingMessageService(incident_processor=IncidentProcessor(ai_provider=provider))
        return case

    def send(self, text):
        self.counter += 1
        return self.service.process_text(provider="telegram",external_user_id="qa-recorded",external_chat_id="qa-recorded",external_message_id=str(self.counter),text=text,raw_payload={},verified_transport=True)["processing"]

    def test_explicit_removal_from_use_survives_short_ai_impact(self):
        case = self.configure(1)
        result = self.send(case["initial_message"])
        self.assertIn("utilizarla",result["extracted_data"]["operational_impact"].lower())
        self.assertIn("caída",result["extracted_data"]["operational_impact"].lower())

    def test_already_retained_passive_closure_does_not_repeat_the_impact(self):
        from incidents.services.conversation_evidence import retain_operational_state
        data = {"description": "Fuga en la ducha.", "operational_impact": "Se cerró la ducha para evitar caídas."}
        retain_operational_state(data, "El piso está mojado y cerramos esa ducha para evitar caídas.")
        self.assertEqual(data["operational_impact"], "Se cerró la ducha para evitar caídas.")

    def test_generic_request_label_requires_actual_materials_or_kind(self):
        case = self.configure(24, self.request_followups(
            "7 bloques de yoga", "Clases grupales", "Hay bloques rotos y no alcanzan para completar la clase.", kind="solicitud"))
        first = self.send(case["initial_message"])
        self.assertIn("request_kind", first["missing_fields"])
        result = self.send(case["planned_complements"][0])
        self.assertEqual(result["extracted_data"]["request_kind"], "Materiales e implementos")
        self.assertIn("operational_impact", result["missing_fields"])
        self.assertFalse(Incident.objects.exists())

    def test_initial_messages_do_not_claim_corrections(self):
        for number in (2,5,11,12,13,14,15):
            with self.subTest(case=number):
                case = self.configure(number)
                try:
                    result = self.send(case["initial_message"])
                    self.assertNotIn("Entendido, actualicé",result["reply"])
                finally:
                    self.send("/cancelar")

    def test_generic_machine_requires_identification_and_early_yes_cannot_create(self):
        case = self.configure(7)
        result = self.send(case["initial_message"])
        self.assertIn("equipment",result["missing_fields"])
        self.send("Sí")
        self.assertFalse(Incident.objects.exists())

    def test_model_cannot_infer_cardio_from_treadmill(self):
        case = self.configure(8)
        first = self.send(case["initial_message"])
        self.assertIn("area",first["missing_fields"])
        self.assertNotIn("equipment",first["missing_fields"])
        second = self.send("Cardio")
        self.assertEqual(second["missing_fields"],[])
        self.assertEqual(second["extracted_data"]["priority"],"low")
        self.assertIn("trotadora",second["extracted_data"]["equipment"].lower())
        self.assertNotIn("selecciones una prioridad",second["reply"])
        self.assertEqual(self.provider._create_interaction_with_retry.call_count,1)

    def test_area_names_take_precedence_over_common_code_in_justification(self):
        case = self.configure(20)
        result = self.send(case["initial_message"])
        self.assertNotIn("catalog",result["missing_fields"])
        self.assertEqual(result["missing_fields"],[])
        self.assertEqual(result["extracted_data"]["branch_area_id"],BranchArea.objects.get(code="MUSCULACION").pk)

    def test_two_named_areas_still_require_clarification(self):
        case = self.configure(20)
        result = self.send(case["initial_message"]+" El área podría ser Cardio o Musculación.")
        self.assertIn("catalog",result["missing_fields"])

    def test_quantity_correction_with_necesitamos_is_not_a_new_request(self):
        case = self.configure(21)
        self.send(case["initial_message"])
        result = self.send(case["planned_complements"][0])
        self.assertNotEqual(result["action"],"pending_report")
        self.assertIn("10 colchonetas",result["extracted_data"]["description"])
        self.assertNotIn("6 colchonetas",result["extracted_data"]["description"])
        self.assertIn("cantidad",result["reply"])
        self.assertFalse(Incident.objects.exists())
        self.send("Sí")
        self.assertEqual(Incident.objects.count(),1)
        self.assertIn("10 colchonetas",Incident.objects.get().description)

    def test_compound_quantity_and_priority_correction_is_atomic(self):
        case = self.configure(29)
        self.send(case["initial_message"])
        self.send("No")
        result = self.send(case["planned_complements"][1])
        self.assertIn("5 balones medicinales",result["extracted_data"]["description"])
        self.assertNotIn("4 balones",result["extracted_data"]["description"])
        self.assertEqual(result["extracted_data"]["priority"],"high")
        self.assertFalse(Incident.objects.exists())
        self.assertIn("¿Confirmas",result["reply"])

    def request_followups(self, material, area, reason, kind="Materiales e implementos"):
        return [
            dict(intent="request_report",report_type="request",request_kind=kind,area=area,location_context=area,description=material,operational_impact=None,priority=None),
            dict(intent="request_report",report_type="request",request_kind=kind,area=area,location_context=area,description=material,operational_impact=reason,priority=None),
        ]

    def test_materials_are_not_stored_as_request_justification(self):
        case = self.configure(22,self.request_followups("2 bidones de detergente para Camarines","Camarines","Se acabó el stock y no podemos completar la limpieza diaria.","Aseo y limpieza"))
        self.send(case["initial_message"])
        result = self.send(case["planned_complements"][0])
        self.assertIn("operational_impact",result["missing_fields"])
        self.assertIn("2 bidones",result["extracted_data"]["description"])
        self.assertFalse(result["extracted_data"].get("operational_impact"))
        result = self.send(case["planned_complements"][1])
        result = self.send(case["planned_complements"][2])
        self.assertIn("stock",result["extracted_data"]["operational_impact"])
        self.assertIn("2 bidones",result["extracted_data"]["description"])
        self.assertEqual(result["missing_fields"],[])

    def test_partial_request_collects_all_facts_without_new_report_guard(self):
        case = self.configure(24,self.request_followups("7 bloques de yoga","Clases grupales","Hay bloques rotos y no alcanzan para completar la clase."))
        self.send(case["initial_message"])
        result = self.send(case["planned_complements"][0])
        self.assertNotEqual(result["action"],"pending_report")
        self.assertIn("7 bloques",result["extracted_data"]["description"])
        result = self.send(case["planned_complements"][1])
        result = self.send(case["planned_complements"][2])
        self.assertEqual(result["missing_fields"],[])
        self.assertIn("rotos",result["extracted_data"]["operational_impact"])
        self.assertEqual(result["extracted_data"]["report_type"],"request")

    def test_branch_conflict_recovery_does_not_reask_known_priority(self):
        case = self.configure(25)
        self.send(case["initial_message"])
        self.assertEqual(self.send(case["planned_complements"][0])["action"],"branch_mismatch")
        result = self.send(case["planned_complements"][1])
        self.assertEqual(result["missing_fields"],[])
        self.assertEqual(result["extracted_data"]["priority"],"medium")
        self.assertIn("¿Confirmas",result["reply"])
        self.assertNotIn("selecciones una prioridad",result["reply"])
        self.assertEqual(result["extracted_data"]["branch_id"],self.branch.pk)

    def test_correcta_priority_with_negated_previous_value(self):
        case = self.configure(26)
        self.send(case["initial_message"])
        self.send(case["planned_complements"][0])
        result = self.send(case["planned_complements"][1])
        self.assertEqual(result["extracted_data"]["priority"],"high")
        self.assertNotIn("priority",result["missing_fields"])
        self.assertIn("bicicleta",result["extracted_data"]["equipment"])

    def test_signed_priority_still_rejects_uncertainty_and_alternatives(self):
        self.assertEqual(priority_choice("La prioridad correcta es alta, no baja."),"high")
        for text in ("Quizás la prioridad correcta es alta","Prioridad alta o crítica","No quiero prioridad alta"):
            self.assertIsNone(priority_choice(text))

    def test_explicit_other_report_remains_blocked(self):
        case = self.configure(24)
        self.send(case["initial_message"])
        result = self.send("Quiero registrar otra incidencia en una trotadora.")
        self.assertEqual(result["action"],"pending_report")
        self.assertEqual(IncidentDraft.objects.filter(status__in=["collecting","ready"]).count(),1)

    def test_complete_initial_scenarios_keep_their_required_facts(self):
        # Complementary Nivel C replay only; these successful cases are not resent to Telegram.
        for number in (3,4,6,9,10,16,17,18,19,23,27,30):
            with self.subTest(case=number):
                case = self.configure(number)
                try:
                    result = self.send(case["initial_message"])
                    self.assertEqual(result["missing_fields"],[])
                    self.assertEqual(result["extracted_data"]["priority"],case["expected"]["priority"])
                    self.assertEqual(result["extracted_data"]["report_type"],case["report_type"])
                finally:
                    self.send("/cancelar")

    def test_request_impact_question_never_asks_machine_state(self):
        from incidents.services.gymplanet_conversation import GymPlanetContext
        context = GymPlanetContext("Solicito materiales",branch=self.branch)
        question = context.question(["operational_impact"],IncidentProcessor.FALLBACK_QUESTIONS["operational_impact"])
        self.assertIn("solicit",question.lower())
        self.assertNotIn("fuera de servicio",question)

    def test_empty_telegram_updates_never_reach_the_processor(self):
        from incidents.messaging.telegram_update_handler import TelegramUpdateHandler
        incoming, transport = Mock(), Mock()
        handler = TelegramUpdateHandler(incoming_service=incoming, messaging_provider=transport)
        for text in (None, "", "   "):
            with self.subTest(text=text):
                result = handler.handle({"message": {"from": {"id": "qa-recorded"}, "chat": {"id": "qa-recorded", "type": "private"}, "message_id": 1, "text": text}})
                self.assertEqual(result["action"], "unsupported_message")
        incoming.process_text.assert_not_called()
        self.assertFalse(IncidentDraft.objects.exists())
        self.assertFalse(Incident.objects.exists())

    def test_long_unicode_message_keeps_original_facts_through_confirmation(self):
        case = self.configure(23)
        description = ("Observación literal: ñ, tildes, «sin encender», ⚠️. " * 35).strip()
        payload = json.loads(next(e for e in self.recordings["exchanges"] if e["case_id"] == case["id"] and e["gemini_calls"])["gemini_calls"][0]["output"])
        payload["description"] = description
        self.provider._create_interaction_with_retry.side_effect = None
        self.provider._create_interaction_with_retry.return_value = SimpleNamespace(output_text=json.dumps(payload, ensure_ascii=False))
        message = case["initial_message"] + " " + description
        result = self.send(message)
        self.assertEqual(result["missing_fields"], [])
        self.assertFalse(Incident.objects.exists())
        self.send("Sí")
        report = Incident.objects.get()
        self.assertIn(message, report.original_message)
        self.assertEqual(report.description, description)

