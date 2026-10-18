from django.contrib.auth.mixins import UserPassesTestMixin
from django.views.generic import TemplateView


class DashboardView(UserPassesTestMixin, TemplateView):
    login_url = "/admin/login/"

    def test_func(self):
        user = self.request.user
        return user.is_authenticated and user.is_active and user.is_superuser

    template_name = "incidents/dashboard.html"