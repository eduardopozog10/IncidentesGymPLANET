from django.core.exceptions import ValidationError
from django.db import transaction
from incidents.forms import AssetOperationalForm, MaintenanceOperationalForm
from incidents.models import GymAsset, MaintenanceRecord, AssetActivity
from incidents.services.operations import authorize


def snapshot(asset):
    return {field.name:str(getattr(asset,field.attname)) for field in GymAsset._meta.fields if field.name not in {"updated_at","created_at"}}


@transaction.atomic
def save_asset(actor,data,pk=None):
    authorize(actor)
    asset=GymAsset.objects.select_for_update().get(pk=pk) if pk else GymAsset()
    before=snapshot(asset) if pk else {}
    form=AssetOperationalForm(data,instance=asset)
    if form.is_valid():
        asset=form.save()
        after=snapshot(asset)
        if before!=after: AssetActivity.objects.create(asset=asset,actor=actor,before=before,after=after,description="Activo actualizado" if pk else "Activo registrado")
    return form


@transaction.atomic
def save_maintenance(actor,asset_id,data,pk=None):
    authorize(actor)
    asset=GymAsset.objects.select_for_update().get(pk=asset_id)
    record=MaintenanceRecord.objects.select_for_update().get(pk=pk,asset=asset) if pk else MaintenanceRecord(asset=asset,branch=asset.branch)
    before={field.name:str(getattr(record,field.attname)) for field in MaintenanceRecord._meta.fields} if pk else {}
    form=MaintenanceOperationalForm(data,instance=record)
    if form.is_valid():
        record=form.save()
        next_date=form.cleaned_data.get("next_maintenance_at")
        if next_date:
            asset.next_maintenance_at=next_date
            asset.save(update_fields=["next_maintenance_at","updated_at"])
        after={field.name:str(getattr(record,field.attname)) for field in MaintenanceRecord._meta.fields}
        if next_date: after["next_maintenance_at"]=next_date.isoformat()
        if before!=after: AssetActivity.objects.create(asset=asset,actor=actor,before=before,after=after,description="Mantenimiento actualizado" if pk else "Mantenimiento registrado")
    return form


def with_planning(queryset):
    from django.db.models import F, Min, Q
    from django.db.models.functions import Least
    return queryset.annotate(planned_maintenance=Least(F("next_maintenance_at"),Min("maintenance_records__scheduled_at",filter=Q(maintenance_records__status__in=["scheduled","in_progress"]))))
