from datetime import timedelta

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from incidents.models import (
    AssetMovement, Branch, BranchArea, EquipmentCategory, GymAsset, Incident,
    MaintenanceRecord, RequestCategory, Technician,
)


class InventoryFixtures(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(code="TEST-A", name="Sucursal de prueba A")
        cls.other_branch = Branch.objects.create(code="TEST-B", name="Sucursal de prueba B")
        cls.area = BranchArea.objects.create(branch=cls.branch, code="AREA", name="Área A")
        cls.second_area = BranchArea.objects.create(branch=cls.branch, code="AREA-2", name="Área A2")
        cls.other_area = BranchArea.objects.create(branch=cls.other_branch, code="AREA", name="Área B")
        cls.category = EquipmentCategory.objects.create(code="TEST-EQ", name="Categoría de prueba")
        cls.request_category = RequestCategory.objects.create(code="TEST-REQ", name="Solicitud de prueba")
        cls.asset = GymAsset.objects.create(
            branch=cls.branch, branch_area=cls.area, category=cls.category,
            code="ASSET-1", name="Activo de prueba", brand="Marca de prueba", model="Modelo de prueba",
        )
        cls.other_asset = GymAsset.objects.create(
            branch=cls.other_branch, branch_area=cls.other_area, category=cls.category,
            code="ASSET-1", name="Otro activo de prueba",
        )
        cls.reporter = Technician.objects.create(full_name="Reportante de prueba")

    def incident_values(self, **overrides):
        values = dict(
            technician=self.reporter, branch=self.branch, branch_area=self.area,
            equipment="Equipo histórico", area="Área histórica", location_context="Contexto histórico",
            unit="Unidad de prueba", failure_type="Síntoma de prueba", description="Descripción",
            original_message="Mensaje histórico",
        )
        values.update(overrides)
        return values

    def incident(self, **overrides):
        return Incident.objects.create(**self.incident_values(**overrides))

    def maintenance(self, **overrides):
        values = dict(asset=self.asset, maintenance_type="preventive", description="Trabajo de prueba")
        values.update(overrides)
        return MaintenanceRecord.objects.create(**values)

    def assert_database_rejects(self, operation):
        with self.assertRaises(IntegrityError), transaction.atomic():
            operation()


class CategoryTests(InventoryFixtures):
    def test_categories_are_configurable_without_seed_data(self):
        self.assertEqual(EquipmentCategory.objects.count(), 1)
        self.assertEqual(RequestCategory.objects.count(), 1)
        self.assertTrue(self.category.is_active)
        self.assertEqual(self.category.description, "")
        self.assertIsNotNone(self.request_category.created_at)
        self.assertIn("TEST-EQ", str(self.category))

    def test_category_codes_are_unique_in_each_catalog(self):
        for model, code in [(EquipmentCategory, "TEST-EQ"), (RequestCategory, "TEST-REQ")]:
            with self.subTest(model=model.__name__):
                duplicate = model(code=code, name="Duplicada")
                with self.assertRaises(ValidationError):
                    duplicate.full_clean()
                self.assert_database_rejects(duplicate.save)

    def test_inactive_equipment_category_preserves_assets(self):
        self.category.is_active = False
        self.category.save()
        self.asset.refresh_from_db()
        self.asset.full_clean()
        self.assertFalse(self.asset.category.is_active)
        self.assertTrue(self.asset.is_active)
        with self.assertRaises(ProtectedError):
            self.category.delete()

    def test_inactive_request_category_preserves_request(self):
        request = self.incident(report_type="request", request_category=self.request_category)
        self.request_category.is_active = False
        self.request_category.save()
        request.refresh_from_db()
        request.full_clean()
        self.assertFalse(request.request_category.is_active)
        with self.assertRaises(ProtectedError):
            self.request_category.delete()


class GymAssetTests(InventoryFixtures):
    def test_asset_fields_and_optional_details(self):
        self.asset.full_clean()
        self.assertEqual(self.asset.operational_status, "operational")
        self.assertTrue(self.asset.is_active)
        self.assertEqual(self.asset.serial_number, "")
        self.assertEqual(self.asset.description, "")
        self.assertIsNotNone(self.asset.created_at)
        self.assertIn("TEST-A / ASSET-1", str(self.asset))
        asset = GymAsset(branch=self.branch, category=self.category, code="NO-AREA", name="Sin área")
        asset.full_clean()
        asset.save()
        self.assertIsNone(asset.branch_area_id)

    def test_branch_and_category_are_required(self):
        with self.assertRaises(ValidationError):
            GymAsset(code="MISSING", name="Incompleto").full_clean()

    def test_asset_codes_are_unique_per_branch_and_can_repeat_between_branches(self):
        self.assertEqual(self.asset.code, self.other_asset.code)
        duplicate = GymAsset(branch=self.branch, category=self.category, code=self.asset.code, name="Duplicado")
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
        self.assert_database_rejects(duplicate.save)

    def test_cross_branch_area_is_rejected_in_validation_and_bulk_update(self):
        self.asset.branch_area = self.other_area
        with self.assertRaises(ValidationError):
            self.asset.full_clean()
        self.assert_database_rejects(lambda: GymAsset.objects.filter(pk=self.asset.pk).update(branch_area=self.other_area))

    def test_bulk_create_cannot_bypass_area_integrity(self):
        asset = GymAsset(branch=self.branch, branch_area=self.other_area, category=self.category, code="BAD", name="Inconsistente")
        self.assert_database_rejects(lambda: GymAsset.objects.bulk_create([asset]))

    def test_all_operational_states_are_available_and_invalid_state_is_rejected(self):
        for status in GymAsset.OperationalStatus.values:
            self.asset.operational_status = status
            self.asset.full_clean()
            self.asset.save()
        self.asset.operational_status = "invalid"
        with self.assertRaises(ValidationError):
            self.asset.full_clean()
        self.assert_database_rejects(self.asset.save)

    def test_deactivation_preserves_referenced_incidents(self):
        incident = self.incident(gym_asset=self.asset)
        self.asset.deactivate()
        incident.refresh_from_db()
        self.assertFalse(incident.gym_asset.is_active)
        self.assertEqual(incident.equipment, "Equipo histórico")
        with self.assertRaises(ProtectedError):
            self.asset.delete()

    def test_explicit_transfer_preserves_incidents_and_maintenance_history(self):
        incident = self.incident(gym_asset=self.asset)
        record = self.maintenance(incident=incident)
        self.asset.transfer_to(self.other_branch, branch_area=self.other_area, code="MOVED")
        movement = self.asset.movements.get()
        self.assertEqual(movement.from_branch, self.branch)
        self.assertEqual(movement.to_branch, self.other_branch)
        self.assertEqual(movement.from_code, "ASSET-1")
        self.assertEqual(movement.to_code, "MOVED")
        incident.refresh_from_db()
        record.refresh_from_db()
        self.assertEqual(incident.branch, self.branch)
        self.assertEqual(incident.branch_area, self.area)
        self.assertEqual(incident.equipment, "Equipo histórico")
        self.assertEqual(record.branch, self.branch)
        incident.description = "Nota histórica revisada"
        incident.full_clean()
        incident.save()
        record.observations = "Nota adicional"
        record.full_clean()
        record.save()

    def test_transfer_collision_rolls_back_without_history(self):
        with self.assertRaises(ValidationError):
            self.asset.transfer_to(self.other_branch, branch_area=self.other_area)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.branch, self.branch)
        self.assertFalse(self.asset.movements.exists())

    def test_transfer_rejects_mismatched_area(self):
        with self.assertRaises(ValidationError):
            self.asset.transfer_to(self.other_branch, branch_area=self.area, code="NEW")

    def test_bulk_movement_is_audited(self):
        GymAsset.objects.filter(pk=self.asset.pk).update(branch=self.other_branch, branch_area=None, code="BULK")
        movement = AssetMovement.objects.get(asset=self.asset)
        self.assertEqual(movement.from_area, self.area)
        self.assertIsNone(movement.to_area_id)
        self.assertEqual(movement.to_branch, self.other_branch)

    def test_movement_history_cannot_be_modified_or_deleted(self):
        self.asset.transfer_to(self.branch, branch_area=self.second_area)
        movement = self.asset.movements.get()
        self.assert_database_rejects(lambda: AssetMovement.objects.filter(pk=movement.pk).update(to_code="FORGED"))
        self.assert_database_rejects(lambda: AssetMovement.objects.filter(pk=movement.pk).delete())

    def test_historical_area_cannot_move_to_another_branch(self):
        self.asset.transfer_to(self.branch, branch_area=self.second_area)
        self.area.branch = self.other_branch
        self.area.code = "OLD-AREA"
        with self.assertRaises(ValidationError):
            self.area.full_clean()
        self.assert_database_rejects(lambda: BranchArea.objects.filter(pk=self.area.pk).update(branch=self.other_branch, code="OLD-AREA"))


class IncidentAssetAndRequestTests(InventoryFixtures):
    def test_incident_without_asset_preserves_historical_fields(self):
        incident = self.incident(branch=None, branch_area=None)
        incident.full_clean()
        self.assertIsNone(incident.gym_asset_id)
        self.assertEqual(incident.area, "Área histórica")
        self.assertEqual(incident.equipment, "Equipo histórico")
        self.assertEqual(incident.location_context, "Contexto histórico")

    def test_matching_asset_does_not_change_priority_or_status(self):
        incident = self.incident(gym_asset=self.asset, priority="high", status="in_progress")
        incident.full_clean()
        self.assertEqual(incident.priority, "high")
        self.assertEqual(incident.status, "in_progress")

    def test_cross_branch_asset_rejected_by_model_and_database(self):
        incident = Incident(**self.incident_values(gym_asset=self.other_asset))
        with self.assertRaises(ValidationError):
            incident.full_clean()
        self.assert_database_rejects(incident.save)

    def test_asset_requires_incident_branch(self):
        incident = Incident(**self.incident_values(branch=None, branch_area=None, gym_asset=self.asset))
        with self.assertRaises(ValidationError):
            incident.full_clean()
        self.assert_database_rejects(incident.save)

    def test_mismatched_area_rejected_in_model_and_bulk_insert(self):
        incident = Incident(**self.incident_values(branch_area=self.second_area, gym_asset=self.asset))
        with self.assertRaises(ValidationError):
            incident.full_clean()
        self.assert_database_rejects(lambda: Incident.objects.bulk_create([incident]))

    def test_unspecified_asset_or_report_area_is_allowed(self):
        self.incident(gym_asset=self.asset, branch_area=None).full_clean()
        self.asset.branch_area = None
        self.asset.save()
        self.incident(gym_asset=self.asset).full_clean()

    def test_bulk_update_revalidates_changed_asset(self):
        incident = self.incident()
        self.assert_database_rejects(lambda: Incident.objects.filter(pk=incident.pk).update(gym_asset=self.other_asset))

    def test_request_category_without_asset_is_supported(self):
        request = self.incident(report_type="request", request_category=self.request_category, gym_asset=None)
        request.full_clean()
        self.assertIsNone(request.gym_asset_id)
        self.assertEqual(list(self.request_category.requests.all()), [request])

    def test_request_can_omit_category_and_asset(self):
        self.incident(report_type="request", branch=None, branch_area=None).full_clean()

    def test_only_requests_can_have_request_category(self):
        incident = Incident(**self.incident_values(request_category=self.request_category))
        with self.assertRaises(ValidationError):
            incident.full_clean()
        self.assert_database_rejects(incident.save)
        request = self.incident(report_type="request", request_category=self.request_category)
        self.assert_database_rejects(lambda: Incident.objects.filter(pk=request.pk).update(report_type="incident"))

    def test_invalid_priority_rejected_without_automatic_assignment(self):
        incident = Incident(**self.incident_values(priority="urgent"))
        with self.assertRaises(ValidationError):
            incident.full_clean()
        self.assert_database_rejects(incident.save)


class MaintenanceTests(InventoryFixtures):
    def test_preventive_and_corrective_records_do_not_change_asset_status(self):
        for kind in MaintenanceRecord.MaintenanceType.values:
            record = self.maintenance(maintenance_type=kind)
            record.full_clean()
            self.assertEqual(record.branch, self.branch)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.operational_status, "operational")

    def test_completion_requires_a_past_or_present_performed_date(self):
        record = MaintenanceRecord(asset=self.asset, maintenance_type="corrective", description="Trabajo", status="completed")
        with self.assertRaises(ValidationError):
            record.full_clean()
        self.assert_database_rejects(record.save)
        record.performed_at = timezone.now() - timedelta(minutes=1)
        record.full_clean()
        record.save()
        self.assertEqual(record.status, "completed")

    def test_future_performed_date_rejected_on_model_and_bulk_update(self):
        record = self.maintenance()
        future = timezone.now() + timedelta(days=1)
        record.status, record.performed_at = "completed", future
        with self.assertRaises(ValidationError):
            record.full_clean()
        self.assert_database_rejects(lambda: MaintenanceRecord.objects.filter(pk=record.pk).update(status="completed", performed_at=future))

    def test_noncompleted_states_cannot_have_performed_date(self):
        for status in ("scheduled", "in_progress", "cancelled"):
            with self.subTest(status=status):
                self.assert_database_rejects(lambda: self.maintenance(status=status, performed_at=timezone.now()))

    def test_scheduled_date_is_optional_and_early_completion_is_valid(self):
        self.maintenance().full_clean()
        self.maintenance(status="completed", scheduled_at=timezone.now()+timedelta(days=1), performed_at=timezone.now()).full_clean()

    def test_invalid_type_and_state_rejected(self):
        self.assert_database_rejects(lambda: self.maintenance(maintenance_type="unknown"))
        self.assert_database_rejects(lambda: self.maintenance(status="unknown"))

    def test_cross_branch_incident_rejected_in_model_and_bulk_insert(self):
        incident = self.incident(branch=self.other_branch, branch_area=self.other_area)
        record = MaintenanceRecord(asset=self.asset, incident=incident, maintenance_type="preventive", description="Trabajo")
        with self.assertRaises(ValidationError):
            record.full_clean()
        self.assert_database_rejects(lambda: MaintenanceRecord.objects.bulk_create([record]))

    def test_incident_of_another_asset_in_same_branch_rejected(self):
        asset = GymAsset.objects.create(branch=self.branch, category=self.category, code="SECOND", name="Segundo")
        incident = self.incident(gym_asset=asset)
        self.assert_database_rejects(lambda: self.maintenance(incident=incident))

    def test_unlocated_legacy_incident_rejected_but_located_incident_without_asset_allowed(self):
        incident = self.incident(branch=None, branch_area=None)
        self.assert_database_rejects(lambda: self.maintenance(incident=incident))
        self.maintenance(incident=self.incident()).full_clean()

    def test_bulk_update_cannot_change_maintenance_to_foreign_asset(self):
        record = self.maintenance(incident=self.incident(gym_asset=self.asset))
        self.assert_database_rejects(lambda: MaintenanceRecord.objects.filter(pk=record.pk).update(asset=self.other_asset))

    def test_maintenance_branch_snapshot_cannot_be_forged(self):
        record = self.maintenance()
        self.assert_database_rejects(lambda: MaintenanceRecord.objects.filter(pk=record.pk).update(branch=self.other_branch))

    def test_maintenance_cannot_be_reassigned_even_to_a_same_branch_asset(self):
        record = self.maintenance()
        asset = GymAsset.objects.create(branch=self.branch, category=self.category, code="REPLACEMENT", name="Otro equipo")
        record.asset = asset
        with self.assertRaises(ValidationError):
            record.full_clean()
        self.assert_database_rejects(lambda: MaintenanceRecord.objects.filter(pk=record.pk).update(asset=asset))

    def test_historical_maintenance_can_link_matching_report_after_asset_transfer(self):
        record = self.maintenance()
        incident = self.incident(gym_asset=self.asset)
        self.asset.transfer_to(self.other_branch, branch_area=self.other_area, code="MOVED")
        record.incident = incident
        record.full_clean()
        record.save()
        record.refresh_from_db()
        self.assertEqual(record.branch, self.branch)
        self.assertEqual(record.incident, incident)

    def test_incident_changes_cannot_invalidate_maintenance(self):
        incident = self.incident(gym_asset=self.asset)
        self.maintenance(incident=incident)
        self.assert_database_rejects(lambda: Incident.objects.filter(pk=incident.pk).update(branch=self.other_branch, branch_area=None, gym_asset=None))

    def test_records_and_referenced_entities_cannot_be_deleted(self):
        incident = self.incident(gym_asset=self.asset)
        record = self.maintenance(incident=incident)
        with self.assertRaises(ProtectedError):
            self.asset.delete()
        with self.assertRaises(ProtectedError):
            incident.delete()
        self.assert_database_rejects(lambda: MaintenanceRecord.objects.filter(pk=record.pk).delete())


class InventoryAdminTests(InventoryFixtures):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create(username="inventory-test-admin", is_superuser=True, is_staff=True)

    def admin_form(self, model, data=None, instance=None):
        request = RequestFactory().get("/admin/")
        request.user = self.user
        return admin.site.get_model_admin(model).get_form(request, obj=instance)(data=data, instance=instance)

    def test_new_admin_changelists_render(self):
        self.client.force_login(self.user)
        self.maintenance()
        for model in ("equipmentcategory", "requestcategory", "gymasset", "maintenancerecord", "assetmovement"):
            with self.subTest(model=model):
                self.assertEqual(self.client.get(reverse(f"admin:incidents_{model}_changelist")).status_code, 200)

    def test_asset_admin_search_and_filters(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("admin:incidents_gymasset_changelist"), {
            "q": "Marca de prueba", "branch__id__exact": self.branch.pk,
            "category__id__exact": self.category.pk, "operational_status__exact": "operational",
        })
        self.assertEqual(list(response.context["cl"].queryset), [self.asset])

    def test_asset_admin_rejects_foreign_area(self):
        form = self.admin_form(GymAsset, data={
            "branch": self.branch.pk, "branch_area": self.other_area.pk,
            "category": self.category.pk, "code": "ADMIN", "name": "Activo",
            "operational_status": "operational", "is_active": True,
        })
        self.assertFalse(form.is_valid())
        self.assertIn("branch_area", form.errors)

    def test_asset_admin_can_create_and_transfer_with_audit(self):
        data = dict(branch=self.branch.pk, branch_area=self.area.pk, category=self.category.pk,
                    code="ADMIN", name="Activo", operational_status="operational", is_active=True)
        form = self.admin_form(GymAsset, data=data)
        self.assertTrue(form.is_valid(), form.errors)
        asset = form.save()
        data.update(branch=self.other_branch.pk, branch_area=self.other_area.pk)
        form = self.admin_form(GymAsset, data=data, instance=asset)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(asset.movements.count(), 1)

    def test_asset_admin_displays_maintenance_and_movement_history(self):
        self.maintenance()
        self.asset.transfer_to(self.branch, branch_area=self.second_area)
        self.client.force_login(self.user)
        response = self.client.get(reverse("admin:incidents_gymasset_change", args=[self.asset.pk]))
        self.assertEqual(response.status_code, 200)
        inline_models = {inline.opts.model for inline in response.context["inline_admin_formsets"]}
        self.assertEqual(inline_models, {MaintenanceRecord, AssetMovement})

    def test_incident_admin_rejects_foreign_asset(self):
        request = RequestFactory().get("/admin/")
        request.user = self.user
        form_class = admin.site.get_model_admin(Incident).get_form(request)
        form = form_class(data={
            "branch": self.branch.pk, "branch_area": self.area.pk, "gym_asset": self.other_asset.pk,
            "technician": self.reporter.pk, "report_type": "incident", "status": "reported",
            "priority": "medium", "unit": "Unidad", "equipment": "Equipo",
            "failure_type": "Falla", "description": "Descripción", "original_message": "Mensaje",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("gym_asset", form.errors)

    def test_legacy_incident_admin_and_historical_asset_after_transfer_remain_editable(self):
        self.assertIsNone(self.admin_form(Incident, instance=self.incident(gym_asset=None)).instance.gym_asset_id)
        incident = self.incident(gym_asset=self.asset)
        self.asset.transfer_to(self.other_branch, code="MOVED", branch_area=self.other_area)
        form = self.admin_form(Incident, instance=incident)
        self.assertIn(self.asset, form.fields["gym_asset"].queryset)

    def test_request_category_admin_filter(self):
        request = self.incident(report_type="request", request_category=self.request_category)
        self.incident()
        self.client.force_login(self.user)
        response = self.client.get(reverse("admin:incidents_incident_changelist"), {
            "request_category__id__exact": self.request_category.pk,
        })
        self.assertEqual(list(response.context["cl"].queryset), [request])

    def test_maintenance_admin_validates_branch(self):
        incident = self.incident(branch=self.other_branch, branch_area=self.other_area)
        form = self.admin_form(MaintenanceRecord, data={
            "asset": self.asset.pk, "incident": incident.pk, "maintenance_type": "corrective",
            "status": "scheduled", "description": "Trabajo",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("incident", form.errors)

    def test_maintenance_admin_accepts_valid_record_and_blocks_delete(self):
        form = self.admin_form(MaintenanceRecord, data={
            "asset": self.asset.pk, "maintenance_type": "corrective", "status": "scheduled", "description": "Trabajo",
        })
        self.assertTrue(form.is_valid(), form.errors)
        record = form.save()
        self.client.force_login(self.user)
        response = self.client.post(reverse("admin:incidents_maintenancerecord_delete", args=[record.pk]), {"post": "yes"})
        self.assertEqual(response.status_code, 403)

    def test_movement_admin_is_read_only(self):
        request = RequestFactory().get("/admin/")
        request.user = self.user
        model_admin = admin.site.get_model_admin(AssetMovement)
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))


class InventoryMigrationTests(TransactionTestCase):
    def test_new_migrations_preserve_legacy_reports_without_creating_catalogs(self):
        self.assertEqual(connection.settings_dict["NAME"], "test_incidentes_gymplanet_db")
        previous = [("incidents", "0012_branch_areas_report_type")]
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(previous)
            apps = executor.loader.project_state(previous).apps
            reporter = apps.get_model("incidents", "Technician").objects.create(full_name="Reportante histórico")
            old = apps.get_model("incidents", "Incident").objects.create(
                technician=reporter, report_type="request", unit="Unidad", area="Área antigua",
                equipment="Equipo antiguo", location_context="Ubicación antigua", asset_category="video_cameras",
                priority="high", status="under_review", failure_type="Falla", description="Descripción", original_message="Mensaje",
            )
            executor = MigrationExecutor(connection)
            executor.migrate(targets)
            apps = executor.loader.project_state(targets).apps
            report = apps.get_model("incidents", "Incident").objects.get(pk=old.pk)
            self.assertEqual(report.area, "Área antigua")
            self.assertEqual(report.equipment, "Equipo antiguo")
            self.assertEqual(report.location_context, "Ubicación antigua")
            self.assertEqual(report.asset_category, "video_cameras")
            self.assertEqual((report.priority, report.status, report.report_type), ("high", "in_progress", "request"))
            self.assertIsNone(report.gym_asset_id)
            self.assertIsNone(report.request_category_id)
            for model in ("EquipmentCategory", "RequestCategory", "GymAsset", "MaintenanceRecord", "AssetMovement"):
                self.assertEqual(apps.get_model("incidents", model).objects.count(), 0)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
