from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.db import IntegrityError
from django.test import TestCase

from incidents.models import Branch


class SeedGymPlanetBranchesTests(TestCase):
    expected = {
        "QUILIN": "Quilín",
        "PENALOLEN": "Peñalolén",
        "LA_CALERA": "La Calera",
        "RECREO": "Recreo",
        "QUILPUE": "Quilpué",
        "CONCEPCION": "Concepción",
    }

    def run_seed(self, *, apply=False):
        stdout, stderr = StringIO(), StringIO()
        args = ["--apply"] if apply else []
        call_command("seed_gymplanet_branches", *args, stdout=stdout, stderr=stderr)
        return stdout.getvalue(), stderr.getvalue()

    def test_default_simulation_proposes_all_branches_without_writes(self):
        with self.assertNumQueries(1):
            stdout, stderr = self.run_seed()

        self.assertFalse(Branch.objects.exists())
        for code, name in self.expected.items():
            self.assertIn(f"PROPUESTA: {code} — {name}; activa=True", stdout)
        self.assertIn("por crear=6", stdout)
        self.assertIn("--apply", stdout)
        self.assertEqual(stderr, "")

    def test_simulation_reports_existing_records_and_discrepancies_without_writes(self):
        Branch.objects.create(code="QUILIN", name="Nombre diferente", is_active=False)
        Branch.objects.create(code="RECREO", name="Recreo")
        before = list(Branch.objects.order_by("code").values())

        stdout, stderr = self.run_seed()

        self.assertEqual(list(Branch.objects.order_by("code").values()), before)
        self.assertIn("EXISTENTE: QUILIN", stdout)
        self.assertIn("EXISTENTE: RECREO", stdout)
        self.assertIn("por crear=4; existentes=2; discrepancias=1", stdout)
        self.assertIn("Nombre diferente", stderr)
        self.assertIn("Quilín", stderr)
        self.assertIn("is_active actual=False", stderr)

    def test_creates_exactly_six_named_active_branches_with_unique_codes(self):
        stdout, stderr = self.run_seed(apply=True)

        self.assertEqual(Branch.objects.count(), 6)
        self.assertEqual(dict(Branch.objects.values_list("code", "name")), self.expected)
        self.assertEqual(Branch.objects.filter(is_active=True).count(), 6)
        self.assertEqual(Branch.objects.values("code").distinct().count(), 6)
        self.assertIn("Creadas: 6", stdout)
        self.assertEqual(stderr, "")

    def test_repeated_runs_preserve_ids_and_timestamps_without_duplicates(self):
        self.run_seed(apply=True)
        before = list(Branch.objects.order_by("code").values())

        stdout, stderr = self.run_seed(apply=True)
        self.run_seed(apply=True)

        self.assertEqual(list(Branch.objects.order_by("code").values()), before)
        self.assertIn("Creadas: 0", stdout)
        self.assertIn("Existentes conservadas: 6", stdout)
        self.assertEqual(stderr, "")

    def test_matching_existing_branch_is_reused_by_code(self):
        branch = Branch.objects.create(code="QUILIN", name="Quilín")
        before = Branch.objects.values().get(pk=branch.pk)

        stdout, stderr = self.run_seed(apply=True)

        self.assertEqual(Branch.objects.count(), 6)
        self.assertEqual(Branch.objects.values().get(code="QUILIN"), before)
        self.assertIn("Creadas: 5", stdout)
        self.assertEqual(stderr, "")

    def test_different_existing_name_is_preserved_and_reported(self):
        branch = Branch.objects.create(code="QUILIN", name="Nombre de prueba existente")
        before = Branch.objects.values().get(pk=branch.pk)

        stderr = StringIO()
        with self.assertRaisesMessage(CommandError, "No se guardó ningún cambio"):
            call_command("seed_gymplanet_branches", "--apply", stdout=StringIO(), stderr=stderr)

        self.assertEqual(Branch.objects.values().get(pk=branch.pk), before)
        self.assertEqual(Branch.objects.count(), 1)
        self.assertIn("QUILIN", stderr.getvalue())
        self.assertIn("Nombre de prueba existente", stderr.getvalue())
        self.assertIn("Quilín", stderr.getvalue())

    def test_inactive_existing_branch_is_not_reactivated_and_is_reported(self):
        branch = Branch.objects.create(code="RECREO", name="Recreo", is_active=False)
        before = Branch.objects.values().get(pk=branch.pk)

        stderr = StringIO()
        with self.assertRaises(CommandError):
            call_command("seed_gymplanet_branches", "--apply", stdout=StringIO(), stderr=stderr)

        self.assertEqual(Branch.objects.values().get(pk=branch.pk), before)
        self.assertIn("RECREO", stderr.getvalue())
        self.assertIn("is_active actual=False", stderr.getvalue())
        self.assertEqual(Branch.objects.count(), 1)
        self.assertEqual(Branch.objects.filter(is_active=True).count(), 0)

    def test_unrelated_existing_branch_is_preserved(self):
        branch = Branch.objects.create(code="TEST-OTHER", name="Sucursal de prueba")
        before = Branch.objects.values().get(pk=branch.pk)

        self.run_seed(apply=True)
        self.run_seed(apply=True)

        self.assertEqual(Branch.objects.count(), 7)
        self.assertEqual(Branch.objects.values().get(pk=branch.pk), before)

    def test_database_error_rolls_back_partial_creation(self):
        original = Branch.objects.get_or_create
        calls = 0

        def fail_after_first_branch(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise IntegrityError("Error de base de datos simulado")
            return original(**kwargs)

        with patch.object(Branch.objects, "get_or_create", side_effect=fail_after_first_branch):
            with self.assertRaises(IntegrityError):
                self.run_seed(apply=True)

        self.assertFalse(Branch.objects.exists())

    def test_discrepancy_detected_after_preview_cancels_before_creating(self):
        branch = Branch.objects.create(code="RECREO", name="Recreo")
        original = Branch.objects.select_for_update

        def concurrent_change():
            Branch.objects.filter(pk=branch.pk).update(name="Cambio concurrente")
            return original()

        with patch.object(Branch.objects, "select_for_update", side_effect=concurrent_change):
            with self.assertRaises(CommandError):
                self.run_seed(apply=True)

        self.assertEqual(Branch.objects.count(), 1)

    def test_concurrent_conflict_after_partial_insert_rolls_back_everything(self):
        original = Branch.objects.get_or_create
        calls = 0

        def conflict_after_first_branch(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                branch = Branch.objects.create(
                    code=kwargs["code"], name="Nombre concurrente diferente",
                )
                return branch, False
            return original(**kwargs)

        with patch.object(Branch.objects, "get_or_create", side_effect=conflict_after_first_branch):
            with self.assertRaisesMessage(CommandError, "Se revirtieron todos los cambios"):
                self.run_seed(apply=True)

        self.assertFalse(Branch.objects.exists())
