import json
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import TestCase, override_settings

from incidents.ai.gemini_provider import GeminiAIProvider
from incidents.ai.mock_provider import MockAIProvider
from incidents.ai.schemas import IncidentExtractionResult
from incidents.messaging.telegram_update_handler import TelegramUpdateHandler
from incidents.models import Branch, BranchArea, ChannelMessage, EquipmentCategory, GymAsset, Incident, IncidentDraft, RequestCategory, Technician, TechnicianChannel
from incidents.services.gymplanet_conversation import GymPlanetContext
from incidents.services.incoming_message_service import IncomingMessageService, ChannelNotAuthorizedError
from incidents.services.incident_processor import IncidentProcessor


@override_settings(AI_PROVIDER="mock", TELEGRAM_BOT_TOKEN="", GEMINI_API_KEY="")
class GymPlanetConversationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(name="Quilín", code="QUILIN")
        cls.other_branch = Branch.objects.create(name="Peñalolén", code="PENALOLEN")
        cls.reporter = Technician.objects.create(full_name="Reportante de prueba")
        TechnicianChannel.objects.create(branch=cls.branch, technician=cls.reporter, provider="telegram", external_user_id="test-user", external_chat_id="test-chat")

    def setUp(self):
        self.service = IncomingMessageService()
        self.number = 0

    def send(self, text, message_id=None):
        self.number += 1
        return self.service.process_text(verified_transport=True, provider="telegram", external_user_id="test-user", external_chat_id="test-chat", external_message_id=message_id or f"test:{self.number}", text=text, raw_payload={})

    def complete_incident(self, branch="QUILIN", priority="alta"):
        return self.send(f"Sucursal {branch}; área Cardio, la trotadora 1 no enciende, impide entrenar, prioridad {priority}")["processing"]

    def test_complete_incident_requires_confirmation_and_saves_branch(self):
        result = self.complete_incident()
        self.assertEqual(result["missing_fields"], [])
        self.assertIn("Quilín", result["reply"])
        self.assertIn("Incidencia", result["reply"])
        self.assertFalse(Incident.objects.exists())
        result = self.send("Sí")["processing"]
        self.assertTrue(result["incident_created"])
        incident = Incident.objects.get()
        self.assertEqual(incident.branch, self.branch)
        self.assertEqual(incident.priority, "high")
        self.assertIsNone(incident.gym_asset_id)
        self.assertIn("Sucursal QUILIN", incident.original_message)

    def test_branch_resolution_ignores_case_and_accents(self):
        result = self.complete_incident(branch="qUiLiN")
        self.assertEqual(result["extracted_data"]["branch_id"], self.branch.pk)
        self.assertIn("Quilín", result["reply"])

    def test_account_supplies_branch_without_losing_report(self):
        result = self.send("Área Cardio, trotadora 1 no enciende, impide entrenar, prioridad alta")["processing"]
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(result["extracted_data"]["branch_id"], self.branch.pk)
        self.assertTrue(self.send("Sí")["processing"]["incident_created"])

    def test_single_branch_uses_authorized_account(self):
        self.other_branch.is_active = False
        self.other_branch.save()
        result = self.send("Área Cardio, trotadora 1 no enciende, impide entrenar, prioridad alta")["processing"]
        self.assertEqual(result["extracted_data"]["branch_id"], self.branch.pk)

    def test_ambiguous_branch_text_never_overrides_account(self):
        Branch.objects.create(name="Centro", code="CENTRO-A")
        Branch.objects.create(name="Centro", code="CENTRO-B")
        result = self.complete_incident(branch="Centro")
        self.assertEqual(result["action"], "branch_mismatch")
        self.assertFalse(Incident.objects.exists())

    def test_multiple_branch_mentions_do_not_choose_one(self):
        context = GymPlanetContext("Ocurre en Quilín o Peñalolén", branch=self.branch)
        self.assertEqual(context.branch, self.branch)
        self.assertIsNotNone(context.branch_issue)

    def test_unknown_branch_is_not_created(self):
        result = self.complete_incident(branch="INEXISTENTE")
        self.assertEqual(result["action"], "branch_mismatch")
        self.assertEqual(Branch.objects.count(), 2)
        self.assertFalse(Incident.objects.exists())

    def test_inactive_branch_is_rejected(self):
        self.branch.is_active = False
        self.branch.save()
        with self.assertRaises(ChannelNotAuthorizedError):
            self.complete_incident()
        self.assertFalse(Incident.objects.exists())

    def test_no_active_branches_prevents_authorization(self):
        Branch.objects.update(is_active=False)
        with self.assertRaises(ChannelNotAuthorizedError):
            self.complete_incident()
        self.assertFalse(Incident.objects.exists())
        self.assertFalse(IncidentDraft.objects.exists())

    def test_ai_branch_guess_without_message_evidence_is_not_trusted(self):
        provider = Mock()
        provider.extract_incident.return_value = IncidentExtractionResult(branch="QUILIN", area="Cardio", location_context="Cardio", equipment="Trotadora 1", failure_type="No enciende", description="La trotadora no enciende", operational_impact="Impide entrenar", priority="high")
        self.service = IncomingMessageService(incident_processor=IncidentProcessor(ai_provider=provider))
        result = self.send("La trotadora no enciende")["processing"]
        self.assertEqual(result["extracted_data"]["branch_id"], self.branch.pk)
        self.assertIn("priority", result["missing_fields"])

    def test_foreign_branch_correction_requires_authorized_resolution(self):
        self.complete_incident()
        result = self.send("Sucursal PENALOLEN;")["processing"]
        self.assertEqual(result["action"], "branch_mismatch")
        self.assertFalse(self.send("Sí")["processing"]["incident_created"])
        self.send("Sucursal QUILIN;")
        self.assertTrue(self.send("Sí")["processing"]["incident_created"])
        self.assertEqual(Incident.objects.get().branch, self.branch)

    def test_catalog_changes_after_summary_prevent_creation(self):
        self.complete_incident()
        Branch.objects.filter(pk=self.branch.pk).update(is_active=False)
        with self.assertRaises(ChannelNotAuthorizedError):
            self.send("Sí")
        self.assertFalse(Incident.objects.exists())

    def test_priority_is_never_assigned_from_urgency_or_risk(self):
        result = self.send("Sucursal QUILIN; área Cardio, trotadora 1 no enciende, riesgo de lesiones, es muy urgente")["processing"]
        self.assertIn("priority", result["missing_fields"])
        self.assertIsNone(result["extracted_data"]["priority"])
        result = self.send("Alta")["processing"]
        self.assertEqual(result["extracted_data"]["priority"], "high")
        self.assertFalse(Incident.objects.exists())

    def test_ambiguous_priority_is_not_accepted(self):
        result = self.complete_incident(priority="alta o baja")
        self.assertIn("priority", result["missing_fields"])

    def test_priority_correction_is_explicit(self):
        self.complete_incident()
        self.send("No")
        result = self.send("La prioridad es baja")["processing"]
        self.assertEqual(result["extracted_data"]["priority"], "low")
        self.assertFalse(Incident.objects.exists())
        self.send("Sí")
        self.assertEqual(Incident.objects.get().priority, "low")

    def test_duplicate_report_and_confirmation_are_deduplicated(self):
        self.complete_incident()
        self.send("Sí", "confirmation")
        duplicate = self.send("Sí", "confirmation")
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(Incident.objects.count(), 1)

    def inventory(self, *, branch=None, code="TR-01", name="Trotadora 1", area=None):
        category, _ = EquipmentCategory.objects.get_or_create(code="TEST-CARDIO", defaults={"name": "Cardio de prueba"})
        return GymAsset.objects.create(branch=branch or self.branch, branch_area=area, category=category, code=code, name=name)

    def test_registered_area_and_asset_are_proposed_then_associated(self):
        area = BranchArea.objects.create(branch=self.branch, name="Cardio", code="CARDIO")
        asset = self.inventory(area=area)
        result = self.complete_incident()
        self.assertEqual(result["extracted_data"]["gym_asset_id"], asset.pk)
        self.assertEqual(result["extracted_data"]["branch_area_id"], area.pk)
        self.send("Sí")
        incident = Incident.objects.get()
        self.assertEqual(incident.gym_asset, asset)
        self.assertEqual(incident.branch_area, area)

    def test_foreign_asset_code_is_not_associated(self):
        self.inventory(branch=self.other_branch, code="FOREIGN")
        result = self.send("Sucursal QUILIN; área Cardio, equipo FOREIGN no enciende, impide entrenar, prioridad alta")["processing"]
        self.assertIsNone(result["extracted_data"]["gym_asset_id"])
        self.assertFalse(result["incident_created"])

    def test_ambiguous_asset_names_keep_text_without_forced_link(self):
        self.inventory(code="TR-01")
        chosen = self.inventory(code="TR-02")
        result = self.complete_incident()
        self.assertNotIn("catalog", result["missing_fields"])
        result = self.send("TR-02")["processing"]
        self.assertEqual(result["extracted_data"]["gym_asset_id"], chosen.pk)
        self.assertFalse(Incident.objects.exists())

    def test_asset_with_different_area_is_not_linked(self):
        BranchArea.objects.create(branch=self.branch, name="Cardio", code="CARDIO")
        area = BranchArea.objects.create(branch=self.branch, name="Musculación", code="MUSC")
        self.inventory(area=area)
        result = self.complete_incident()
        self.assertIn("catalog", result["missing_fields"])
        self.assertIsNone(result["extracted_data"]["gym_asset_id"])

    def test_text_report_does_not_create_inventory(self):
        self.complete_incident()
        self.send("Sí")
        self.assertEqual(GymAsset.objects.count(), 0)
        self.assertEqual(Incident.objects.get().equipment, "Trotadora 1")

    def test_operational_request_needs_no_machine_or_failure(self):
        result = self.send("Sucursal QUILIN; Solicito reponer mancuernas, impide entrenar, prioridad media")["processing"]
        self.assertEqual(result["missing_fields"], [])
        self.assertIn("Solicitud operativa", result["reply"])
        self.assertFalse(Incident.objects.exists())
        self.send("Sí")
        report = Incident.objects.get()
        self.assertEqual(report.report_type, "request")
        self.assertEqual(report.request_kind, "Reposición")
        self.assertEqual(report.failure_type, "")
        self.assertEqual(report.equipment, "")
        self.assertIsNone(report.gym_asset_id)
        report.full_clean()

    def test_request_category_uses_registered_catalog(self):
        category = RequestCategory.objects.create(code="REPOSICION", name="Reposición de prueba")
        result = self.send("Sucursal QUILIN; Solicito reponer mancuernas, impide entrenar, prioridad media")["processing"]
        self.assertEqual(result["extracted_data"]["request_category_id"], category.pk)
        self.send("Sí")
        self.assertEqual(Incident.objects.get().request_category, category)

    def test_catalog_category_can_supply_request_kind_without_technical_fields(self):
        category = RequestCategory.objects.create(code="OTROS", name="Otros requerimientos")
        result = self.send("Sucursal QUILIN; Solicito gestión OTROS, no podemos limpiar, prioridad media")["processing"]
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(result["extracted_data"]["request_kind"], category.name)
        self.send("Sí")
        self.assertEqual(Incident.objects.get().request_category, category)

    def test_provider_can_propose_request_type_but_not_branch_or_priority(self):
        provider = Mock()
        provider.extract_incident.return_value = IncidentExtractionResult(
            report_type="request", request_kind="Reposición", description="Renovar material",
            operational_impact="Impide entrenar", priority="critical",
        )
        self.service = IncomingMessageService(incident_processor=IncidentProcessor(ai_provider=provider))
        result = self.send("Sucursal QUILIN; Hace falta renovar el material del salón, prioridad media")["processing"]
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(result["extracted_data"]["priority"], "medium")
        self.assertEqual(result["extracted_data"]["report_type"], "request")
        self.assertEqual(provider.extract_incident.call_count, 1)

    def test_existing_nontelegram_channel_uses_the_same_validated_flow(self):
        TechnicianChannel.objects.create(branch=self.branch, technician=self.reporter, provider="whatsapp", external_user_id="test-phone", external_chat_id="test-phone")
        result = self.service.process_text(
            verified_transport=True,
            provider="whatsapp", external_user_id="test-phone", external_chat_id="test-phone",
            external_message_id="whatsapp:1", text="Sucursal QUILIN; área Cardio, trotadora 1 no enciende, impide entrenar, prioridad alta", raw_payload={},
        )["processing"]
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(result["extracted_data"]["branch_id"], self.branch.pk)

    def test_request_gathers_justification_and_priority_progressively(self):
        result = self.send("Sucursal QUILIN; Solicito implementos de aseo")["processing"]
        self.assertIn("operational_impact", result["missing_fields"])
        result = self.send("No podemos limpiar los camarines")["processing"]
        self.assertEqual(result["missing_fields"], ["priority"])
        result = self.send("Media")["processing"]
        self.assertEqual(result["missing_fields"], [])
        self.send("Sí")
        self.assertIn("limpiar", Incident.objects.get().operational_impact)

    def test_request_correction_preserves_other_fields(self):
        self.send("Sucursal QUILIN; Solicito comprar accesorios, impide entrenar, prioridad media")
        self.send("No")
        self.send("Descripción")
        result = self.send("Comprar dos accesorios compatibles según revisión del proveedor")["processing"]
        self.assertEqual(result["extracted_data"]["description"], "Comprar dos accesorios compatibles según revisión del proveedor")
        self.assertEqual(result["extracted_data"]["priority"], "medium")
        self.assertFalse(Incident.objects.exists())

    def test_initial_timeout_keeps_report_and_can_resume(self):
        provider = Mock(wraps=MockAIProvider())
        provider.extract_incident.side_effect = [TimeoutError(), MockAIProvider().extract_incident("área Cardio, trotadora 1 no enciende, impide entrenar, prioridad alta")]
        self.service = IncomingMessageService(incident_processor=IncidentProcessor(ai_provider=provider))
        result = self.complete_incident()
        self.assertEqual(result["action"], "ai_timeout")
        self.assertEqual(IncidentDraft.objects.count(), 1)
        result = self.send("continuar")["processing"]
        self.assertEqual(result["missing_fields"], [])
        self.send("Sí")
        self.assertEqual(Incident.objects.count(), 1)

    def test_invalid_ai_response_preserves_existing_draft(self):
        self.send("Sucursal QUILIN; área Cardio, trotadora 1 no enciende")
        draft = IncidentDraft.objects.get()
        previous = dict(draft.extracted_data)
        provider = Mock()
        provider.extract_incident.side_effect = ValueError("JSON inválido simulado")
        self.service = IncomingMessageService(incident_processor=IncidentProcessor(ai_provider=provider))
        result = self.send("Un dato adicional sobre lo ocurrido")["processing"]
        draft.refresh_from_db()
        self.assertEqual(result["action"], "ai_error")
        for key, value in previous.items():
            self.assertEqual(draft.extracted_data[key], value)
        self.assertIn("Un dato adicional", str(draft.extracted_data[IncidentProcessor.PENDING_MESSAGES_KEY]))

    def test_initial_ai_error_keeps_message_for_recovery(self):
        provider = Mock()
        provider.extract_incident.side_effect = ValueError("Respuesta inválida simulada")
        self.service = IncomingMessageService(incident_processor=IncidentProcessor(ai_provider=provider))
        result = self.complete_incident()
        self.assertEqual(result["action"], "ai_error")
        draft = IncidentDraft.objects.get()
        self.assertEqual(draft.extracted_data["branch_id"], self.branch.pk)
        self.assertIn("trotadora", str(draft.extracted_data[IncidentProcessor.PENDING_MESSAGES_KEY]))

    def test_materials_request_does_not_require_selecting_existing_inventory(self):
        self.inventory(code="M-01", name="Mancuernas")
        self.inventory(code="M-02", name="Mancuernas")
        result = self.send("Sucursal QUILIN; Solicito reponer mancuernas, impide entrenar, prioridad media")["processing"]
        self.assertEqual(result["missing_fields"], [])
        self.assertIsNone(result["extracted_data"]["gym_asset_id"])

    def test_priority_is_not_changed_by_incidental_word_in_ready_draft(self):
        self.complete_incident(priority="baja")
        result = self.send("La ubicación correcta es sala de planta alta")["processing"]
        self.assertEqual(result["extracted_data"]["priority"], "low")

    def test_asset_deactivation_after_summary_prevents_creation(self):
        asset = self.inventory()
        self.complete_incident()
        GymAsset.objects.filter(pk=asset.pk).update(is_active=False)
        result = self.send("Sí")["processing"]
        self.assertFalse(result["incident_created"])
        self.assertFalse(Incident.objects.exists())

    def test_cancel_prevents_later_confirmation_from_creating_report(self):
        self.complete_incident()
        result = self.send("/cancelar")["processing"]
        self.assertEqual(result["action"], "draft_cancelled")
        self.assertEqual(IncidentDraft.objects.get().status, "cancelled")
        self.send("Sí")
        self.assertFalse(Incident.objects.exists())


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send_text(self, recipient, text):
        self.sent.append((recipient, text))
        return {"result": {"message_id": 1000 + len(self.sent), "from": {"id": "fake-bot"}}}


class TelegramGymPlanetTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Quilín", code="QUILIN")
        self.reporter = Technician.objects.create(full_name="Reportante de prueba")
        self.channel = TechnicianChannel.objects.create(branch=self.branch, technician=self.reporter, provider="telegram", external_user_id="test-user", external_chat_id="test-chat")
        self.provider = FakeTelegram()
        self.handler = TelegramUpdateHandler(messaging_provider=self.provider)

    def update(self, text, number=1):
        return {"message": {"message_id": number, "from": {"id": "test-user"}, "chat": {"id": "test-chat", "type": "private"}, "text": text}}

    def test_start_is_gymplanet_branded_and_deduplicated(self):
        update = self.update("/start")
        self.assertEqual(self.handler.handle(update)["action"], "welcome_sent")
        self.assertEqual(self.handler.handle(update)["action"], "duplicate_message")
        self.assertEqual(len(self.provider.sent), 1)
        self.assertIn("Gym Planet", self.provider.sent[0][1])
        self.assertIn("solicitudes", self.provider.sent[0][1])
        self.assertNotIn("Secuoya", self.provider.sent[0][1])

    def test_start_requires_authorized_reporter(self):
        self.channel.is_active = False
        self.channel.save()
        self.assertEqual(self.handler.handle(self.update("/start"))["action"], "unauthorized_user")
        self.assertFalse(Incident.objects.exists())

    def test_cancel_is_authorized_and_deduplicated(self):
        draft = IncidentDraft.objects.create(channel_account=TechnicianChannel.objects.filter(technician=self.reporter).first(), technician=self.reporter)
        update = self.update("/cancelar")
        self.assertEqual(self.handler.handle(update)["action"], "draft_cancelled")
        self.assertEqual(self.handler.handle(update)["action"], "duplicate_message")
        draft.refresh_from_db()
        self.assertEqual(draft.status, "cancelled")

    def test_custom_draft_service_remains_supported(self):
        service = Mock()
        service.cancel_active_draft.return_value = None
        handler = TelegramUpdateHandler(messaging_provider=self.provider, draft_service=service)
        self.assertEqual(handler.handle(self.update("/cancelar"))["action"], "no_active_draft")
        service.cancel_active_draft.assert_called_once_with(technician=self.reporter, channel_account=self.channel)


class ProviderContractTests(TestCase):
    def test_gemini_rejects_nonobject_json_as_recoverable_error(self):
        provider = GeminiAIProvider.__new__(GeminiAIProvider)
        provider.model = "fake-model"
        provider._create_interaction_with_retry = Mock(return_value=SimpleNamespace(output_text="[]"))
        with self.assertRaises(ValueError):
            provider.extract_incident("Texto de prueba")

    def test_gemini_contract_and_prompt_without_external_connection(self):
        provider = GeminiAIProvider.__new__(GeminiAIProvider)
        provider.model = "fake-model"
        provider._create_interaction_with_retry = Mock(return_value=SimpleNamespace(output_text=json.dumps({
            "intent": "request_report", "report_type": "request", "branch": "Quilín",
            "request_kind": "Reposición", "description": "Reponer mancuernas", "operational_impact": "Impide entrenar", "priority": None,
        })))
        result = provider.extract_incident("Solicito reponer mancuernas en Quilín")
        self.assertEqual(result.report_type, "request")
        self.assertEqual(result.branch, "Quilín")
        self.assertIn("priority", result.missing_fields)
        prompt = provider._build_prompt("texto de prueba", {})
        self.assertIn("Gym Planet", prompt)
        self.assertNotIn("Secuoya", prompt)
        self.assertNotIn("Canal 13", prompt)

    def test_mock_request_contract_does_not_require_broken_machine(self):
        result = MockAIProvider().extract_incident("Solicito reponer mancuernas, impide entrenar, prioridad media")
        self.assertEqual(result.report_type, "request")
        self.assertNotIn("failure_type", result.missing_fields)
        self.assertNotIn("equipment", result.missing_fields)
