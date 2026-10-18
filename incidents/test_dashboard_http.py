"""Real HTTP + Django auth/CSRF + PostgreSQL, exclusively on the test database."""
import re
from http.cookies import SimpleCookie
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import connection
from django.test import Client, override_settings
from django.urls import reverse

from incidents.models import Branch, Incident, Technician


@override_settings(AI_PROVIDER="mock", GEMINI_API_KEY="", TELEGRAM_BOT_TOKEN="", TELEGRAM_WEBHOOK_ENABLED=False, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class DashboardHTTPTests(StaticLiveServerTestCase):
    def setUp(self):
        self.assertEqual(connection.settings_dict["NAME"], "test_incidentes_gymplanet_db")
        # No persistent demonstration credentials: this disposable test user has no password.
        self.user = get_user_model().objects.create_user(username="http-dashboard-test", is_superuser=True, is_staff=True)
        self.client.force_login(self.user)
        self.cookie = self.client.cookies["sessionid"].OutputString().split(";", 1)[0]
        self.branch = Branch.objects.create(name="Sede HTTP de prueba", code="TEST-HTTP")
        self.reporter = Technician.objects.create(full_name="Cuenta corporativa HTTP")
        self.incident = Incident.objects.create(technician=self.reporter, branch=self.branch, equipment="Trotadora HTTP", failure_type="Se detiene", description="Incidencia HTTP de prueba", priority="high", code_prefix="SC")
        self.request_report = Incident.objects.create(technician=self.reporter, branch=self.branch, report_type="request", request_kind="Reposición", description="Solicitud HTTP de colchonetas", priority="medium", code_prefix="SC")

    def fetch(self, path, data=None, cookie=None):
        headers = {"Cookie": self.cookie if cookie is None else cookie}
        payload = urlencode(data).encode() if data is not None else None
        if payload is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        return urlopen(Request(self.live_server_url + path, data=payload, headers=headers), timeout=10)

    def test_every_operational_page_and_static_css_over_http(self):
        paths = [reverse(name) for name in ("dashboard", "dashboard-incidents", "dashboard-requests", "dashboard-reports", "dashboard-branches", "dashboard-assets")]
        paths += [reverse("report-detail", args=[report.pk]) for report in (self.incident, self.request_report)]
        for path in paths:
            with self.subTest(path=path), self.fetch(path) as response:
                html = response.read().decode()
                self.assertEqual(response.status, 200)
                self.assertIn("Gym Planet", html)
                self.assertNotIn("Secuoya", html)
        with self.fetch("/static/incidents/dashboard.css") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("--accent", response.read().decode())

    def test_report_separation_and_filter_over_http(self):
        for name, included, excluded in (("dashboard-incidents", self.incident, self.request_report), ("dashboard-requests", self.request_report, self.incident)):
            path = reverse(name) + "?" + urlencode({"branch": self.branch.pk, "search": included.code})
            with self.fetch(path) as response:
                html = response.read().decode()
                self.assertIn(included.code, html)
                self.assertNotIn(excluded.code, html)

    def test_real_csrf_protected_status_update_over_http(self):
        path = reverse("report-detail", args=[self.incident.pk])
        with self.assertRaises(HTTPError) as rejected:
            self.fetch(path, {"status": "resolved"})
        self.assertEqual(rejected.exception.code, 403)
        with self.fetch(path) as response:
            html = response.read().decode()
            cookies = SimpleCookie()
            for header in response.headers.get_all("Set-Cookie", []):
                cookies.load(header)
            csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
        cookie = self.cookie + "; csrftoken=" + cookies["csrftoken"].value
        with self.fetch(path, {"status": "resolved", "csrfmiddlewaretoken": csrf}, cookie=cookie) as response:
            self.assertEqual(response.status, 200)
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, "resolved")
        self.assertIsNotNone(self.incident.resolved_at)

    def test_anonymous_and_staff_http_requests_are_denied(self):
        with self.fetch(reverse("dashboard"), cookie="") as response:
            self.assertIn("/admin/login/", response.geturl())
            self.assertNotIn(self.incident.code, response.read().decode())
        staff = get_user_model().objects.create_user(username="http-staff-test", is_staff=True)
        client = Client()
        client.force_login(staff)
        cookie = client.cookies["sessionid"].OutputString().split(";", 1)[0]
        with self.assertRaises(HTTPError) as rejected:
            self.fetch(reverse("dashboard-incidents"), cookie=cookie)
        self.assertEqual(rejected.exception.code, 403)
