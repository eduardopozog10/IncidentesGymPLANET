from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from incidents.models import RequestProduct, ProductAlias
from incidents.request_catalog import PRODUCTS


class Command(BaseCommand):
    help = "Simula el catálogo oficial de 34 productos; --apply confirma una carga atómica, sin cantidades."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        proposed = []
        conflicts = []
        for index, (name, presentation, unit, aliases) in enumerate(PRODUCTS, 1):
            code = f"GP-ASEO-{index:03d}"
            values = dict(name=name, presentation=presentation, purchase_unit=unit, is_active=True)
            records = RequestProduct.objects.select_for_update() if options['apply'] else RequestProduct.objects
            row = records.filter(code=code).first()
            if row and any(getattr(row, key) != value for key, value in values.items()):
                conflicts.append(code)
            elif not row and RequestProduct.objects.filter(name__iexact=name).exists():
                conflicts.append(code)
            self.stdout.write(f"{'Existente' if row else 'Propuesto'}: {code} — {name}")
            proposed.append((code, values, aliases))
        if conflicts:
            raise CommandError("Discrepancias; no se aplicó ningún cambio: " + ", ".join(conflicts))
        if not options['apply']:
            self.stdout.write("Simulación: 0 escrituras. Utiliza --apply después de revisar.")
            return
        for code, values, aliases in proposed:
            row, _ = RequestProduct.objects.get_or_create(code=code, defaults=values)
            if any(getattr(row,key)!=value for key,value in values.items()):
                raise CommandError('El catálogo cambió durante la carga; se revirtieron los cambios: '+code)
            for name in aliases:
                from incidents.services.gymplanet_conversation import normalize
                ProductAlias.objects.get_or_create(product=row, normalized=normalize(name), defaults={"name":name})
        self.stdout.write("Catálogo disponible: 34 productos, sin importar cantidades ni solicitudes.")
