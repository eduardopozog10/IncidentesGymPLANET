from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, connection, transaction
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from incidents.models import Branch, Incident, Technician, ReportActivity, ReportAssignment
from incidents.services.operations import manage_report, can_operate


@override_settings(AI_PROVIDER="mock", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", TELEGRAM_BOT_TOKEN="", GEMINI_API_KEY="")
class OperationsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group, _ = Group.objects.get_or_create(name="operations_manager")
        cls.group.permissions.add(Permission.objects.get(codename="manage_operations"))
        cls.manager = get_user_model().objects.create_user(username="operations-test")
        cls.manager.groups.add(cls.group)
        cls.ordinary = get_user_model().objects.create_user(username="ordinary-test",is_staff=True)
        cls.root = get_user_model().objects.create_user(username="root-test",is_staff=True,is_superuser=True)
        cls.branch = Branch.objects.create(code="TEST-OPS",name="Sede de prueba")
        cls.reporter = Technician.objects.create(full_name="Cuenta de prueba")
        cls.report = Incident.objects.create(branch=cls.branch,technician=cls.reporter,equipment="Bicicleta",failure_type="Ruido",description="Reporte de prueba",priority="high")

    def setUp(self):
        self.assertEqual(connection.settings_dict["NAME"],"test_incidentes_gymplanet_db")
        self.client.force_login(self.manager)
        self.url=reverse("report-detail",args=[self.report.pk])

    def act(self, action, **data):
        return manage_report(self.report.pk,self.manager,action,**data)

    def resolve(self):
        for status in ("under_review","in_progress"):
            self.act("status",status=status)
        return self.act("status",status="resolved",solution="Se reparó el equipo.")

    def test_group_has_only_operational_permission(self):
        self.assertEqual(list(self.group.permissions.values_list("codename",flat=True)),["manage_operations"])
        self.assertFalse(self.manager.is_superuser)
        self.assertFalse(self.manager.is_staff)
        self.assertFalse(self.manager.has_perm("auth.change_user"))

    def test_manager_all_pages_and_future_branches(self):
        future=Branch.objects.create(code="TEST-FUTURE",name="Sede futura de prueba")
        for name in ("dashboard","dashboard-incidents","dashboard-requests","dashboard-assets","dashboard-branches"):
            self.assertEqual(self.client.get(reverse(name)).status_code,200)
        self.assertContains(self.client.get(reverse("dashboard-branches")),future.name)

    def test_technical_admin_and_transport_denied(self):
        self.assertEqual(self.client.get("/admin/auth/user/").status_code,302)
        self.assertFalse(self.manager.has_perm("auth.add_user"))

    def test_unauthorized_direct_http_and_service(self):
        self.client.force_login(self.ordinary)
        self.assertEqual(self.client.post(self.url,{"action":"assign","responsible":"Gasfíter"}).status_code,403)
        with self.assertRaises(PermissionDenied): manage_report(self.report.pk,self.ordinary,"comment",comment="No permitido")

    def test_inactive_role_denied(self):
        self.manager.is_active=False
        self.assertFalse(can_operate(self.manager))

    def test_superuser_retains_access(self):
        self.client.force_login(self.root)
        self.assertEqual(self.client.get(self.url).status_code,200)

    def test_assignment_without_person_creates_no_users(self):
        count=get_user_model().objects.count()
        self.act("assign",responsible="Gasfíter")
        self.assertEqual(ReportAssignment.objects.get().person_name,"")
        self.assertEqual(get_user_model().objects.count(),count)

    def test_assignment_with_name_and_reassignment_history(self):
        self.act("assign",responsible="Gasfíter",person_name="Persona de prueba")
        self.act("assign",responsible="Empresa de mantenimiento",notes="Revisar filtración")
        self.assertEqual(ReportAssignment.objects.count(),2)
        event=ReportActivity.objects.filter(action="reassigned").get()
        self.assertEqual(event.before["responsible"],"Gasfíter")
        self.assertEqual(event.actor,self.manager)

    def test_empty_assignment_rejected(self):
        with self.assertRaises(ValidationError): self.act("assign",responsible="   ")
        self.assertFalse(ReportAssignment.objects.exists())

    def test_state_cycle_resolution_close_reopen(self):
        report=self.resolve()
        resolved=report.resolved_at
        report=self.act("status",status="closed")
        self.assertEqual(report.resolved_at,resolved)
        self.assertEqual(ReportActivity.objects.filter(action="solution").count(),1)
        report=self.act("status",status="under_review",reason="Se repitió la falla")
        self.assertIsNone(report.resolved_at)
        self.assertTrue(ReportActivity.objects.filter(action="reopened").exists())

    def test_unknown_state_is_rejected_without_history(self):
        count=ReportActivity.objects.count()
        with self.assertRaises(ValidationError): self.act("status",status="unknown")
        self.assertEqual(ReportActivity.objects.count(),count)

    def test_solution_required(self):
        self.act("status",status="under_review")
        self.act("status",status="in_progress")
        with self.assertRaises(ValidationError): self.act("status",status="resolved",solution=" ")
        self.assertFalse(ReportActivity.objects.filter(action="solution").exists())

    def test_reopen_does_not_require_reason(self):
        self.resolve()
        report=self.act("status",status="under_review")
        self.assertEqual(report.status,"under_review")
        self.assertIsNone(report.resolved_at)

    def test_comment_author_and_escaped_display(self):
        self.act("comment",comment="<script>test</script>")
        row=ReportActivity.objects.get(action="comment")
        self.assertEqual(row.actor,self.manager)
        self.assertContains(self.client.get(self.url),"&lt;script&gt;")

    def test_empty_comment_rejected(self):
        with self.assertRaises(ValidationError): self.act("comment",comment=" ")

    def test_history_database_update_delete_blocked(self):
        event=ReportActivity.objects.first()
        for method in (lambda:ReportActivity.objects.filter(pk=event.pk).update(description="Alterado"),lambda:ReportActivity.objects.filter(pk=event.pk).delete()):
            with self.assertRaises(DatabaseError), transaction.atomic(): method()
        self.act("assign",responsible="Técnico")
        with self.assertRaises(DatabaseError), transaction.atomic(): ReportAssignment.objects.all().update(responsible="Otro")

    def test_csrf_and_successful_assignment(self):
        client=Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        self.assertEqual(client.post(self.url,{"action":"comment","comment":"Avance"}).status_code,403)
        client.get(self.url)
        response=client.post(self.url,{"action":"assign","responsible":"Técnico","actor":self.ordinary.pk},HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code,302)
        self.assertEqual(ReportAssignment.objects.get().actor,self.manager)

    def test_request_uses_same_workflow_and_code_preserved(self):
        self.report.report_type="request"
        self.report.code_prefix="SC"
        self.report.save()
        code=self.report.code
        report=self.resolve()
        self.assertEqual(report.code,code)
        self.assertEqual(report.report_type,"request")

    def test_creation_event_only_new_reports(self):
        self.assertEqual(ReportActivity.objects.filter(action="created",report=self.report).count(),1)
        self.report.save()
        self.assertEqual(ReportActivity.objects.filter(action="created",report=self.report).count(),1)

    def test_statistics(self):
        self.resolve()
        metrics=self.client.get(reverse("dashboard")).context["metrics"]
        self.assertEqual(metrics["resolved"],1)
        self.assertEqual(metrics["unassigned"],1)
        self.assertIsNotNone(metrics["average_resolution"])

    def test_operational_login_does_not_require_staff(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("operations-login")).status_code,200)

    def test_manager_can_update_api_with_same_rules(self):
        url=f"/api/v1/incidents/{self.report.pk}/"
        self.assertEqual(self.client.patch(url,{"status":"closed"},content_type="application/json").status_code,200)
        self.assertEqual(self.client.patch(url,{"status":"under_review"},content_type="application/json").status_code,200)
        self.assertEqual(ReportActivity.objects.filter(action="reopened").get().actor,self.manager)

    def test_staff_without_role_cannot_read_api(self):
        self.client.force_login(self.ordinary)
        self.assertEqual(self.client.get(f"/api/v1/incidents/{self.report.pk}/").status_code,403)

    def test_invalid_state_preserves_submitted_solution(self):
        response=self.client.post(self.url,{"status":"unknown","solution":"Texto todavía no guardado"})
        self.assertEqual(response.status_code,400)
        self.assertContains(response,"Texto todavía no guardado",status_code=400)
        self.assertFalse(ReportActivity.objects.filter(action="solution").exists())

    def test_all_five_states_are_free_for_both_report_types(self):
        states=["reported","under_review","in_progress","resolved","closed"]
        for kind in ("incident","request"):
            for source in states:
                for target in states:
                    with self.subTest(kind=kind,source=source,target=target):
                        report=Incident.objects.create(technician=self.reporter,report_type=kind,equipment="Equipo",failure_type="Falla",description="Prueba",priority="low",status=source)
                        count=report.reportactivitys.count()
                        result=manage_report(report.pk,self.manager,"status",status=target,solution="Solución explícita")
                        self.assertEqual(result.status,target)
                        events=report.reportactivitys.exclude(action="created")
                        if source==target:
                            self.assertEqual(report.reportactivitys.count(),count)
                        else:
                            change=events.exclude(action="solution").get()
                            self.assertEqual(change.before,{"status":source})
                            self.assertEqual(change.after,{"status":target})
                            self.assertEqual(change.actor,self.manager)
                            self.assertIsNotNone(change.created_at)

    def test_direct_resolution_requires_solution_and_keeps_assignment(self):
        self.act("assign",responsible="Gasfíter")
        original=ReportAssignment.objects.get()
        with self.assertRaises(ValidationError): self.act("status",status="resolved")
        self.act("status",status="resolved",solution="Se reparó la falla")
        self.assertEqual(ReportAssignment.objects.get().pk,original.pk)
        self.assertEqual(ReportActivity.objects.get(action="solution").description,"Se reparó la falla")

    def test_direct_close_has_no_invented_resolution_date(self):
        report=self.act("status",status="closed")
        self.assertIsNone(report.resolved_at)
        self.assertFalse(ReportActivity.objects.filter(action="solution").exists())

    def test_same_state_is_noop_including_resolved(self):
        for target in ("reported","resolved","closed"):
            if target!="reported": self.act("status",status=target,solution="Solución real")
            self.report.refresh_from_db()
            before=(self.report.updated_at,self.report.resolved_at,ReportActivity.objects.count())
            self.act("status",status=target)
            self.report.refresh_from_db()
            self.assertEqual(before,(self.report.updated_at,self.report.resolved_at,ReportActivity.objects.count()))

    def test_history_and_solution_survive_backward_changes(self):
        self.act("status",status="resolved",solution="Primera solución")
        ids=list(ReportActivity.objects.values_list("pk",flat=True))
        self.act("status",status="closed")
        self.act("status",status="reported")
        self.assertEqual(ReportActivity.objects.filter(pk__in=ids).count(),len(ids))
        self.assertEqual(ReportActivity.objects.get(action="solution").description,"Primera solución")
        self.client.force_login(self.ordinary)
        self.assertEqual(self.client.post(self.url,{"status":"closed"}).status_code,403)
