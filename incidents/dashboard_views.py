from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.paginator import Paginator
from django.db.models import Count, Q, Avg, F
from django.db.models.functions import TruncDate
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import TemplateView

from incidents.models import Branch, GymAsset, Incident
from incidents.report_filters import OPEN_STATUSES, ReportFilters, AssetFilters
from incidents.services.operations import can_operate, manage_report
from django.core.exceptions import ValidationError


class OperationalView(UserPassesTestMixin, TemplateView):
    login_url = "/operations/login/"

    def test_func(self):
        user = self.request.user
        return can_operate(user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(section=getattr(self, "section", "overview"), page_title=getattr(self, "title", "Centro de control"))
        return context


def distribution(queryset, field, labels=None):
    rows = list(queryset.values(field).annotate(count=Count("pk")).order_by("-count", field))
    maximum = max((row["count"] for row in rows), default=1)
    return [{"label": (labels or {}).get(row[field], row[field] or "Sin sucursal histórica"), "count": row["count"], "width": round(row["count"] / maximum * 100)} for row in rows]


class DashboardView(OperationalView):
    template_name = "incidents/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filters = ReportFilters(self.request.GET)
        reports = filters.apply(Incident.objects.select_related("branch", "technician", "request_category"))
        values = filters.cleaned_data if filters.is_valid() else {}
        branches, assets = Branch.objects.all(), GymAsset.objects.all()
        if values.get("branch"):
            branches = branches.filter(pk=values["branch"].pk)
            assets = assets.filter(branch=values["branch"])
        if not filters.is_valid():
            branches, assets = branches.none(), assets.none()
        metrics = reports.aggregate(
            total=Count("pk"),
            open_incidents=Count("pk", filter=Q(report_type="incident", status__in=OPEN_STATUSES)),
            pending_requests=Count("pk", filter=Q(report_type="request", status__in=OPEN_STATUSES)),
            urgent=Count("pk", filter=Q(report_type="incident", priority__in=["high", "critical"], status__in=OPEN_STATUSES)),
        )
        metrics.update(reported=reports.filter(status="reported").count(), review=reports.filter(status="under_review").count(), working=reports.filter(status="in_progress").count(), resolved=reports.filter(status="resolved").count(), closed=reports.filter(status="closed").count(), unassigned=reports.filter(reportassignments__isnull=True).count(), average_resolution=reports.filter(resolved_at__gte=F("created_at")).aggregate(value=Avg(F("resolved_at")-F("created_at")))["value"])
        metrics.update(active_branches=branches.filter(is_active=True).count(), unavailable_assets=assets.filter(operational_status="out_of_service", is_active=True).count())
        timeline = list(reports.annotate(day=TruncDate("created_at")).values("day").annotate(count=Count("pk")).order_by("day"))
        maximum = max((row["count"] for row in timeline), default=1)
        for row in timeline:
            row["height"] = round(row["count"] / maximum * 100)
        branch_rows = list(reports.values("branch_id", "branch__name", "branch__code").annotate(count=Count("pk")).order_by("-count", "branch_id"))
        branch_max = max((row["count"] for row in branch_rows), default=1)
        branch_distribution = [{"label": f"{row['branch__name']} ({row['branch__code']})" if row["branch_id"] else "Sin sucursal histórica", "count": row["count"], "width": round(row["count"] / branch_max * 100)} for row in branch_rows]
        context.update(management_metrics=[("Reportados",metrics["reported"]),("En revisión",metrics["review"]),("En atención",metrics["working"]),("Resueltos",metrics["resolved"]),("Cerrados",metrics["closed"]),("Sin responsable (todos los estados)",metrics["unassigned"]),("Alta o crítica (todos los reportes)",reports.filter(priority__in=["high","critical"]).count())], filters=filters, metrics=metrics, recent=reports.order_by("-updated_at", "-pk")[:5],
                       latest_incidents=reports.filter(report_type="incident").order_by("-created_at", "-pk")[:4],
                       latest_requests=reports.filter(report_type="request").order_by("-created_at", "-pk")[:4],
                       by_branch=branch_distribution,
                       by_priority=distribution(reports, "priority", dict(Incident.Priority.choices)),
                       by_status=distribution(reports, "status", dict(Incident.Status.choices)), timeline=timeline)
        return context


class ReportListView(OperationalView):
    template_name = "incidents/report_list.html"
    report_type = None
    title = "Todos los reportes"
    section = "reports"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filters = ReportFilters(self.request.GET)
        reports = filters.apply(Incident.objects.select_related("branch", "branch_area", "technician", "request_category"))
        if self.report_type:
            reports = reports.filter(report_type=self.report_type)
        context.update(filters=filters, report_type=self.report_type, page_obj=Paginator(reports.order_by("-created_at", "-pk"), 20).get_page(self.request.GET.get("page")))
        return context


class StatusForm(forms.Form):
    solution = forms.CharField(label="Solución aplicada (obligatoria al resolver)", required=False, widget=forms.Textarea, max_length=4000)
    reason = forms.CharField(label="Motivo u observación (opcional)", required=False, max_length=4000)

    status = forms.ChoiceField(label="Estado del reporte", choices=Incident.Status.choices)


class ReportDetailView(OperationalView):
    template_name = "incidents/report_detail.html"
    section = "reports"
    title = "Detalle del reporte"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        report = get_object_or_404(Incident.objects.select_related("branch", "branch_area", "technician", "gym_asset", "request_category").prefetch_related("maintenance_records__asset"), pk=self.kwargs["pk"])
        context.update(assignment=report.reportassignments.select_related("actor").first(), activity=Paginator(report.reportactivitys.select_related("actor").all(), 30).get_page(self.request.GET.get("activity_page")), report=report, status_form=kwargs.get("status_form") or StatusForm(initial={"status": report.status}))
        return context

    def post(self, request, *args, **kwargs):
        get_object_or_404(Incident, pk=self.kwargs["pk"])
        action = request.POST.get("action", "status")
        try:
            if action == "status":
                form = StatusForm(request.POST)
                if not form.is_valid():
                    return self.render_to_response(self.get_context_data(status_form=form), status=400)
                manage_report(self.kwargs["pk"], request.user, "status", **form.cleaned_data)
            else:
                manage_report(self.kwargs["pk"], request.user, action, **{key:request.POST.get(key, "") for key in ("responsible","person_name","notes","comment")})
        except ValidationError as error:
            context = self.get_context_data(status_form=form) if action == "status" else self.get_context_data()
            context["operation_errors"] = error.messages
            context["submitted"] = request.POST
            return self.render_to_response(context, status=400)
        messages.success(request, {"status":"Estado actualizado correctamente.", "assign":"Asignación registrada correctamente.", "comment":"Seguimiento registrado correctamente."}[action])
        return HttpResponseRedirect(reverse("report-detail", args=[self.kwargs["pk"]]))


class BranchListView(OperationalView):
    template_name = "incidents/branches.html"
    title = "Sucursales"
    section = "branches"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filters = ReportFilters(self.request.GET)
        reports = filters.apply(Incident.objects.all())
        branches = Branch.objects.all()
        if filters.is_valid() and filters.cleaned_data.get("branch"):
            branches = branches.filter(pk=filters.cleaned_data["branch"].pk)
        elif not filters.is_valid():
            branches = branches.none()
        rows = list(branches)
        grouped = {r["branch_id"]: r for r in reports.values("branch_id").annotate(incidents=Count("pk", filter=Q(report_type="incident")), requests=Count("pk", filter=Q(report_type="request")), open=Count("pk", filter=Q(status__in=OPEN_STATUSES)))}
        for branch in rows:
            branch.metrics = grouped.get(branch.pk, {"incidents": 0, "requests": 0, "open": 0})
        context.update(filters=filters, branches=rows)
        return context


class AssetListView(OperationalView):
    template_name = "incidents/assets.html"
    title = "Equipos y activos"
    section = "assets"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filters = AssetFilters(self.request.GET)
        assets = GymAsset.objects.select_related("branch", "branch_area", "category").prefetch_related("maintenance_records__branch").annotate(incident_count=Count("incidents", filter=Q(incidents__report_type="incident")))
        if filters.is_valid():
            data = filters.cleaned_data
            if data.get("branch"):
                assets = assets.filter(branch=data["branch"])
            for field in ("category", "operational_status"):
                if data.get(field):
                    assets = assets.filter(**{field: data[field]})
            if data.get("area"):
                assets = assets.filter(branch_area__name__icontains=data["area"])
            if data.get("search"):
                text = data["search"]
                assets = assets.filter(Q(code__icontains=text) | Q(name__icontains=text) | Q(category__name__icontains=text))
        else:
            assets = assets.none()
        context.update(filters=filters, inventory=True, page_obj=Paginator(assets.order_by("branch__name", "code"), 20).get_page(self.request.GET.get("page")))
        return context


class AssetDetailView(OperationalView):
    template_name = "incidents/asset_detail.html"
    title = "Historial del activo"
    section = "assets"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        asset = get_object_or_404(GymAsset.objects.select_related("branch", "branch_area", "category").prefetch_related("maintenance_records__branch"), pk=self.kwargs["pk"])
        reports = asset.incidents.filter(report_type="incident").select_related("branch", "technician").order_by("-created_at", "-pk")
        context.update(asset=asset, report_type="incident", page_obj=Paginator(reports, 20).get_page(self.request.GET.get("page")))
        return context
