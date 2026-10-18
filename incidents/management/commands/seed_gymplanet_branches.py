from django.core.management.base import BaseCommand
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
        "Crea las seis sucursales iniciales de Gym Planet por código único. "
        "Conserva los registros existentes e informa discrepancias sin actualizarlos."
    )

    def handle(self, *args, **options):
        created_count = 0
        discrepancies = []

        with transaction.atomic():
            for code, name in BRANCHES:
                branch, created = Branch.objects.get_or_create(
                    code=code,
                    defaults={"name": name, "is_active": True},
                )
                if created:
                    created_count += 1
                    continue

                differences = []
                if branch.name != name:
                    differences.append(
                        f"nombre actual={branch.name!r}, esperado={name!r}"
                    )
                if not branch.is_active:
                    differences.append("is_active actual=False, esperado=True")
                if differences:
                    discrepancies.append(
                        f"{code}: {'; '.join(differences)}. "
                        "Registro existente conservado sin cambios."
                    )

        for message in discrepancies:
            self.stderr.write(self.style.WARNING(message))
        self.stdout.write(
            self.style.SUCCESS(
                f"Creadas: {created_count}. "
                f"Existentes conservadas: {len(BRANCHES) - created_count}. "
                f"Discrepancias: {len(discrepancies)}."
            )
        )
