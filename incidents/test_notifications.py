import io
from datetime import timedelta
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from incidents.models import Branch, Incident, NotificationOutbox, Technician, TechnicianChannel
from incidents.services.incident_notification_service import dispatch, queue_created_notification, detail_url
from incidents.services.incoming_message_service import IncomingMessageService
from incidents.services.operations import manage_report


@override_settings(EMAIL_NOTIFICATIONS_ENABLED=False, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", INCIDENT_NOTIFICATION_EMAIL="recipient@example.com", DEFAULT_FROM_EMAIL="Gym Planet Reportes <sender@example.com>", APP_BASE_URL="https://ops.example.com", AI_PROVIDER="mock", TELEGRAM_BOT_TOKEN="", GEMINI_API_KEY="")
class NotificationTests(TestCase):
    def setUp(self):
        self.assertEqual(connection.settings_dict["NAME"],"test_incidentes_gymplanet_db")
        self.branch=Branch.objects.create(code="TEST-MAIL",name="Sede de prueba")
        self.reporter=Technician.objects.create(full_name="Cuenta de prueba")
        TechnicianChannel.objects.create(branch=self.branch,technician=self.reporter,provider="telegram",external_user_id="test-mail")
        self.manager=get_user_model().objects.create_user(username="test-mail-manager",is_superuser=True)
        self.number=0

    def report(self,**kw):
        return Incident.objects.create(branch=self.branch,technician=self.reporter,equipment="Bicicleta",failure_type="Ruido",description="Descripción <script>no ejecutar</script>",priority=kw.pop("priority","low"),**kw)

    def send(self,text,mid=None):
        self.number+=1
        return IncomingMessageService().process_text(provider="telegram",external_user_id="test-mail",external_chat_id="test-mail",external_message_id=mid or str(self.number),text=text,raw_payload={},verified_transport=True)

    def deliver(self,row):
        with override_settings(EMAIL_NOTIFICATIONS_ENABLED=True): return dispatch(row.pk)

    def test_five_states(self):
        self.assertEqual(set(Incident.Status.values),{"reported","in_progress","resolved","closed","cancelled"})
        self.client.force_login(self.manager)
        response=self.client.get(reverse("dashboard"))
        self.assertNotContains(response,"En revisión")
        self.assertEqual(set(self.client.get("/api/v1/incidents/summary/").json()["by_status"]),set(Incident.Status.values))

    def test_all_priorities_and_types_queue_and_send(self):
        for kind in ("incident","request"):
            for priority in Incident.Priority.values:
                report=self.report(report_type=kind,priority=priority)
                self.assertTrue(self.deliver(report.creation_notification))
        self.assertEqual(len(mail.outbox),8)

    def test_no_email_before_final_confirmation_or_cancelled_draft(self):
        self.send("Área Cardio, bicicleta no enciende, impide entrenar, prioridad baja")
        self.assertFalse(NotificationOutbox.objects.exists())
        self.send("/cancelar")
        self.assertFalse(NotificationOutbox.objects.exists())

    def test_confirmed_message_is_deduplicated(self):
        self.send("Área Cardio, bicicleta no enciende, impide entrenar, prioridad baja")
        self.send("Sí",mid="confirmation")
        self.send("Sí",mid="confirmation")
        self.send("Sí",mid="repeat-confirmation")
        self.assertEqual(NotificationOutbox.objects.count(),1)
        row=NotificationOutbox.objects.get()
        self.assertTrue(self.deliver(row))
        self.assertFalse(self.deliver(row))
        self.assertEqual(len(mail.outbox),1)

    def test_changes_comments_reassignments_do_not_queue_again(self):
        report=self.report()
        for action,data in (("status",{"status":"in_progress"}),("comment",{"comment":"Seguimiento"}),("assign",{"responsible":"Técnico"}),("assign",{"responsible":"Empresa"})):
            manage_report(report.pk,self.manager,action,**data)
        self.assertEqual(NotificationOutbox.objects.count(),1)

    def test_rollback_removes_outbox_and_report(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.report()
            raise RuntimeError("Rollback de prueba")
        self.assertFalse(Incident.objects.exists())
        self.assertFalse(NotificationOutbox.objects.exists())

    def test_only_after_commit_callback(self):
        with patch("incidents.services.incident_notification_service.dispatch_safely") as mocked:
            with self.captureOnCommitCallbacks(execute=False) as callbacks, override_settings(EMAIL_NOTIFICATIONS_ENABLED=True):
                self.report()
                mocked.assert_not_called()
            self.assertEqual(len(callbacks),1)
            callbacks[0]()
            mocked.assert_called_once()

    def test_known_failure_preserves_report_and_retries_safely(self):
        report=self.report()
        with patch("incidents.services.incident_notification_service.EmailMultiAlternatives.send",side_effect=RuntimeError("SECRET MUST NOT APPEAR")):
            self.assertFalse(self.deliver(report.creation_notification))
        row=NotificationOutbox.objects.get()
        self.assertEqual(row.status,"failed")
        self.assertNotIn("SECRET",row.last_error)
        self.assertTrue(Incident.objects.filter(pk=report.pk).exists())
        self.assertFalse(self.deliver(row))
        NotificationOutbox.objects.filter(pk=row.pk).update(next_attempt_at=timezone.now()-timedelta(seconds=1))
        self.assertTrue(self.deliver(row))
        self.assertFalse(self.deliver(row))
        self.assertEqual(len(mail.outbox),1)

    def test_attempts_are_bounded(self):
        row=self.report().creation_notification
        NotificationOutbox.objects.filter(pk=row.pk).update(attempts=3,status="failed")
        self.assertFalse(self.deliver(row))

    def test_ambiguous_delivery_is_not_retried(self):
        row=self.report().creation_notification
        with patch("incidents.services.incident_notification_service.EmailMultiAlternatives.send",side_effect=TimeoutError("private details")):
            self.assertFalse(self.deliver(row))
        row.refresh_from_db()
        self.assertEqual(row.status,"sending")
        self.assertFalse(self.deliver(row))

    def test_rendered_email_sender_recipient_url_and_html(self):
        report=self.report()
        self.deliver(report.creation_notification)
        message=mail.outbox[0]
        self.assertEqual(message.to,["recipient@example.com"])
        self.assertEqual(message.from_email,settings.DEFAULT_FROM_EMAIL)
        url="https://ops.example.com"+reverse("report-detail",args=[report.pk])
        self.assertIn(url,message.body)
        html=message.alternatives[0].content
        self.assertIn("Ver reporte",html)
        self.assertIn(url,html)
        self.assertIn("#ab4e3c",html)
        self.assertIn("cid:gymplanet-brand",html)
        self.assertTrue(message.attachments)
        self.assertNotIn("<script>",html)

    def test_link_login_next_and_permissions(self):
        report=self.report()
        url=reverse("report-detail",args=[report.pk])
        response=self.client.get(url)
        self.assertIn("next=",response.url)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(url).status_code,200)
        ordinary=get_user_model().objects.create_user(username="test-mail-ordinary")
        self.client.force_login(ordinary)
        self.assertEqual(self.client.get(url).status_code,403)

    def test_base_url_requires_safe_origin(self):
        for base in ("", "javascript:alert(1)","https://user:secret@example.com","http://public.example.com"):
            with override_settings(APP_BASE_URL=base), self.assertRaises(ValueError): detail_url(1)

    def test_missing_configuration_never_calls_backend(self):
        row=self.report().creation_notification
        with override_settings(APP_BASE_URL=""), patch("incidents.services.incident_notification_service.EmailMultiAlternatives.send") as mocked:
            self.assertFalse(self.deliver(row))
            mocked.assert_not_called()

    def test_queue_idempotence_and_dry_command(self):
        report=self.report()
        queue_created_notification(report)
        self.assertEqual(NotificationOutbox.objects.count(),1)
        call_command("process_report_notifications",stdout=io.StringIO())
        self.assertEqual(NotificationOutbox.objects.get().attempts,0)

    def test_cancellation_requires_and_preserves_reason(self):
        report=self.report()
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError): manage_report(report.pk,self.manager,"status",status="cancelled")
        manage_report(report.pk,self.manager,"status",status="cancelled",reason="Duplicado informado")
        event=report.reportactivitys.get(action="cancelled")
        manage_report(report.pk,self.manager,"status",status="reported")
        self.assertEqual(report.reportactivitys.get(pk=event.pk).after["reason"],"Duplicado informado")

    def test_smtp_without_credentials_never_sends(self):
        row=self.report().creation_notification
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",EMAIL_HOST_USER="",EMAIL_HOST_PASSWORD=""), patch("incidents.services.incident_notification_service.EmailMultiAlternatives.send") as mocked:
            self.assertFalse(self.deliver(row))
            mocked.assert_not_called()

    def test_command_retries_due_failed_without_resending_success(self):
        row=self.report().creation_notification
        NotificationOutbox.objects.filter(pk=row.pk).update(status="failed",attempts=1)
        with override_settings(EMAIL_NOTIFICATIONS_ENABLED=True):
            for _ in range(2): call_command("process_report_notifications",apply=True,retry_failed=True,stdout=io.StringIO())
        self.assertEqual(len(mail.outbox),1)
        row.refresh_from_db()
        self.assertEqual(row.status,"sent")

    def test_login_redirects_to_original_report(self):
        from django.utils.crypto import get_random_string
        report=self.report()
        password=get_random_string(32)
        self.manager.set_password(password)
        self.manager.save()
        url=reverse("report-detail",args=[report.pk])
        response=self.client.post(reverse("operations-login"),{"username":self.manager.username,"password":password,"next":url})
        self.assertEqual(response.status_code,302)
        self.assertEqual(response.url,url)
        self.assertEqual(self.client.get(response.url).status_code,200)


class StatusMigrationTests(TransactionTestCase):
    def test_0021_changes_only_old_status(self):
        self.assertEqual(connection.settings_dict["NAME"],"test_incidentes_gymplanet_db")
        old=[("incidents","0020_operations_role_history")]
        executor=MigrationExecutor(connection)
        executor.migrate(old)
        try:
            apps=executor.loader.project_state(old).apps
            reporter=apps.get_model("incidents","Technician").objects.create(full_name="Cuenta histórica de prueba")
            report=apps.get_model("incidents","Incident").objects.create(technician_id=reporter.pk,status="under_review",equipment="Histórico",failure_type="Ruido",description="Preservar",priority="high")
            activity=apps.get_model("incidents","ReportActivity").objects.create(report_id=report.pk,action="under_review",description="Actividad histórica",after={"status":"under_review"})
            assignment=apps.get_model("incidents","ReportAssignment").objects.create(report_id=report.pk,responsible="Oficio histórico")
            executor=MigrationExecutor(connection)
            executor.migrate([("incidents","0021_alter_incident_status")])
            report.refresh_from_db()
            activity.refresh_from_db()
            assignment.refresh_from_db()
            self.assertEqual((report.status,report.description,report.priority),("in_progress","Preservar","high"))
            self.assertEqual(activity.after,{"status":"under_review"})
            self.assertEqual(assignment.responsible,"Oficio histórico")
        finally:
            executor=MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
