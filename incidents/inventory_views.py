from datetime import timedelta
from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Max, Min
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from incidents.dashboard_views import OperationalView
from incidents.forms import AssetOperationalForm, MaintenanceOperationalForm
from incidents.models import GymAsset, MaintenanceRecord, EquipmentCategory, Branch
from incidents.services.inventory import save_asset, save_maintenance, with_planning


class AssetEditView(OperationalView):
    template_name="incidents/inventory_form.html"
    title="Registrar o editar activo"
    section="assets"

    def get_context_data(self,**kwargs):
        context=super().get_context_data(**kwargs)
        asset=get_object_or_404(GymAsset,pk=self.kwargs["pk"]) if self.kwargs.get("pk") else None
        context.update(inventory_page="edit" if asset else "create", form=kwargs.get("form") or AssetOperationalForm(instance=asset))
        return context

    def post(self,request,*args,**kwargs):
        if kwargs.get("pk"): get_object_or_404(GymAsset,pk=kwargs["pk"])
        form=save_asset(request.user,request.POST,kwargs.get("pk"))
        if form.errors: return self.render_to_response(self.get_context_data(form=form),status=400)
        messages.success(request,"Activo guardado correctamente.")
        return redirect("dashboard-asset-detail",pk=form.instance.pk)


class CategoryForm(forms.ModelForm):
    class Meta:
        model=EquipmentCategory
        fields=["code","name","is_active"]


class CategoryView(OperationalView):
    template_name="incidents/inventory_form.html"
    title="Categorías de activos"
    section="assets"

    def get_context_data(self,**kwargs):
        ctx=super().get_context_data(**kwargs)
        instance=get_object_or_404(EquipmentCategory,pk=self.kwargs["pk"]) if self.kwargs.get("pk") else None
        ctx.update(inventory_page="categories", form=kwargs.get("form") or CategoryForm(instance=instance),categories=EquipmentCategory.objects.all())
        return ctx

    def post(self,request,*args,**kwargs):
        instance=get_object_or_404(EquipmentCategory,pk=kwargs["pk"]) if kwargs.get("pk") else None
        form=CategoryForm(request.POST,instance=instance)
        if not form.is_valid(): return self.render_to_response(self.get_context_data(form=form),status=400)
        form.save()
        return redirect("asset-categories")


class MaintenanceEditView(OperationalView):
    template_name="incidents/inventory_form.html"
    title="Registrar o actualizar mantenimiento"
    section="assets"

    def get_context_data(self,**kwargs):
        ctx=super().get_context_data(**kwargs)
        asset=get_object_or_404(GymAsset,pk=self.kwargs["asset_id"])
        record=get_object_or_404(MaintenanceRecord,pk=self.kwargs["pk"],asset=asset) if self.kwargs.get("pk") else MaintenanceRecord(asset=asset,branch=asset.branch)
        ctx.update(inventory_page="maintenance", asset=asset,form=kwargs.get("form") or MaintenanceOperationalForm(instance=record))
        return ctx

    def post(self,request,*args,**kwargs):
        get_object_or_404(GymAsset,pk=kwargs["asset_id"])
        if kwargs.get("pk"): get_object_or_404(MaintenanceRecord,pk=kwargs["pk"],asset_id=kwargs["asset_id"])
        form=save_maintenance(request.user,kwargs["asset_id"],request.POST,kwargs.get("pk"))
        if form.errors: return self.render_to_response(self.get_context_data(form=form),status=400)
        messages.success(request,"Mantenimiento guardado. No se cambió automáticamente el estado del equipo.")
        return redirect("dashboard-asset-detail",pk=kwargs["asset_id"])


class MaintenanceDashboard(OperationalView):
    template_name="incidents/maintenance_dashboard.html"
    title="Mantenimiento y recurrencia"
    section="assets"

    def get_context_data(self,**kwargs):
        ctx=super().get_context_data(**kwargs)
        now=timezone.now()
        form=MaintenanceFilters(self.request.GET)
        assets=with_planning(GymAsset.objects.all()).select_related("branch","category").annotate(recent_failures=Count("incidents",filter=Q(incidents__report_type="incident",incidents__created_at__gte=now-timedelta(days=90)),distinct=True),last_maintenance=Max("maintenance_records__performed_at",filter=Q(maintenance_records__status="completed")))
        records=MaintenanceRecord.objects.select_related("asset","branch")
        if form.is_valid():
            if form.cleaned_data.get("branch"):
                assets=assets.filter(branch=form.cleaned_data["branch"])
                records=records.filter(branch=form.cleaned_data["branch"])
            mode=form.cleaned_data.get("planning")
            if mode=="overdue": assets=assets.filter(planned_maintenance__lt=now)
            elif mode=="soon": assets=assets.filter(planned_maintenance__gte=now,planned_maintenance__lte=now+timedelta(days=30))
            elif mode=="none": assets=assets.filter(planned_maintenance__isnull=True)
            elif mode=="recurrent": assets=assets.filter(recent_failures__gte=2)
        else: assets,records=assets.none(),records.none()
        ctx.update(filters=form,page_obj=Paginator(assets.order_by("branch__name","code"),20).get_page(self.request.GET.get("page")),recent_maintenance=records.order_by("-updated_at")[:10],now=now,soon=now+timedelta(days=30))
        return ctx


class MaintenanceFilters(forms.Form):
    branch=forms.ModelChoiceField(queryset=Branch.objects.all(),required=False,label="Sucursal",empty_label="Todas las sucursales")
    planning=forms.ChoiceField(required=False,label="Planificación",choices=[("","Todos"),("soon","Próximos 30 días"),("overdue","Vencidos"),("none","Sin planificación"),("recurrent","Dos o más incidencias en 90 días")])
