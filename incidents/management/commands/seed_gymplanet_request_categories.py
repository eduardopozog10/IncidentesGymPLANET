from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from incidents.models import RequestCategory
from incidents.services.gym_language import plain


CATEGORIES = (
    ("REPOSICION", "Reposición de implementos"),
    ("COMPRA", "Compra de equipamiento"),
    ("ASEO", "Insumos de aseo"),
    ("PREVENTIVO", "Mantención preventiva"),
    ("REPARACIONES", "Reparaciones"),
    ("INFRAESTRUCTURA", "Infraestructura"),
    ("CLIMATIZACION", "Climatización"),
    ("SERVICIOS", "Servicios externos"),
    ("SUMINISTROS", "Suministros"),
    ("OTROS", "Otros requerimientos operativos"),
)


class Command(BaseCommand):
    help = "Simula categorías operativas. --apply aplica una carga atómica sin sobrescribir registros."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Crear únicamente si no hay discrepancias.")

    def inspect(self, rows, *, output=False):
        by_code = {row.code: row for row in rows}
        conflicts = []
        for code, name in CATEGORIES:
            row = by_code.get(code)
            equivalent = [r for r in rows if plain(r.name) == plain(name) and r.code != code]
            if equivalent or (row and (row.name != name or not row.is_active)):
                conflicts.append(code)
                if output:
                    self.stderr.write(f"DISCREPANCIA {code}: nombre/código/estado existente requiere revisión. No se sobrescribe.")
            if output:
                self.stdout.write(f"{'EXISTENTE' if row else 'PROPUESTA'}: {code} — {row.name if row else name}")
        return conflicts

    def handle(self, *args, **options):
        self.inspect(list(RequestCategory.objects.all()), output=True)
        if not options["apply"]:
            self.stdout.write("Simulación. No se escribió ningún registro. Revisar antes de usar --apply.")
            return
        with transaction.atomic():
            if self.inspect(list(RequestCategory.objects.select_for_update().all())):
                raise CommandError("Carga cancelada por discrepancias. Sin cambios parciales.")
            created = 0
            for code, name in CATEGORIES:
                row, new = RequestCategory.objects.get_or_create(code=code, defaults={"name": name, "is_active": True})
                if row.name != name or not row.is_active:
                    raise CommandError("Discrepancia concurrente. Carga revertida.")
                created += int(new)
            # Recheck equivalent names after inserts, including races on other codes.
            if self.inspect(list(RequestCategory.objects.select_for_update().all())):
                raise CommandError("Discrepancia de nombres. Carga revertida.")
        self.stdout.write(f"Creadas: {created}. Registros existentes conservados.")
