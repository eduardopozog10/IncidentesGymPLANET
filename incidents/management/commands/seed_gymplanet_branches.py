from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from incidents.models import Branch


BRANCHES = (
    ("QUILIN", "Quilín"),
    ("PENALOLEN", "Peñalolén"),
    ("LA_CALERA", "La Calera"),
    ("RECREO", "Recreo"),
    ("QUILPUE", "Quilpué"),
    ("CONCEPCION", "Concepción"),
)


class Command(BaseCommand):
    help = (
        "Simula la carga inicial de sucursales sin escribir. "
        "Usa --apply para crear las faltantes; cualquier discrepancia cancela la carga."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Aplica la carga en una transacción, solo si no existen discrepancias.",
        )

    @staticmethod
    def differences(branch, name):
        differences = []
        if branch.name != name:
            differences.append(f"nombre actual={branch.name!r}, esperado={name!r}")
        if not branch.is_active:
            differences.append("is_active actual=False, esperado=True")
        return differences

    def report_discrepancy(self, code, differences):
        self.stderr.write(self.style.WARNING(
            f"{code}: {'; '.join(differences)}. "
            "Registro existente conservado sin cambios."
        ))

    def handle(self, *args, **options):
        codes = [code for code, _ in BRANCHES]
        existing = Branch.objects.filter(code__in=codes).in_bulk(field_name="code")
        discrepancy_count = 0
        for code, name in BRANCHES:
            branch = existing.get(code)
            if branch is None:
                self.stdout.write(f"PROPUESTA: {code} — {name}; activa=True.")
            else:
                self.stdout.write(
                    f"EXISTENTE: {code} — {branch.name}; activa={branch.is_active}."
                )
                differences = self.differences(branch, name)
                if differences:
                    discrepancy_count += 1
                    self.report_discrepancy(code, differences)

        if not options["apply"]:
            self.stdout.write(
                f"Simulación: por crear={len(BRANCHES) - len(existing)}; "
                f"existentes={len(existing)}; discrepancias={discrepancy_count}. "
                "No se escribió ningún registro. Usa --apply para aplicar."
            )
            return

        created_count = 0
        with transaction.atomic():
            # Recheck under row locks: the preview may have become stale.
            locked = Branch.objects.select_for_update().filter(
                code__in=codes,
            ).in_bulk(field_name="code")
            conflicts = [
                (code, self.differences(locked[code], name))
                for code, name in BRANCHES if code in locked
            ]
            conflicts = [(code, differences) for code, differences in conflicts if differences]
            if conflicts:
                for code, differences in conflicts:
                    self.report_discrepancy(code, differences)
                raise CommandError(
                    "Carga cancelada por discrepancias. No se guardó ningún cambio. "
                    "Revisa la simulación antes de volver a aplicar."
                )
            for code, name in BRANCHES:
                if code in locked:
                    continue
                branch, created = Branch.objects.get_or_create(
                    code=code,
                    defaults={"name": name, "is_active": True},
                )
                if not created:
                    # A concurrent insert can occur after the preflight query.
                    branch = Branch.objects.select_for_update().get(pk=branch.pk)
                differences = self.differences(branch, name)
                if differences:
                    self.report_discrepancy(code, differences)
                    raise CommandError(
                        "Carga cancelada por una discrepancia concurrente. "
                        "Se revirtieron todos los cambios de esta ejecución."
                    )
                created_count += int(created)

        self.stdout.write(
            self.style.SUCCESS(
                f"Creadas: {created_count}. "
                f"Existentes conservadas: {len(BRANCHES) - created_count}. "
                "Discrepancias: 0."
            )
        )
