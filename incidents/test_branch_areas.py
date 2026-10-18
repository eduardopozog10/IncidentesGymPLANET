from django.contrib import admin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse

from incidents.models import Branch, BranchArea, Incident, Technician


class AreaFixtures(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(name="Sucursal de prueba A", code="TEST-A")
        cls.other_branch = Branch.objects.create(name="Sucursal de prueba B", code="TEST-B")
        cls.area = BranchArea.objects.create(branch=cls.branch, name="Área de prueba", code="AREA")
        cls.other_area = BranchArea.objects.create(branch=cls.other_branch, name="Otra área", code="AREA")
        cls.reporter = Technician.objects.create(full_name="Reportante de prueba")

    def incident_values(self, **kwargs):
        values = {
            "technician": self.reporter,
            "unit": "Unidad de prueba",
            "area": "Texto histórico sin transformar",
            "equipment": "Equipo de prueba",
            "failure_type": "Falla de prueba",
            "description": "Descripción de prueba",
            "original_message": "Mensaje de prueba",
        }
        values.update(kwargs)
        return values

    def create_incident(self, **kwargs):
        return Incident.objects.create(**self.incident_values(**kwargs))


class BranchAreaTests(AreaFixtures):
    def test_areas_are_independent_and_codes_can_repeat_across_branches(self):
        self.assertEqual(list(self.branch.areas.all()), [self.area])
        self.assertEqual(list(self.other_branch.areas.all()), [self.other_area])
        self.assertTrue(self.area.is_active)
        self.assertIsNotNone(self.area.created_at)
        self.assertIsNotNone(self.area.updated_at)
        self.assertIn(self.branch.code, str(self.area))

    def test_code_is_unique_within_branch_in_validation_and_database(self):
        duplicate = BranchArea(branch=self.branch, name="Área duplicada", code="AREA")
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save()

    def test_branch_name_and_code_are_required(self):
        for field in ("branch", "name", "code"):
            with self.subTest(field=field):
                values = {"branch": self.branch, "name": "Área", "code": "NEW"}
                values[field] = None if field == "branch" else ""
                with self.assertRaises(ValidationError):
                    BranchArea(**values).full_clean()

    def test_used_area_cannot_be_deleted(self):
        incident = self.create_incident(branch=self.branch, branch_area=self.area)
        with self.assertRaises(ProtectedError):
            self.area.delete()
        incident.refresh_from_db()
        self.assertEqual(incident.branch_area_id, self.area.pk)

    def test_branch_with_areas_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError):
            self.branch.delete()
        self.assertTrue(BranchArea.objects.filter(pk=self.area.pk).exists())

    def test_deactivating_area_preserves_history(self):
        incident = self.create_incident(branch=self.branch, branch_area=self.area)
        self.area.is_active = False
        self.area.save()
        incident.refresh_from_db()
        incident.full_clean()
        self.assertFalse(incident.branch_area.is_active)
        self.assertEqual(incident.area, "Texto histórico sin transformar")

    def test_used_area_cannot_move_to_another_branch(self):
        self.create_incident(branch=self.branch, branch_area=self.area)
        self.area.branch = self.other_branch
        self.area.code = "MOVED"
        with self.assertRaises(ValidationError) as error:
            self.area.full_clean()
        self.assertIn("branch", error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            BranchArea.objects.filter(pk=self.area.pk).update(
                branch=self.other_branch, code="MOVED",
            )


class IncidentAreaAndTypeTests(AreaFixtures):
    def test_legacy_incident_keeps_text_area_and_defaults_to_incident(self):
        incident = self.create_incident()
        incident.full_clean()
        incident.refresh_from_db()
        self.assertIsNone(incident.branch_id)
        self.assertIsNone(incident.branch_area_id)
        self.assertEqual(incident.report_type, Incident.ReportType.INCIDENT)
        self.assertEqual(incident.area, "Texto histórico sin transformar")

    def test_branch_without_structured_area_is_allowed(self):
        incident = self.create_incident(branch=self.branch)
        incident.full_clean()
        self.assertIsNone(incident.branch_area_id)

    def test_matching_area_can_be_assigned_without_overwriting_legacy_text(self):
        incident = self.create_incident()
        incident.branch, incident.branch_area = self.branch, self.area
        incident.full_clean()
        incident.save()
        incident.refresh_from_db()
        self.assertEqual(list(self.area.incidents.all()), [incident])
        self.assertEqual(incident.area, "Texto histórico sin transformar")

    def test_cross_branch_area_is_rejected_by_model_validation(self):
        incident = Incident(**self.incident_values(branch=self.branch, branch_area=self.other_area))
        with self.assertRaises(ValidationError) as error:
            incident.full_clean()
        self.assertIn("branch_area", error.exception.message_dict)

    def test_cross_branch_area_is_rejected_on_save_without_full_clean(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_incident(branch=self.branch, branch_area=self.other_area)

    def test_area_without_branch_is_rejected_by_validation_and_database(self):
        incident = Incident(**self.incident_values(branch_area=self.area))
        with self.assertRaises(ValidationError) as error:
            incident.full_clean()
        self.assertIn("branch_area", error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            incident.save()

    def test_bulk_update_cannot_change_only_incident_branch(self):
        incident = self.create_incident(branch=self.branch, branch_area=self.area)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Incident.objects.filter(pk=incident.pk).update(branch=self.other_branch)

    def test_bulk_create_cannot_bypass_branch_area_integrity(self):
        incident = Incident(**self.incident_values(branch=self.branch, branch_area=self.other_area))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Incident.objects.bulk_create([incident])

    def test_branch_cannot_be_cleared_while_area_is_assigned(self):
        incident = self.create_incident(branch=self.branch, branch_area=self.area)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Incident.objects.filter(pk=incident.pk).update(branch=None)

    def test_operational_request_uses_existing_states_and_priority(self):
        incident = self.create_incident(
            branch=self.branch, branch_area=self.area,
            report_type=Incident.ReportType.REQUEST,
        )
        incident.full_clean()
        incident.refresh_from_db()
        self.assertEqual(incident.report_type, "request")
        self.assertEqual(incident.status, Incident.Status.REPORTED)
        self.assertEqual(incident.priority, Incident.Priority.MEDIUM)

    def test_invalid_report_type_is_rejected_by_validation_and_database(self):
        incident = Incident(**self.incident_values(report_type="invalid"))
        with self.assertRaises(ValidationError) as error:
            incident.full_clean()
        self.assertIn("report_type", error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            incident.save()


class BranchAreaAdminTests(AreaFixtures):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin_user = User.objects.create(
            username="area-admin-test", is_staff=True, is_superuser=True,
        )

    def incident_form(self, **overrides):
        request = RequestFactory().get("/admin/")
        request.user = self.admin_user
        form_class = admin.site.get_model_admin(Incident).get_form(request)
        data = {
            "technician": self.reporter.pk,
            "branch": self.branch.pk,
            "branch_area": self.area.pk,
            "report_type": "request",
            "status": "reported",
            "priority": "medium",
            "unit": "Unidad de prueba",
            "area": "Área histórica",
            "equipment": "Equipo de prueba",
            "failure_type": "Falla de prueba",
            "description": "Descripción",
            "original_message": "Mensaje original",
        }
        data.update(overrides)
        return form_class(data=data)

    def test_admin_accepts_matching_branch_area_and_request(self):
        form = self.incident_form()
        self.assertTrue(form.is_valid(), form.errors)
        incident = form.save()
        self.assertEqual(incident.branch_area, self.area)
        self.assertEqual(incident.report_type, "request")

    def test_admin_rejects_cross_branch_area_and_filters_bound_choices(self):
        form = self.incident_form(branch_area=self.other_area.pk)
        self.assertEqual(list(form.fields["branch_area"].queryset), [self.area])
        self.assertFalse(form.is_valid())
        self.assertIn("branch_area", form.errors)

    def test_admin_rejects_area_without_branch(self):
        form = self.incident_form(branch="")
        self.assertFalse(form.is_valid())
        self.assertIn("branch_area", form.errors)

    def test_admin_invalid_branch_input_does_not_crash(self):
        form = self.incident_form(branch="not-a-number")
        self.assertFalse(form.is_valid())
        self.assertIn("branch", form.errors)

    def test_admin_accepts_legacy_incident_without_structured_area(self):
        form = self.incident_form(branch="", branch_area="", report_type="incident")
        self.assertTrue(form.is_valid(), form.errors)
        incident = form.save()
        self.assertIsNone(incident.branch_area_id)
        self.assertEqual(incident.area, "Área histórica")

    def test_admin_keeps_inactive_historical_area_available_on_change(self):
        incident = self.create_incident(branch=self.branch, branch_area=self.area)
        self.area.is_active = False
        self.area.save()
        request = RequestFactory().get("/admin/")
        request.user = self.admin_user
        form = admin.site.get_model_admin(Incident).get_form(request, obj=incident)(instance=incident)
        self.assertIn(self.area, form.fields["branch_area"].queryset)
        self.assertNotIn(self.other_area, form.fields["branch_area"].queryset)

    def test_area_admin_prevents_reassigning_used_area(self):
        self.create_incident(branch=self.branch, branch_area=self.area)
        request = RequestFactory().get("/admin/")
        request.user = self.admin_user
        form_class = admin.site.get_model_admin(BranchArea).get_form(request, obj=self.area)
        form = form_class(instance=self.area, data={
            "branch": self.other_branch.pk, "name": self.area.name,
            "code": "MOVED", "is_active": True,
        })
        self.assertFalse(form.is_valid())
        self.assertIn("branch", form.errors)

    def test_area_admin_search_and_branch_filter(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin:incidents_brancharea_changelist"), {
            "q": "TEST-A", "branch__id__exact": self.branch.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["cl"].queryset), [self.area])

    def test_incident_admin_report_type_filter(self):
        request = self.create_incident(report_type="request")
        self.create_incident()
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin:incidents_incident_changelist"), {
            "report_type__exact": "request",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["cl"].queryset), [request])

    def test_admin_delete_view_protects_used_area(self):
        incident = self.create_incident(branch=self.branch, branch_area=self.area)
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("admin:incidents_brancharea_delete", args=[self.area.pk]),
            {"post": "yes"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["protected"])
        self.assertTrue(BranchArea.objects.filter(pk=self.area.pk).exists())
        self.assertTrue(Incident.objects.filter(pk=incident.pk).exists())


class BranchAreaMigrationTests(TransactionTestCase):
    def test_migration_preserves_legacy_incident_and_text_area(self):
        # Schema changes in this test are explicitly restricted to the test DB.
        self.assertEqual(connection.settings_dict["NAME"], "test_incidentes_gymplanet_db")
        previous = [("incidents", "0011_branch_incident_branch")]
        target = [("incidents", "0012_branch_areas_report_type")]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(previous)
            old_apps = executor.loader.project_state(previous).apps
            reporter = old_apps.get_model("incidents", "Technician").objects.create(
                full_name="Reportante histórico de prueba",
            )
            old_incident = old_apps.get_model("incidents", "Incident").objects.create(
                technician=reporter, unit="Unidad histórica", area="Texto histórico",
                equipment="Equipo de prueba", failure_type="Síntoma de prueba",
                description="Descripción histórica", original_message="Mensaje histórico",
            )
            executor = MigrationExecutor(connection)
            executor.migrate(target)
            apps = executor.loader.project_state(target).apps
            incident = apps.get_model("incidents", "Incident").objects.get(pk=old_incident.pk)
            self.assertEqual(incident.area, "Texto histórico")
            self.assertEqual(incident.description, "Descripción histórica")
            self.assertEqual(incident.report_type, "incident")
            self.assertIsNone(incident.branch_area_id)
            self.assertIsNone(incident.branch_id)
            self.assertEqual(apps.get_model("incidents", "BranchArea").objects.count(), 0)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
