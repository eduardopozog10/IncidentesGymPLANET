from datetime import timedelta
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from incidents.models import Branch, Incident, Technician


class BranchTests(TestCase):
    def create_branch(self, **kwargs):
        values = {"name": "Sucursal de prueba", "code": "TEST-001"}
        values.update(kwargs)
        return Branch.objects.create(**values)

    def test_branch_defaults_and_timestamps_are_persisted(self):
        branch = self.create_branch()
        branch.refresh_from_db()

        self.assertTrue(branch.is_active)
        self.assertIsNotNone(branch.created_at)
        self.assertIsNotNone(branch.updated_at)
        self.assertEqual(str(branch), "TEST-001 - Sucursal de prueba")

    def test_duplicate_code_is_rejected_by_validation_and_database(self):
        self.create_branch()
        duplicate = Branch(name="Otra sucursal de prueba", code="TEST-001")

        with self.assertRaises(ValidationError) as error:
            duplicate.full_clean()
        self.assertIn("code", error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save()
        self.assertEqual(Branch.objects.count(), 1)

    def test_name_and_code_are_required(self):
        for field in ("name", "code"):
            with self.subTest(field=field):
                values = {"name": "Sucursal de prueba", "code": "TEST-001"}
                values[field] = ""
                with self.assertRaises(ValidationError) as error:
                    Branch(**values).full_clean()
                self.assertIn(field, error.exception.message_dict)

    def test_name_can_be_changed_without_changing_identity_or_code(self):
        branch = self.create_branch()
        original_id = branch.pk
        created_at = branch.created_at
        later = timezone.now() + timedelta(seconds=1)

        with patch("django.utils.timezone.now", return_value=later):
            branch.name = "Sucursal de prueba renombrada"
            branch.save()
        branch.refresh_from_db()

        self.assertEqual(branch.pk, original_id)
        self.assertEqual(branch.code, "TEST-001")
        self.assertEqual(branch.created_at, created_at)
        self.assertEqual(branch.updated_at, later)


class IncidentBranchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reporter = Technician.objects.create(full_name="Reportante de prueba")
        cls.branch = Branch.objects.create(name="Sucursal de prueba", code="TEST-001")

    def create_incident(self, **kwargs):
        values = {
            "technician": self.reporter,
            "unit": "Unidad de prueba",
            "equipment": "Equipo de prueba",
            "failure_type": "Falla de prueba",
            "description": "Descripción de prueba",
            "original_message": "Mensaje de prueba",
        }
        values.update(kwargs)
        return Incident.objects.create(**values)

    def test_legacy_incident_can_be_created_without_branch(self):
        incident = self.create_incident()
        incident.full_clean()
        incident.refresh_from_db()

        self.assertIsNone(incident.branch_id)

    def test_branch_can_be_assigned_to_an_existing_incident(self):
        incident = self.create_incident()
        incident.branch = self.branch
        incident.save(update_fields=["branch"])
        incident.refresh_from_db()

        self.assertEqual(incident.branch, self.branch)
        self.assertEqual(list(self.branch.incidents.all()), [incident])

    def test_branch_queries_keep_incidents_separate(self):
        other = Branch.objects.create(name="Otra sucursal de prueba", code="TEST-002")
        first = self.create_incident(branch=self.branch)
        second = self.create_incident(branch=other)
        self.create_incident()

        self.assertEqual(list(self.branch.incidents.all()), [first])
        self.assertEqual(list(other.incidents.all()), [second])

    def test_deleting_branch_with_incidents_is_protected(self):
        incident = self.create_incident(branch=self.branch)

        with self.assertRaises(ProtectedError):
            self.branch.delete()

        incident.refresh_from_db()
        self.assertEqual(incident.branch_id, self.branch.pk)
        self.assertTrue(Branch.objects.filter(pk=self.branch.pk).exists())

    def test_deactivating_branch_preserves_incident_history(self):
        incident = self.create_incident(branch=self.branch)
        self.branch.is_active = False
        self.branch.save(update_fields=["is_active", "updated_at"])
        incident.refresh_from_db()

        self.assertEqual(incident.branch, self.branch)
        self.assertFalse(incident.branch.is_active)
        self.assertEqual(self.branch.incidents.count(), 1)

    def test_branch_can_be_removed_from_incident_during_transition(self):
        incident = self.create_incident(branch=self.branch)
        incident.branch = None
        incident.full_clean()
        incident.save(update_fields=["branch"])
        incident.refresh_from_db()

        self.assertIsNone(incident.branch_id)
        self.assertTrue(Branch.objects.filter(pk=self.branch.pk).exists())


class BranchAdminTests(TestCase):
    def admin_form(self, model):
        request = RequestFactory().get("/admin/")
        request.user = User(is_staff=True, is_superuser=True)
        return admin.site.get_model_admin(model).get_form(request)

    def test_branch_admin_form_can_create_branch_and_reject_duplicate_code(self):
        form_class = self.admin_form(Branch)
        data = {"name": "Sucursal de prueba", "code": "TEST-ADMIN", "is_active": True}
        form = form_class(data=data)

        self.assertTrue(form.is_valid(), form.errors)
        branch = form.save()
        duplicate = form_class(data=data)
        self.assertFalse(duplicate.is_valid())
        self.assertIn("code", duplicate.errors)
        self.assertEqual(branch.code, "TEST-ADMIN")

    def test_incident_admin_exposes_optional_branch_selection(self):
        branch = Branch.objects.create(name="Sucursal de prueba", code="TEST-001")
        form = self.admin_form(Incident)()

        self.assertIn("branch", form.fields)
        self.assertFalse(form.fields["branch"].required)
        self.assertIn(branch, form.fields["branch"].queryset)
