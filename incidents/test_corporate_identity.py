"""Synthetic identities live exclusively in Django's test database."""
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.forms import modelform_factory
from django.test import TestCase, override_settings
from django.urls import reverse

from incidents.models import Branch, ChannelMessage, Incident, IncidentDraft, Technician, TechnicianChannel
from incidents.services.incoming_message_service import ChannelNotAuthorizedError, IncomingMessageService
from incidents.services.incident_processor import IncidentProcessor
from incidents.test_gymplanet_conversation import FakeTelegram
from incidents.messaging.telegram_update_handler import TelegramUpdateHandler


@override_settings(AI_PROVIDER="mock", TELEGRAM_BOT_TOKEN="", GEMINI_API_KEY="", TELEGRAM_AUTO_REGISTER=False)
class CorporateIdentityTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="TEST-A", name="Sede Alfa")
        self.other_branch = Branch.objects.create(code="TEST-B", name="Sede Beta")
        self.reporter = Technician.objects.create(full_name="Cuenta corporativa compartida")
        self.account = TechnicianChannel.objects.create(technician=self.reporter, branch=self.branch, provider="telegram", external_user_id="synthetic-a")
        self.other_account = TechnicianChannel.objects.create(technician=self.reporter, branch=self.other_branch, provider="telegram", external_user_id="synthetic-b")
        self.service = IncomingMessageService()
        self.number = 0

    def send(self, text, account=None, message_id=None, verified=True):
        account = account or self.account
        self.number += 1
        return self.service.process_text(
            provider=account.provider, external_user_id=account.external_user_id,
            external_chat_id=account.external_user_id, external_message_id=message_id or f"synthetic:{self.number}",
            text=text, raw_payload={}, verified_transport=verified,
        )

    def complete(self, account=None):
        return self.send("Área Cardio, trotadora 3 no enciende, impide entrenar, prioridad alta", account)["processing"]

    def test_identity_selects_branch_without_text(self):
        result = self.complete()
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(result["extracted_data"]["branch_id"], self.branch.pk)
        self.send("Sí")
        self.assertEqual(Incident.objects.get().branch, self.branch)

    def test_identity_does_not_require_phone_number_or_person(self):
        self.complete()
        self.assertIsNone(self.reporter.whatsapp_number)
        self.assertEqual(IncidentDraft.objects.get().channel_account, self.account)

    def test_unverified_ids_are_not_identity_proof(self):
        with self.assertRaises(ChannelNotAuthorizedError):
            self.send("Solicito materiales", verified=False)
        self.assertFalse(ChannelMessage.objects.exists())

    def test_unassigned_account_cannot_create_report(self):
        self.account.branch = None
        self.account.save()
        with self.assertRaises(ChannelNotAuthorizedError):
            self.complete()
        self.assertFalse(IncidentDraft.objects.exists())

    def test_inactive_account_revoked_after_summary(self):
        self.complete()
        self.account.is_active = False
        self.account.save()
        with self.assertRaises(ChannelNotAuthorizedError):
            self.send("Sí")
        self.assertFalse(Incident.objects.exists())
        self.assertEqual(IncidentDraft.objects.get().status, "ready")

    def test_inactive_reporter_revoked_after_summary(self):
        self.complete()
        Technician.objects.filter(pk=self.reporter.pk).update(is_active=False)
        with self.assertRaises(ChannelNotAuthorizedError):
            self.send("Sí")
        self.assertFalse(Incident.objects.exists())

    def test_inactive_branch_revoked_after_summary(self):
        self.complete()
        Branch.objects.filter(pk=self.branch.pk).update(is_active=False)
        with self.assertRaises(ChannelNotAuthorizedError):
            self.send("Sí")
        self.assertFalse(Incident.objects.exists())

    def test_reassigned_account_does_not_move_or_reuse_old_draft(self):
        self.complete()
        draft = IncidentDraft.objects.get()
        self.account.branch = self.other_branch
        self.account.save()
        self.assertEqual(self.send("Sí")["processing"]["action"], "branch_mismatch")
        draft.refresh_from_db()
        self.assertEqual(draft.extracted_data["branch_id"], self.branch.pk)
        self.assertFalse(Incident.objects.exists())
        self.send("/cancelar")
        self.complete()
        self.send("Sí")
        self.assertEqual(Incident.objects.get().branch, self.other_branch)
        self.assertEqual(IncidentDraft.objects.get(pk=draft.pk).status, "cancelled")

    def test_foreign_branch_mention_blocks_and_requires_resolution(self):
        self.complete()
        self.assertEqual(self.send("Ocurre en Sede Beta")["processing"]["action"], "branch_mismatch")
        self.assertFalse(self.send("Sí")["processing"]["incident_created"])
        self.send("Sucursal TEST-A;")
        self.send("Sí")
        self.assertEqual(Incident.objects.get().branch, self.branch)

    def test_initial_foreign_branch_does_not_start_draft(self):
        self.assertEqual(self.send("Sucursal TEST-B; solicito materiales")["processing"]["action"], "branch_mismatch")
        self.assertFalse(IncidentDraft.objects.exists())

    def test_authorized_code_does_not_mask_foreign_branch_name(self):
        result = self.send("Sucursal TEST-A; el problema ocurre en Sede Beta")["processing"]
        self.assertEqual(result["action"], "branch_mismatch")
        self.assertFalse(IncidentDraft.objects.exists())

    def test_unknown_explicit_branch_is_rejected_without_delimiter(self):
        self.assertEqual(self.send("Cambiar a sucursal desconocida")["processing"]["action"], "branch_mismatch")
        self.assertFalse(IncidentDraft.objects.exists())

    def test_ai_cannot_change_authorized_branch(self):
        from incidents.ai.mock_provider import MockAIProvider
        from unittest.mock import Mock

        provider = Mock(wraps=MockAIProvider())
        extraction = MockAIProvider().extract_incident("Área Cardio, trotadora 3 no enciende, impide entrenar, prioridad alta")
        extraction.branch = self.other_branch.code
        provider.extract_incident.return_value = extraction
        self.service = IncomingMessageService(incident_processor=IncidentProcessor(ai_provider=provider))
        result = self.complete()
        self.assertEqual(result["extracted_data"]["branch_id"], self.branch.pk)
        self.send("Sí")
        self.assertEqual(Incident.objects.get().branch, self.branch)

    def test_accounts_sharing_reporter_have_independent_drafts(self):
        self.complete()
        self.complete(self.other_account)
        self.assertEqual(IncidentDraft.objects.count(), 2)
        self.send("Sí", self.other_account)
        self.assertEqual(Incident.objects.get().branch, self.other_branch)
        self.assertEqual(IncidentDraft.objects.get(channel_account=self.account).status, "ready")
        self.send("Sí")
        self.assertEqual(Incident.objects.count(), 2)

    def test_cancel_does_not_cancel_other_account_draft(self):
        self.complete()
        self.complete(self.other_account)
        self.send("/cancelar")
        self.assertEqual(IncidentDraft.objects.get(channel_account=self.account).status, "cancelled")
        self.assertEqual(IncidentDraft.objects.get(channel_account=self.other_account).status, "ready")

    def test_duplicate_message_is_idempotent(self):
        self.send("Solicito materiales", message_id="synthetic:duplicate")
        self.assertTrue(self.send("Solicito materiales", message_id="synthetic:duplicate")["duplicate"])
        self.assertEqual(ChannelMessage.objects.count(), 1)

    def test_duplicate_id_from_other_account_does_not_expose_message(self):
        self.send("Solicito materiales", message_id="synthetic:collision")
        with self.assertRaises(ChannelNotAuthorizedError):
            self.send("Solicito materiales", self.other_account, message_id="synthetic:collision")
        self.assertEqual(ChannelMessage.objects.count(), 1)

    def test_duplicate_provider_identity_rejected_in_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            TechnicianChannel.objects.create(technician=self.reporter, branch=self.other_branch, provider="telegram", external_user_id=self.account.external_user_id)

    def test_future_provider_namespace_kept_separate(self):
        account = TechnicianChannel.objects.create(technician=self.reporter, branch=self.branch, provider="whatsapp", external_user_id=self.account.external_user_id)
        account.full_clean()
        self.assertNotEqual(account.pk, self.account.pk)

    def test_database_allows_only_one_active_draft_per_account(self):
        self.complete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            IncidentDraft.objects.create(technician=self.reporter, channel_account=self.account)

    def test_legacy_unattributed_draft_is_preserved_without_resuming_it(self):
        legacy = IncidentDraft.objects.create(technician=self.reporter, extracted_data={"description": "Historia anterior"})
        self.complete()
        self.assertEqual(IncidentDraft.objects.count(), 2)
        legacy.refresh_from_db()
        self.assertEqual(legacy.extracted_data, {"description": "Historia anterior"})

    def test_historical_draft_backfill_uses_only_proven_message_account(self):
        message = ChannelMessage.objects.create(technician=self.reporter, channel_account=self.account, provider="telegram", external_message_id="history", content="Historia")
        draft = IncidentDraft.objects.create(technician=self.reporter, initial_message=message)
        import_module("incidents.migrations.0016_corporate_account_branch").link_historical_drafts(apps, SimpleNamespace(connection=SimpleNamespace(alias="default")))
        draft.refresh_from_db()
        self.assertEqual(draft.channel_account, self.account)

    def test_used_identity_cannot_be_repurposed_in_admin_form(self):
        self.complete()
        form_class = modelform_factory(TechnicianChannel, fields=["technician", "branch", "provider", "external_user_id", "is_active"])
        form = form_class(instance=self.account, data={"technician": self.reporter.pk, "branch": self.branch.pk, "provider": "telegram", "external_user_id": "different-identity", "is_active": True})
        self.assertFalse(form.is_valid())
        self.assertIn("historial", str(form.errors))

    def test_admin_refuses_association_to_inactive_branch(self):
        self.branch.is_active = False
        self.branch.save()
        with self.assertRaises(ValidationError):
            self.account.full_clean()

    def test_admin_can_disable_account_even_if_branch_is_inactive(self):
        self.branch.is_active = False
        self.branch.save()
        self.account.is_active = False
        self.account.full_clean()
        self.account.save()
        with self.assertRaises(ChannelNotAuthorizedError):
            self.complete()

    def test_branch_and_account_with_drafts_are_protected(self):
        self.complete()
        with self.assertRaises(ProtectedError):
            self.branch.delete()
        with self.assertRaises(ProtectedError):
            self.account.delete()

    def test_account_with_only_message_history_is_protected(self):
        self.send("/start")
        self.assertFalse(IncidentDraft.objects.exists())
        with self.assertRaises(ProtectedError):
            self.account.delete()
        with self.assertRaises(ProtectedError):
            self.reporter.delete()

    def test_draft_validation_rejects_another_reporter_account(self):
        other = Technician.objects.create(full_name="Otra cuenta corporativa")
        draft = IncidentDraft(technician=other, channel_account=self.account)
        with self.assertRaises(ValidationError):
            draft.full_clean()

    def test_draft_validation_rejects_initial_message_from_another_account(self):
        message = ChannelMessage.objects.create(technician=self.reporter, channel_account=self.other_account, provider="telegram", external_message_id="wrong-draft-source", content="Historia")
        draft = IncidentDraft(technician=self.reporter, channel_account=self.account, initial_message=message)
        with self.assertRaises(ValidationError):
            draft.full_clean()

    def test_processor_checks_persisted_sender_again(self):
        message = ChannelMessage.objects.create(technician=self.reporter, channel_account=self.account, provider="telegram", external_sender_id="spoofed", external_message_id="spoofed", content="Solicito materiales")
        self.assertEqual(IncidentProcessor().process(message)["action"], "unauthorized_account")
        self.assertFalse(IncidentDraft.objects.exists())

    def test_processor_without_account_cannot_select_branch_from_text(self):
        message = ChannelMessage.objects.create(technician=self.reporter, provider="telegram", external_message_id="no-account", content="Sucursal TEST-A; solicito materiales")
        self.assertEqual(IncidentProcessor().process(message)["action"], "unauthorized_account")
        self.assertFalse(IncidentDraft.objects.exists())

    def test_requests_use_authorized_branch_without_technical_fields(self):
        result = self.send("Solicito reponer toallas, impide entrenar, prioridad media")["processing"]
        self.assertEqual(result["missing_fields"], [])
        self.send("Sí")
        report = Incident.objects.get()
        self.assertEqual((report.branch, report.report_type, report.equipment), (self.branch, "request", ""))

    def test_legacy_incident_without_branch_remains_valid(self):
        report = Incident(technician=self.reporter, equipment="Equipo histórico", failure_type="No enciende", description="Reporte histórico", original_message="Mensaje histórico", priority="low")
        report.full_clean()
        report.save()
        self.assertIsNone(report.branch_id)


@override_settings(TELEGRAM_WEBHOOK_ENABLED=False, TELEGRAM_WEBHOOK_SECRET="", ENABLE_TEST_MESSAGE_ENDPOINT=False)
class AccessSecurityTests(TestCase):
    def setUp(self):
        self.superuser = get_user_model().objects.create_user(username="synthetic-admin", is_staff=True, is_superuser=True)
        self.staff = get_user_model().objects.create_user(username="synthetic-staff", is_staff=True)
        reporter = Technician.objects.create(full_name="Cuenta de prueba")
        self.incident = Incident.objects.create(technician=reporter, equipment="Equipo", failure_type="Falla", description="Historia", priority="low")

    def api_urls(self):
        return [reverse("incidents:incident-list"), reverse("incidents:incident-summary"), reverse("incidents:incident-analytics"), reverse("incidents:incident-detail", args=[self.incident.pk])]

    def test_anonymous_cannot_read_incidents_or_statistics(self):
        for url in self.api_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_staff_without_branch_scope_cannot_read_any_branch(self):
        self.client.force_login(self.staff)
        for url in self.api_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url, {"branch_id": "1"}).status_code, 403)

    def test_superuser_can_read_existing_historical_reports(self):
        self.client.force_login(self.superuser)
        for url in self.api_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_status_update_requires_authorized_administrator(self):
        url = reverse("incidents:incident-detail", args=[self.incident.pk])
        self.assertEqual(self.client.patch(url, {"status": "resolved"}, content_type="application/json").status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.patch(url, {"status": "resolved"}, content_type="application/json").status_code, 403)
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, "reported")
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.patch(url, {"status": "in_progress"}, content_type="application/json").status_code, 200)

    def test_dashboard_requires_authenticated_superuser(self):
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 403)
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_admin_retains_access_for_superuser_only(self):
        url = reverse("admin:incidents_technicianchannel_changelist")
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertIn("branch", admin.site._registry[TechnicianChannel].get_form(SimpleNamespace(user=self.superuser)).base_fields)

    def test_test_message_endpoint_is_closed_even_with_client_identity(self):
        url = reverse("incidents:incoming-text-message-test")
        payload = {"provider": "telegram", "external_user_id": "synthetic-a", "text": "Solicito materiales"}
        self.assertEqual(self.client.post(url, payload).status_code, 403)
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.post(url, payload).status_code, 404)
        self.assertFalse(ChannelMessage.objects.exists())

    @override_settings(DEBUG=True, ENABLE_TEST_MESSAGE_ENDPOINT=True)
    def test_controlled_development_endpoint_still_cannot_impersonate_sender(self):
        self.client.force_login(self.superuser)
        with patch("incidents.services.incoming_message_service.IncomingMessageService") as service:
            self.assertEqual(self.client.post(reverse("incidents:incoming-text-message-test"), {"external_user_id": "synthetic-a"}).status_code, 403)
            service.assert_not_called()

    def test_webhook_disabled_by_default(self):
        with patch("incidents.views.TelegramUpdateHandler") as handler:
            self.assertEqual(self.client.post(reverse("incidents:telegram-webhook"), {}, content_type="application/json").status_code, 404)
            handler.assert_not_called()

    @override_settings(TELEGRAM_WEBHOOK_ENABLED=True, TELEGRAM_WEBHOOK_SECRET="")
    def test_webhook_cannot_be_enabled_without_secret(self):
        self.assertEqual(self.client.post(reverse("incidents:telegram-webhook"), {}, content_type="application/json").status_code, 404)

    @override_settings(TELEGRAM_WEBHOOK_ENABLED=True, TELEGRAM_WEBHOOK_SECRET="synthetic-test-secret")
    def test_webhook_checks_secret_before_handler(self):
        with patch("incidents.views.TelegramUpdateHandler") as handler:
            for token in ("", "wrong", "inválido"):
                with self.subTest(token=token):
                    self.assertEqual(self.client.post(reverse("incidents:telegram-webhook"), {}, content_type="application/json", HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=token).status_code, 403)
            handler.assert_not_called()

    @override_settings(TELEGRAM_WEBHOOK_ENABLED=True, TELEGRAM_WEBHOOK_SECRET="synthetic-test-secret")
    def test_valid_webhook_uses_verified_adapter_without_external_services(self):
        payload = {"update_id": 123}
        with patch("incidents.views.TelegramUpdateHandler") as handler:
            handler.return_value.handle.return_value = {"action": "ignored"}
            self.assertEqual(self.client.post(reverse("incidents:telegram-webhook"), payload, content_type="application/json", HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="synthetic-test-secret").status_code, 200)
            handler.return_value.handle.assert_called_once_with(payload)

    def test_telegram_group_messages_cannot_use_corporate_identity(self):
        fake = FakeTelegram()
        result = TelegramUpdateHandler(messaging_provider=fake).handle({"message": {"message_id": 1, "from": {"id": "synthetic-a"}, "chat": {"id": "group", "type": "supergroup"}, "text": "Solicito materiales"}})
        self.assertEqual(result["action"], "ignored")
        self.assertFalse(ChannelMessage.objects.exists())
        self.assertEqual(fake.sent, [])
