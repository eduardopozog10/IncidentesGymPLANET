from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import include, path

from incidents.dashboard_views import DashboardView, ReportListView, ReportDetailView, BranchListView, AssetListView, AssetDetailView


urlpatterns = [
    path("operations/login/", LoginView.as_view(template_name="incidents/operations_login.html", next_page="/dashboard/"), name="operations-login"),
    path("operations/logout/", LogoutView.as_view(next_page="/operations/login/"), name="operations-logout"),
    path("admin/", admin.site.urls),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("dashboard/incidents/", ReportListView.as_view(report_type="incident", title="Incidencias", section="incidents"), name="dashboard-incidents"),
    path("dashboard/requests/", ReportListView.as_view(report_type="request", title="Solicitudes", section="requests"), name="dashboard-requests"),
    path("dashboard/reports/", ReportListView.as_view(), name="dashboard-reports"),
    path("dashboard/reports/<int:pk>/", ReportDetailView.as_view(), name="report-detail"),
    path("dashboard/branches/", BranchListView.as_view(), name="dashboard-branches"),
    path("dashboard/assets/", AssetListView.as_view(), name="dashboard-assets"),
    path("dashboard/assets/<int:pk>/", AssetDetailView.as_view(), name="dashboard-asset-detail"),
    path("api/v1/", include("incidents.urls")),
]