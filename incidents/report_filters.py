import re

from django import forms
from django.db.models import Q

from incidents.models import Branch, EquipmentCategory, GymAsset, Incident, RequestCategory

OPEN_STATUSES = ("reported", "under_review", "in_progress")


class ReportFilters(forms.Form):
    branch = forms.ModelChoiceField(label="Sucursal", queryset=Branch.objects.all(), required=False, empty_label="Todas las sucursales")
    report_type = forms.ChoiceField(label="Tipo", choices=[("", "Todos los tipos"), *Incident.ReportType.choices], required=False)
    priority = forms.ChoiceField(label="Prioridad", choices=[("", "Todas las prioridades"), *Incident.Priority.choices], required=False)
    status = forms.ChoiceField(label="Estado", choices=[("", "Todos los estados"), *Incident.Status.choices], required=False)
    date_from = forms.DateField(label="Desde", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(label="Hasta", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    area = forms.CharField(label="Área o ubicación", required=False, max_length=150)
    request_category = forms.ModelChoiceField(label="Categoría de solicitud", queryset=RequestCategory.objects.all(), required=False, empty_label="Todas las categorías")
    search = forms.CharField(label="Buscar reportes", required=False, max_length=200, widget=forms.TextInput(attrs={"placeholder": "Código, descripción, equipo o reportante…", "type": "search"}))

    def clean(self):
        data = super().clean()
        if data.get("date_from") and data.get("date_to") and data["date_from"] > data["date_to"]:
            raise forms.ValidationError("La fecha inicial debe ser anterior o igual a la final.")
        return data

    def apply(self, queryset):
        if not self.is_valid():
            return queryset.none()
        data = self.cleaned_data
        for name in ("branch", "report_type", "priority", "status", "request_category"):
            if data.get(name):
                queryset = queryset.filter(**{name: data[name]})
        for name, lookup in (("date_from", "created_at__date__gte"), ("date_to", "created_at__date__lte")):
            if data.get(name):
                queryset = queryset.filter(**{lookup: data[name]})
        if data.get("area"):
            queryset = queryset.filter(Q(area__icontains=data["area"]) | Q(location_context__icontains=data["area"]) | Q(branch_area__name__icontains=data["area"]))
        if data.get("search"):
            text = data["search"]
            match = Q(description__icontains=text) | Q(equipment__icontains=text) | Q(technician__full_name__icontains=text) | Q(failure_type__icontains=text)
            code = re.fullmatch(r"(SC|GP)-(\d{1,19})", text, re.I)
            if code:
                match |= Q(pk=int(code[2]), code_prefix=code[1].upper())
            queryset = queryset.filter(match)
        return queryset


class AssetFilters(forms.Form):
    branch = forms.ModelChoiceField(label="Sucursal", queryset=Branch.objects.all(), required=False, empty_label="Todas las sucursales")
    category = forms.ModelChoiceField(label="Categoría", queryset=EquipmentCategory.objects.all(), required=False, empty_label="Todas las categorías")
    operational_status = forms.ChoiceField(label="Estado operativo", choices=[("", "Todos los estados"), *GymAsset.OperationalStatus.choices], required=False)
    area = forms.CharField(label="Área", required=False, max_length=150)
    search = forms.CharField(label="Buscar activos", required=False, max_length=200, widget=forms.TextInput(attrs={"type": "search", "placeholder": "Código, nombre o categoría…"}))
