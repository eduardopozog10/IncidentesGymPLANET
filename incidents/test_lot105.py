from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from incidents.models import Branch, EquipmentCategory, GymAsset, Incident, Technician, ReportActivity
from incidents.services.inventory import save_asset
from incidents.services.operations import manage_report


@override_settings(AI_PROVIDER="mock", EMAIL_NOTIFICATIONS_ENABLED=False,
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   TELEGRAM_BOT_TOKEN="", GEMINI_API_KEY="")
class OperationalPolishTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="test-lot105")
        cls.user.user_permissions.add(Permission.objects.get(codename="manage_operations"))
        cls.branch = Branch.objects.create(code="TEST105", name="Sede de prueba")
        cls.other = Branch.objects.create(code="TEST105B", name="Sede de prueba")
        cls.reporter = Technician.objects.create(full_name="Reportante sintético")
        cls.category = EquipmentCategory.objects.create(code="TEST105", name="Categoría de prueba")

    def setUp(self):
        self.assertEqual(connection.settings_dict["NAME"], "test_incidentes_gymplanet_db")
        self.client.force_login(self.user)

    def report(self, **kwargs):
        data = dict(branch=self.branch, technician=self.reporter, description="Descripción sintética",
                    equipment="Trotadora", failure_type="No enciende", priority="high")
        data.update(kwargs)
        return Incident.objects.create(**data)

    def test_operational_lists_only_pending_for_both_types(self):
        for kind, name in (("incident", "dashboard-incidents"), ("request", "dashboard-requests")):
            reports = {status:self.report(report_type=kind, status=status) for status in Incident.Status.values}
            response = self.client.get(reverse(name))
            self.assertEqual({r.pk for r in response.context["page_obj"]}, {reports[s].pk for s in ("reported", "in_progress")})
            self.assertEqual(response.context["pending_count"], 2)
            self.assertContains(response, "Resultado de los filtros actuales")
            self.assertContains(response, "Consultar historial")
            # Neither a final-state filter nor a report_type parameter can expose history here.
            self.assertEqual(self.client.get(reverse(name), {"status":"closed"}).context["pending_count"], 0)

    def test_history_contains_every_state_and_detail_remains_accessible(self):
        reports = [self.report(status=status) for status in Incident.Status.values]
        response = self.client.get(reverse("dashboard-reports"))
        self.assertEqual(response.context["page_obj"].paginator.count, 5)
        for report in reports:
            self.assertContains(response, report.code)
            self.assertEqual(self.client.get(reverse("report-detail", args=[report.pk])).status_code, 200)

    def test_pending_count_and_pagination_obey_branch_priority_and_type(self):
        for _ in range(23): self.report()
        self.report(branch=self.other)
        self.report(report_type="request")
        self.report(priority="low")
        self.report(status="closed")
        response = self.client.get(reverse("dashboard-incidents"), {"branch":self.branch.pk, "priority":"high", "page":2})
        self.assertEqual(response.context["pending_count"], 23)
        self.assertEqual(len(response.context["page_obj"]), 3)
        self.assertContains(response, "branch=")

    def test_overview_four_primary_metrics_and_priority_queue(self):
        self.report(priority="high")
        critical = self.report(priority="critical")
        self.report(report_type="request", priority="critical")
        self.report(status="resolved", priority="critical")
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["metrics"]["urgent"], 2)
        self.assertEqual(response.context["metrics"]["pending_requests"], 1)
        self.assertEqual(response.context["review_reports"][0], critical)
        self.assertNotContains(response, "Promedio hasta resolución")
        self.assertNotContains(response, "Sin responsable (todos los estados)")
        self.assertEqual(response.content.decode().count('<article class="metric'), 4)

    def test_pending_branches_do_not_merge_identical_names(self):
        self.report()
        self.report(branch=self.other)
        rows = self.client.get(reverse("dashboard")).context["pending_by_branch"]
        self.assertEqual(len(rows), 2)
        filtered = self.client.get(reverse("dashboard"), {"branch":self.other.pk}).context["pending_by_branch"]
        self.assertEqual([row["branch_id"] for row in filtered], [self.other.pk])

    def test_request_cards_show_request_kind_without_rewriting_equipment(self):
        report = self.report(report_type="request", request_kind="reposición", equipment="Dato histórico")
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "reposición")
        self.assertNotContains(response, "Dato histórico")
        report.refresh_from_db()
        self.assertEqual(report.equipment, "Dato histórico")

    def test_missing_solution_has_field_error_and_preserves_report(self):
        report = self.report(report_type="request")
        before = report.reportactivitys.count()
        response = self.client.post(reverse("report-detail", args=[report.pk]), {"status":"resolved"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("solution", response.context["status_form"].errors)
        self.assertContains(response, "Registra la solución aplicada", status_code=400)
        self.assertNotContains(response, "Completa el dato requerido", status_code=400)
        report.refresh_from_db()
        self.assertEqual(report.status, "reported")
        self.assertEqual(report.reportactivitys.count(), before)

    def test_all_direct_state_changes_persist_for_incidents_and_requests(self):
        for kind in ("incident", "request"):
            report = self.report(report_type=kind)
            for source in Incident.Status.values:
                for target in Incident.Status.values:
                    Incident.objects.filter(pk=report.pk).update(status=source)
                    before = report.reportactivitys.count()
                    response = self.client.post(reverse("report-detail", args=[report.pk]),
                        {"status":target, "solution":"Solución sintética", "reason":"Motivo sintético"})
                    self.assertEqual(response.status_code, 302, (kind, source, target))
                    report.refresh_from_db()
                    self.assertEqual(report.status, target)
                    if source == target:
                        self.assertEqual(report.reportactivitys.count(), before)
                    else:
                        event = report.reportactivitys.first()
                        self.assertEqual((event.before["status"], event.after["status"], event.actor), (source, target, self.user))

    def test_same_resolved_state_without_solution_is_noop(self):
        report = self.report(status="resolved")
        before = report.reportactivitys.count()
        self.assertEqual(self.client.post(reverse("report-detail",args=[report.pk]), {"status":"resolved"}).status_code, 302)
        self.assertEqual(report.reportactivitys.count(), before)

    def test_cancel_requires_named_reason(self):
        report = self.report()
        response = self.client.post(reverse("report-detail",args=[report.pk]), {"status":"cancelled"})
        self.assertIn("reason", response.context["status_form"].errors)
        with self.assertRaisesMessage(ValidationError, "Motivo de cancelación"):
            manage_report(report.pk, self.user, "status", status="cancelled")

    def test_length_errors_keep_entered_solution(self):
        report = self.report()
        value = "x" * 4001
        response = self.client.post(reverse("report-detail", args=[report.pk]), {"status":"resolved", "solution":value})
        self.assertIn("solution", response.context["status_form"].errors)
        self.assertEqual(response.context["status_form"]["solution"].value(), value)
        with self.assertRaisesMessage(ValidationError, "Solución aplicada: admite como máximo 4000"):
            manage_report(report.pk, self.user, "status", status="resolved", solution=value)

    def test_location_duplicate_hidden_only_in_presentation(self):
        report = self.report(report_type="request", area="clases grupales", location_context="  CLASES   GRUPALES ")
        response = self.client.get(reverse("report-detail",args=[report.pk]))
        self.assertEqual(response.context["display_area"], "clases grupales")
        self.assertEqual(response.context["display_location"], "")
        report.refresh_from_db()
        self.assertEqual(report.location_context, "  CLASES   GRUPALES ")

    def test_distinct_location_is_preserved(self):
        report = self.report(area="Cardio", location_context="Segundo piso")
        response = self.client.get(reverse("report-detail",args=[report.pk]))
        self.assertContains(response, "Cardio · Segundo piso")

    def test_asset_dates_remain_optional_and_can_be_set_later(self):
        data = dict(code="TEST-ASSET", name="Equipo sintético", branch=self.branch.pk,
                    category=self.category.pk, operational_status="operational", is_active=True)
        form = save_asset(self.user, data)
        self.assertFalse(form.errors)
        asset = form.instance
        self.assertIsNone(asset.next_maintenance_at)
        self.assertEqual(self.client.get(reverse("maintenance-dashboard"), {"planning":"overdue"}).context["page_obj"].paginator.count, 0)
        self.assertContains(self.client.get(reverse("maintenance-dashboard")), "Sin mantenimiento programado")
        data["next_maintenance_at"] = (timezone.now()+timedelta(days=7)).isoformat()
        self.assertFalse(save_asset(self.user, data, asset.pk).errors)
        self.assertContains(self.client.get(reverse("maintenance-dashboard"), {"planning":"soon"}), asset.code)

    def test_inventory_navigation_shared_and_access_controlled(self):
        for name in ("dashboard-assets", "asset-create", "asset-categories", "maintenance-dashboard"):
            response = self.client.get(reverse(name))
            self.assertContains(response, 'aria-label="Gestión de inventario"')
            for target in ("asset-create", "asset-categories", "maintenance-dashboard"):
                self.assertContains(response, reverse(target))
        user = get_user_model().objects.create_user(username="test105-denied")
        self.client.force_login(user)
        for name in ("asset-create", "asset-categories", "maintenance-dashboard"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_planning_labels_and_filters_distinguish_null_past_future(self):
        now = timezone.now()
        for code, date in (("SIN-FECHA",None), ("VENCIDO",now-timedelta(days=2)), ("PROXIMO",now+timedelta(days=2))):
            GymAsset.objects.create(code=code, name="Activo sintético", branch=self.branch,
                category=self.category, next_maintenance_at=date)
        response = self.client.get(reverse("maintenance-dashboard"))
        for label in ("Sin mantenimiento programado", "Vencido", "Próximo"):
            self.assertContains(response, label)
        for mode, code in (("none","SIN-FECHA"), ("overdue","VENCIDO"), ("soon","PROXIMO")):
            rows = self.client.get(reverse("maintenance-dashboard"), {"planning":mode}).context["page_obj"]
            self.assertEqual([row.code for row in rows], [code])

    def test_state_change_still_requires_csrf_and_authorization(self):
        report = self.report()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse("report-detail",args=[report.pk]), {"status":"closed"}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("report-detail",args=[report.pk]), {"status":"closed"}).status_code, 302)
