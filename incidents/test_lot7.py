import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from incidents.ai.gemini_provider import GeminiAIProvider
from incidents.ai.mock_provider import MockAIProvider
from incidents.models import Branch, ChannelMessage, EquipmentCategory, GymAsset, Incident, IncidentDraft, RequestCategory, Technician, TechnicianChannel
from incidents.services.gym_language import availability, failure, priority_choice
from incidents.services.gymplanet_conversation import report_type, request_kind
from incidents.services.incident_processor import IncidentProcessor
from incidents.services.incoming_message_service import IncomingMessageService


class ChileanLanguageTests(TestCase):
    def test_observed_failures(self):
        examples = {"La bicicleta mete un ruido raro": "Ruido inusual", "No están funcionando las duchas": "No funciona", "El torniquete no deja pasar a los socios": "No permite el acceso", "El aire acondicionado no está enfriando": "No enfría", "Hay una fuga de agua en camarines": "Fuga de agua", "La máquina funciona a ratos": "Funcionamiento intermitente", "El equipo quedó parado desde ayer": "Equipo detenido"}
        for text, expected in examples.items():
            with self.subTest(text=text):
                self.assertEqual(failure(text), expected)
                self.assertEqual(report_type(text), "incident")

    def test_vague_or_hypothetical_symptoms_do_not_invent_diagnosis(self):
        for text in ("La trotadora está mala", "Quizás está detenido", "No está detenido", "No hay una fuga de agua"):
            with self.subTest(text=text):
                self.assertIsNone(failure(text))

    def test_requests_are_not_based_only_on_need_word(self):
        self.assertEqual(report_type("Necesito que revisen una máquina que dejó de funcionar"), "incident")
        for text in ("Se nos acabó el papel higiénico", "Necesitamos más mancuernas", "Nos faltan cinco colchonetas", "Necesito mantenimiento preventivo"):
            with self.subTest(text=text):
                self.assertEqual(report_type(text), "request")
                self.assertIsNotNone(request_kind(text))

    def test_pending_type_does_not_change_for_answer(self):
        self.assertEqual(report_type("No funciona", previous="request"), "request")
        self.assertEqual(report_type("Limpieza", previous="incident"), "incident")

    def test_negations_preserve_literal_availability(self):
        for text in ("No está fuera de servicio", "No hay riesgo", "Hay riesgo", "No está detenido", "Se puede utilizar", "No se puede utilizar"):
            with self.subTest(text=text):
                self.assertEqual(availability(text), text)
        self.assertIsNone(availability("Quizás está fuera de servicio"))

    def test_explicit_priority_with_negative_alternative(self):
        self.assertEqual(priority_choice("La prioridad es media, no alta"), "medium")
        self.assertEqual(priority_choice("No era prioridad alta, es prioridad media"), "medium")
        self.assertEqual(priority_choice("No se puede utilizar, prioridad alta"), "high")
        for text in ("Quizás sea prioridad alta", "No es prioridad alta", "Prioridad alta o baja", "No quiero prioridad alta"):
            self.assertIsNone(priority_choice(text))


@override_settings(AI_PROVIDER="mock", GEMINI_API_KEY="", TELEGRAM_BOT_TOKEN="")
class AdvancedConversationTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="TEST-7", name="Sede de prueba")
        self.reporter = Technician.objects.create(full_name="Cuenta corporativa de prueba")
        self.account = TechnicianChannel.objects.create(branch=self.branch, technician=self.reporter, provider="telegram", external_user_id="lot7-test")
        self.provider = Mock(wraps=MockAIProvider())
        self.service = IncomingMessageService(incident_processor=IncidentProcessor(ai_provider=self.provider))
        self.number = 0

    def send(self, text, mid=None):
        self.number += 1
        return self.service.process_text(provider="telegram", external_user_id="lot7-test", external_chat_id="lot7-test", external_message_id=mid or f"lot7:{self.number}", text=text, raw_payload={}, verified_transport=True)

    def complete(self):
        return self.send("Área Cardio, trotadora 3 no enciende, impide entrenar, prioridad alta")["processing"]

    def test_priority_correction_has_no_provider_call_and_reconfirms(self):
        self.complete()
        calls = self.provider.extract_incident.call_count
        result = self.send("La prioridad es media, no alta")["processing"]
        self.assertEqual(result["extracted_data"]["priority"], "medium")
        self.assertEqual(self.provider.extract_incident.call_count, calls)
        self.assertFalse(Incident.objects.exists())
        self.send("Sí")
        self.assertEqual(Incident.objects.get().priority, "medium")

    def test_equipment_correction_updates_only_relevant_fields(self):
        self.complete()
        before = dict(IncidentDraft.objects.get().extracted_data)
        calls = self.provider.extract_incident.call_count
        result = self.send("No era trotadora, era bicicleta")["processing"]
        self.assertEqual(result["extracted_data"]["equipment"], "bicicleta")
        for field in ("priority", "failure_type", "branch_id", "operational_impact"):
            self.assertEqual(result["extracted_data"][field], before[field])
        self.assertEqual(calls, self.provider.extract_incident.call_count)
        self.assertFalse(Incident.objects.exists())

    def test_signed_priority_correction_does_not_change_equipment(self):
        self.complete()
        before = dict(IncidentDraft.objects.get().extracted_data)
        calls = self.provider.extract_incident.call_count
        result = self.send("No era prioridad alta, es prioridad media")["processing"]
        self.assertEqual(result["extracted_data"]["priority"], "medium")
        self.assertEqual(result["extracted_data"]["equipment"], before["equipment"])
        self.assertEqual(calls, self.provider.extract_incident.call_count)
        self.assertFalse(Incident.objects.exists())

    def test_negated_priority_requires_a_new_explicit_choice(self):
        self.complete()
        result = self.send("No era prioridad alta")["processing"]
        self.assertIsNone(result["extracted_data"]["priority"])
        self.assertIn("priority", result["missing_fields"])
        self.assertFalse(Incident.objects.exists())

    def test_hypothetical_answer_does_not_confirm_priority(self):
        self.send("Área Cardio, trotadora 3 no enciende, impide entrenar")
        result = self.send("Podría ser alta")["processing"]
        self.assertIsNone(result["extracted_data"]["priority"])
        self.assertIn("priority", result["missing_fields"])
        self.assertFalse(Incident.objects.exists())

    def test_quantity_correction_preserves_request_context(self):
        self.send("Solicito reponer 5 colchonetas, impide entrenar, prioridad media")
        calls = self.provider.extract_incident.call_count
        result = self.send("Me equivoqué, eran 8 colchonetas")["processing"]
        self.assertIn("8 colchonetas", result["extracted_data"]["description"])
        self.assertNotIn("5 colchonetas", result["extracted_data"]["description"])
        self.assertEqual(result["extracted_data"]["priority"], "medium")
        self.assertEqual(calls, self.provider.extract_incident.call_count)
        self.assertFalse(Incident.objects.exists())

    def test_negative_impact_correction_does_not_change_failure(self):
        self.complete()
        result = self.send("La máquina no quedó fuera de servicio")["processing"]
        self.assertEqual(result["extracted_data"]["failure_type"], "No enciende")
        self.assertIn("no quedó fuera de servicio", result["extracted_data"]["operational_impact"])
        self.assertFalse(Incident.objects.exists())

    def test_symptom_correction_is_deterministic(self):
        self.complete()
        calls = self.provider.extract_incident.call_count
        self.send("Corrige el síntoma")
        result = self.send("Se detiene al iniciar")["processing"]
        self.assertEqual(result["extracted_data"]["failure_type"], "Se detiene al iniciar")
        self.assertEqual(calls, self.provider.extract_incident.call_count)
        self.assertFalse(Incident.objects.exists())

    def test_contradiction_requires_review_before_final_confirmation(self):
        self.complete()
        result = self.send("La máquina funciona a ratos")["processing"]
        self.assertIn("difiere", result["reply"])
        self.send("Sí")
        self.assertFalse(Incident.objects.exists())
        self.assertEqual(IncidentDraft.objects.get().extracted_data["failure_type"], "Funcionamiento intermitente")
        self.send("Sí")
        self.assertEqual(Incident.objects.count(), 1)

    def test_new_report_never_overwrites_pending(self):
        self.complete()
        before = dict(IncidentDraft.objects.get().extracted_data)
        for text in ("Nueva incidencia: bicicleta mala", "Solicito comprar mancuernas"):
            self.assertEqual(self.send(text)["processing"]["action"], "pending_report")
            self.assertEqual(IncidentDraft.objects.get().extracted_data, before)
        self.assertFalse(Incident.objects.exists())

    def test_repeat_and_duplicate_messages_do_not_call_provider(self):
        self.send("La trotadora está mala")
        question = IncidentDraft.objects.get().last_question
        calls = self.provider.extract_incident.call_count
        self.assertEqual(self.send("Repite la pregunta", mid="repeat")["processing"]["reply"], question)
        self.assertTrue(self.send("Repite la pregunta", mid="repeat")["duplicate"])
        self.assertEqual(calls, self.provider.extract_incident.call_count)

    def test_repeated_confirmation_creates_only_one_report(self):
        self.complete()
        self.send("Sí")
        self.send("Sí")
        self.assertEqual(Incident.objects.count(), 1)

    def test_repeated_ai_errors_resume_without_new_external_call(self):
        self.provider.extract_incident.side_effect = TimeoutError("synthetic provider failure")
        self.assertEqual(self.complete()["action"], "ai_timeout")
        self.send("Continuar")
        self.assertEqual(self.provider.extract_incident.call_count, 2)
        result = self.send("Continuar")["processing"]
        self.assertEqual(self.provider.extract_incident.call_count, 2)
        self.assertEqual(result["missing_fields"], [])
        self.assertFalse(Incident.objects.exists())
        self.send("Sí")
        self.assertEqual(Incident.objects.count(), 1)

    def test_partial_recovery_does_not_invent_fields(self):
        self.provider.extract_incident.side_effect = ValueError("invalid JSON")
        self.send("La trotadora está mala")
        self.send("Continuar")
        result = self.send("Continuar")["processing"]
        self.assertIn("priority", result["missing_fields"])
        self.assertFalse(Incident.objects.exists())

    def test_optional_asset_association_remains_optional(self):
        category = EquipmentCategory.objects.create(code="TEST-7", name="Cardio")
        for code in ("TEST-T1", "TEST-T2"):
            GymAsset.objects.create(branch=self.branch, category=category, code=code, name="Trotadora 3")
        self.complete()
        result = self.send("Continuar sin activo")["processing"]
        self.assertIsNone(result["extracted_data"]["gym_asset_id"])
        self.send("Sí")
        self.assertIsNone(Incident.objects.get().gym_asset_id)
        self.assertTrue(all(a.operational_status == "operational" for a in GymAsset.objects.all()))

    def test_text_cannot_change_branch_even_during_correction(self):
        self.complete()
        self.assertEqual(self.send("Sucursal otra sede;")["processing"]["action"], "branch_mismatch")
        self.assertFalse(Incident.objects.exists())

    def test_equipment_article_boundary_preserves_facts(self):
        from incidents.services.conversation_controls import replace_equipment
        for article in ("una ", ""):
            self.complete()
            draft = IncidentDraft.objects.get(status__in=["ready", "collecting"])
            draft.extracted_data["equipment"] = "trotadora"
            draft.extracted_data["description"] = "Una trotadora en cardio no enciende y no hay riesgo."
            draft.save()
            result = self.send("Me equivoqué, no era una trotadora, era " + article + "bicicleta estática")["processing"]
            self.assertEqual(result["extracted_data"]["description"], "Una bicicleta estática en cardio no enciende y no hay riesgo.")
            self.assertEqual(result["extracted_data"]["failure_type"], "No enciende")
            self.assertEqual(result["extracted_data"]["operational_impact"], "Impide entrenar")
            self.assertIn("Entendido, cambié el equipo a bicicleta estática", result["reply"])
            self.assertFalse(Incident.objects.exists())
            self.send("/cancelar")
        self.assertEqual(replace_equipment("Dos trotadoras y una trotadora", "trotadora", "una bicicleta"), "Dos trotadoras y una bicicleta")
        self.assertEqual(replace_equipment("Trotadora y trotadora", "trotadora", "bicicleta"), "Trotadora y trotadora")
        self.assertEqual(replace_equipment("Sin coincidencia", "trotadora", "bicicleta"), "Sin coincidencia")

    def test_ambiguous_equipment_has_no_false_acknowledgment(self):
        self.complete()
        before = dict(IncidentDraft.objects.get().extracted_data)
        result = self.send("No era trotadora, era bicicleta o elíptica")["processing"]
        self.assertNotIn("Entendido", result["reply"])
        self.assertEqual(IncidentDraft.objects.get().extracted_data, before)

    def test_quantity_and_priority_acknowledgments_still_require_confirmation(self):
        self.send("Solicito reponer 5 colchonetas, impide entrenar, prioridad media")
        result = self.send("Me equivoqué, eran 8 colchonetas")["processing"]
        self.assertIn("actualicé la cantidad", result["reply"])
        result = self.send("Prioridad alta")["processing"]
        self.assertIn("actualicé la prioridad", result["reply"])
        self.assertFalse(Incident.objects.exists())
        self.send("Sí")
        self.assertEqual(Incident.objects.count(), 1)

    def test_acknowledges_other_explicit_fields(self):
        self.complete()
        for text, label in (("La ubicación correcta es camarines", "ubicación"), ("El área correcta es mantenimiento", "área"), ("El síntoma correcto es ruido al arrancar", "síntoma"), ("La descripción correcta es bicicleta con ruido al arrancar", "descripción"), ("El impacto operacional correcto es impide utilizarla", "impacto")):
            with self.subTest(text=text):
                result = self.send(text)["processing"]
                self.assertIn("Entendido", result["reply"])
                self.assertIn(label, result["reply"])
                self.assertFalse(Incident.objects.exists())
        self.send("/cancelar")
        self.assertEqual(IncidentDraft.objects.get().status, "cancelled")


class ProviderRobustnessTests(TestCase):
    def provider(self, output):
        provider = GeminiAIProvider.__new__(GeminiAIProvider)
        provider.model = "synthetic-test-model"
        provider._create_interaction_with_retry = Mock(return_value=SimpleNamespace(output_text=output))
        return provider

    def test_empty_malformed_and_wrong_schema(self):
        for output in ("", "{", "[]", '{"equipment":7}', '{"report_type":"unknown"}', '{"missing_fields":[7]}', '{"intent":"delete_all"}'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.provider(output).extract_incident("Mensaje de prueba")

    def test_context_excludes_internal_database_and_pending_metadata(self):
        provider = GeminiAIProvider.__new__(GeminiAIProvider)
        prompt = provider._build_prompt("Reporte", {"_authorized_branch":"Sede autorizada", "branch_id":98765, "_pending_messages":["internal-pending-marker"], "_branch_conflict":True})
        self.assertIn("Sede autorizada", prompt)
        self.assertNotIn("98765", prompt)
        self.assertNotIn("internal-pending-marker", prompt)

    def test_retryable_statuses_and_connection_errors(self):
        provider = GeminiAIProvider.__new__(GeminiAIProvider)
        for status in (429, 500, 503, "429"):
            error = RuntimeError("private provider text")
            error.status_code = status
            self.assertTrue(provider._is_retryable_error(error))
        self.assertTrue(provider._is_retryable_error(httpx.ReadError("network failed")))

    def test_errors_retry_at_most_twice_and_log_no_payload(self):
        for status in (429,500,503):
            provider = GeminiAIProvider.__new__(GeminiAIProvider)
            provider.model = "synthetic"
            error = RuntimeError("DO-NOT-LOG-PAYLOAD")
            error.status_code = status
            provider.client = Mock()
            provider.client.interactions.create.side_effect = error
            with self.assertLogs("incidents.ai.gemini_provider", level="WARNING") as logs:
                with self.assertRaises(TimeoutError):
                    provider._create_interaction_with_retry("private-worker-message")
            self.assertEqual(provider.client.interactions.create.call_count, 2)
            self.assertNotIn("DO-NOT-LOG-PAYLOAD", str(logs.output))
            self.assertNotIn("private-worker-message", str(logs.output))

    def test_non_retryable_error_is_sanitized(self):
        provider = GeminiAIProvider.__new__(GeminiAIProvider)
        provider.model = "synthetic"
        provider.client = Mock()
        provider.client.interactions.create.side_effect = RuntimeError("DO-NOT-EXPOSE")
        with self.assertRaises(RuntimeError) as error:
            provider._create_interaction_with_retry("test")
        self.assertNotIn("DO-NOT-EXPOSE", str(error.exception))
        self.assertEqual(provider.client.interactions.create.call_count, 1)


class CategorySeedTests(TestCase):
    def seed(self, apply=False):
        out = io.StringIO()
        call_command("seed_gymplanet_request_categories", apply=apply, stdout=out, stderr=io.StringIO())
        return out.getvalue()

    def test_default_is_simulation(self):
        self.assertIn("Simulación", self.seed())
        self.assertFalse(RequestCategory.objects.exists())

    def test_explicit_apply_is_idempotent(self):
        self.seed(True)
        self.assertEqual(RequestCategory.objects.count(), 10)
        self.seed(True)
        self.assertEqual(RequestCategory.objects.count(), 10)

    def test_discrepancy_rolls_back_and_preserves_existing(self):
        row = RequestCategory.objects.create(code="OTROS", name="Mi nombre validado", is_active=False)
        with self.assertRaises(CommandError):
            self.seed(True)
        row.refresh_from_db()
        self.assertEqual((row.name,row.is_active), ("Mi nombre validado",False))
        self.assertEqual(RequestCategory.objects.count(), 1)

    def test_equivalent_name_with_different_code_is_not_duplicated(self):
        RequestCategory.objects.create(code="EXISTENTE", name="CLIMATIZACION")
        with self.assertRaises(CommandError):
            self.seed(True)
        self.assertEqual(RequestCategory.objects.count(), 1)


class BrandAndAssetTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="brand-test", is_superuser=True, is_staff=True)
        self.client.force_login(self.user)
        self.branch = Branch.objects.create(code="TEST-7", name="Sede de prueba")
        self.category = EquipmentCategory.objects.create(code="TEST-FIT", name="Cardio")
        self.asset = GymAsset.objects.create(branch=self.branch, category=self.category, code="TEST-BICI", name="Bicicleta", operational_status="out_of_service")

    def test_official_logo_and_favicon_are_local(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response,"gymplanet-logo-color.avif")
        self.assertContains(response,"gymplanet-favicon.png")
        self.assertNotContains(response,'class="brand-mark"')
        self.assertNotContains(response,'https://')

    def test_palette_tokens_and_distinct_critical_semantics(self):
        css = Path("incidents/static/incidents/dashboard.css").read_text(encoding="utf-8")
        self.assertIn("--brand-primary:#ab4e3c", css)
        self.assertIn("--brand-primary-hover:#ff7e5f", css)
        self.assertNotIn("#c9f36b", css)
        for name in ("brand-primary-active","brand-primary-soft","background-main","background-sidebar","background-card","background-elevated","text-primary","text-secondary","border-default","border-strong","focus-ring","critical-bg"):
            self.assertIn("--"+name+":", css)

    def test_asset_detail_is_read_only_and_shows_history(self):
        reporter = Technician.objects.create(full_name="Cuenta de prueba")
        report = Incident.objects.create(technician=reporter, branch=self.branch, gym_asset=self.asset, equipment="Bicicleta", failure_type="Ruido", description="Reporte de prueba", priority="low")
        url = reverse("dashboard-asset-detail", args=[self.asset.pk])
        response = self.client.get(url)
        self.assertContains(response,report.code)
        self.assertContains(response,"Fuera de servicio")
        self.assertEqual(self.client.post(url, {"operational_status":"operational"}).status_code, 405)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.operational_status,"out_of_service")
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)


@override_settings(AI_PROVIDER="mock", GEMINI_API_KEY="", TELEGRAM_BOT_TOKEN="", INCIDENT_NOTIFICATION_EMAIL="")
class ConcurrentConfirmationTests(TransactionTestCase):
    def test_two_confirmations_create_one_report(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import close_old_connections, connection

        self.assertEqual(connection.settings_dict["NAME"], "test_incidentes_gymplanet_db")
        branch = Branch.objects.create(code="TEST-CONFIRM", name="Sede de prueba")
        reporter = Technician.objects.create(full_name="Cuenta compartida de prueba")
        TechnicianChannel.objects.create(branch=branch, technician=reporter, provider="telegram", external_user_id="synthetic-concurrent")
        def send(text, number):
            return IncomingMessageService().process_text(provider="telegram", external_user_id="synthetic-concurrent", external_chat_id="synthetic-concurrent", external_message_id=f"concurrent-confirm:{number}", text=text, raw_payload={}, verified_transport=True)
        send("Área Cardio, trotadora 3 no enciende, impide entrenar, prioridad alta", 0)
        def confirm(number):
            close_old_connections()
            try:
                return send("Sí", number)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(confirm, (1,2)))
        self.assertEqual(Incident.objects.count(), 1)
        self.assertEqual(IncidentDraft.objects.filter(status="completed").count(), 1)
