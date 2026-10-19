from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from incidents.ai.gemini_provider import GeminiAIProvider
from incidents.ai.mock_provider import MockAIProvider
from incidents.ai.schemas import IncidentExtractionResult
from incidents.models import Branch, BranchArea, EquipmentCategory, GymAsset, Incident, MaintenanceRecord, RequestCategory, Technician, TechnicianChannel
from incidents.services.incident_processor import IncidentProcessor
from incidents.services.incoming_message_service import IncomingMessageService


class OperationalDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user(username="dashboard-test", is_superuser=True, is_staff=True)
        cls.staff = get_user_model().objects.create_user(username="limited-test", is_staff=True)
        cls.branch = Branch.objects.create(name="Sede de prueba", code="TEST-A")
        cls.other = Branch.objects.create(name="Otra sede", code="TEST-B")
        cls.area = BranchArea.objects.create(branch=cls.branch, name="Cardio", code="CARDIO")
        cls.reporter = Technician.objects.create(full_name="Cuenta corporativa de prueba")
        cls.category = RequestCategory.objects.create(code="TEST-REQ", name="Implementos")
        cls.incident = Incident.objects.create(technician=cls.reporter, branch=cls.branch, branch_area=cls.area, equipment="Trotadora", failure_type="Se detiene", description="Cinta se detiene", operational_impact="No puede utilizarse", priority="high", area="Cardio", original_message="Reporte de prueba", code_prefix="SC")
        cls.request = Incident.objects.create(technician=cls.reporter, branch=cls.branch, report_type="request", request_kind="Reposición", request_category=cls.category, description="Cinco colchonetas para clases", operational_impact="Deterioro y falta de stock", priority="medium", original_message="Solicitud de prueba", code_prefix="SC")
        cls.other_incident = Incident.objects.create(technician=cls.reporter, branch=cls.other, equipment="Bicicleta", failure_type="Ruido", description="Ruido constante", priority="low", status="closed", original_message="Otro reporte")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_control_center_real_metrics(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({key:response.context["metrics"][key] for key in ("total","open_incidents","pending_requests","urgent","active_branches","unavailable_assets")}, {"total": 3, "open_incidents": 1, "pending_requests": 1, "urgent": 1, "active_branches": 2, "unavailable_assets": 0})
        self.assertContains(response, "Centro de control")
        self.assertContains(response, self.incident.code)
        self.assertContains(response, self.request.code)

    def test_branch_metrics_obey_selection(self):
        response = self.client.get(reverse("dashboard"), {"branch": self.other.pk})
        metrics = response.context["metrics"]
        self.assertEqual((metrics["total"], metrics["open_incidents"], metrics["pending_requests"], metrics["active_branches"]), (1, 0, 0, 1))

    def test_date_filter_obeys_local_date(self):
        Incident.objects.filter(pk=self.incident.pk).update(created_at=timezone.now()-timedelta(days=30))
        response = self.client.get(reverse("dashboard"), {"date_from": timezone.localdate().isoformat()})
        self.assertEqual(response.context["metrics"]["total"], 2)
        self.assertEqual(response.context["metrics"]["urgent"], 0)

    def test_invalid_filters_show_errors_and_no_results(self):
        for data in ({"branch": "not-a-number"}, {"date_from": "bad-date"}, {"date_from": "2026-10-20", "date_to": "2026-10-01"}, {"priority": "arbitrary"}):
            with self.subTest(data=data):
                response = self.client.get(reverse("dashboard"), data)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["metrics"]["total"], 0)
                self.assertTrue(response.context["filters"].errors)

    def test_distributions_and_timeline_are_database_counts(self):
        response = self.client.get(reverse("dashboard"))
        for key in ("by_branch", "by_priority", "by_status"):
            self.assertEqual(sum(row["count"] for row in response.context[key]), 3)
        self.assertEqual(sum(row["count"] for row in response.context["timeline"]), 3)

    def test_duplicate_branch_names_do_not_merge_distribution(self):
        self.other.name = self.branch.name
        self.other.save()
        rows = self.client.get(reverse("dashboard")).context["by_branch"]
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["label"], rows[1]["label"])

    def test_incident_list_excludes_requests(self):
        response = self.client.get(reverse("dashboard-incidents"))
        self.assertEqual({r.pk for r in response.context["page_obj"]}, {self.incident.pk})
        self.assertNotContains(response, self.request.code)

    def test_request_list_excludes_technical_columns(self):
        response = self.client.get(reverse("dashboard-requests"))
        self.assertEqual([r.pk for r in response.context["page_obj"]], [self.request.pk])
        self.assertContains(response, "Justificación")
        self.assertNotContains(response, "Falla o síntoma")
        self.assertNotContains(response, "Equipo / instalación")

    def test_all_report_filters(self):
        data = {"branch": self.branch.pk, "report_type": "request", "priority": "medium", "status": "reported", "request_category": self.category.pk}
        response = self.client.get(reverse("dashboard-reports"), data)
        self.assertEqual([r.pk for r in response.context["page_obj"]], [self.request.pk])

    def test_area_filter_includes_registered_area(self):
        response = self.client.get(reverse("dashboard-reports"), {"area": "Cardio"})
        self.assertEqual([r.pk for r in response.context["page_obj"]], [self.incident.pk])

    def test_search_by_code_description_equipment_and_reporter(self):
        for term, expected in ((self.incident.code, 1), ("colchonetas", 1), ("Trotadora", 1), ("corporativa", 3), ("no existe", 0)):
            with self.subTest(term=term):
                response = self.client.get(reverse("dashboard-reports"), {"search": term})
                self.assertEqual(response.context["page_obj"].paginator.count, expected)

    def test_search_escapes_html(self):
        response = self.client.get(reverse("dashboard-reports"), {"search": '<script>alert("x")</script>'})
        self.assertNotContains(response, '<script>alert("x")</script>')

    def test_pagination_retains_filters(self):
        for i in range(21):
            Incident.objects.create(technician=self.reporter, branch=self.branch, equipment="Equipo", failure_type="Falla", description=f"Paginado {i}", priority="medium")
        response = self.client.get(reverse("dashboard-reports"), {"branch": self.branch.pk, "search": "Paginado"})
        self.assertEqual(len(response.context["page_obj"]), 20)
        self.assertContains(response, "page=2")
        self.assertContains(response, "search=Paginado")
        response = self.client.get(reverse("dashboard-reports"), {"branch": self.branch.pk, "search": "Paginado", "page": 2})
        self.assertEqual(len(response.context["page_obj"]), 1)

    def test_detail_incident_and_request(self):
        for report in (self.incident, self.request):
            response = self.client.get(reverse("report-detail", args=[report.pk]))
            self.assertContains(response, report.code)
            self.assertContains(response, report.description)
        response = self.client.get(reverse("report-detail", args=[self.request.pk]))
        self.assertNotContains(response, "Falla o síntoma")
        self.assertContains(response, "Cinco colchonetas")

    def test_detail_missing_is_404(self):
        self.assertEqual(self.client.get(reverse("report-detail", args=[99999999])).status_code, 404)

    def test_status_update_preserves_report_fields(self):
        url = reverse("report-detail", args=[self.incident.pk])
        before = self.incident.description
        self.incident.status = "in_progress"
        self.incident.save()
        self.assertEqual(self.client.post(url, {"status": "resolved", "solution":"Solución de prueba", "branch": self.other.pk}).status_code, 302)
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, "resolved")
        self.assertIsNotNone(self.incident.resolved_at)
        self.assertEqual((self.incident.description, self.incident.branch_id, self.incident.code_prefix), (before, self.branch.pk, "SC"))
        self.client.post(url, {"status": "in_progress", "reason":"Reabrir para revisión"})
        self.incident.refresh_from_db()
        self.assertIsNone(self.incident.resolved_at)

    def test_invalid_status_does_not_write(self):
        response = self.client.post(reverse("report-detail", args=[self.incident.pk]), {"status": "invalid"})
        self.assertEqual(response.status_code, 400)
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.status, "reported")

    def test_state_update_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        url = reverse("report-detail", args=[self.incident.pk])
        self.assertEqual(client.post(url, {"status": "resolved"}).status_code, 403)
        client.get(url)
        self.assertEqual(client.post(url, {"status": "in_progress"}, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value).status_code, 302)

    def test_all_new_routes_require_superuser(self):
        urls = [reverse(name) for name in ("dashboard", "dashboard-incidents", "dashboard-requests", "dashboard-reports", "dashboard-branches", "dashboard-assets")]+[reverse("report-detail", args=[self.incident.pk])]
        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.staff)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(urls[-1], {"status": "closed"}).status_code, 403)

    def test_sucursales_counts_and_active_status(self):
        response = self.client.get(reverse("dashboard-branches"))
        branch = next(b for b in response.context["branches"] if b.pk == self.branch.pk)
        self.assertEqual(branch.metrics, {"branch_id": self.branch.pk, "incidents": 1, "requests": 1, "open": 2})
        self.assertContains(response, "Activa")

    def test_inventory_empty_state(self):
        response = self.client.get(reverse("dashboard-assets"))
        self.assertContains(response, "Un lugar para cada equipo")
        self.assertEqual(response.context["page_obj"].paginator.count, 0)

    def test_inventory_and_maintenance(self):
        category = EquipmentCategory.objects.create(code="TEST-FIT", name="Cardio")
        asset = GymAsset.objects.create(branch=self.branch, branch_area=self.area, category=category, code="TEST-T", name="Trotadora", operational_status="out_of_service")
        self.incident.gym_asset = asset
        self.incident.save()
        MaintenanceRecord.objects.create(asset=asset, incident=self.incident, maintenance_type="corrective", description="Revisar cinta")
        response = self.client.get(reverse("dashboard-assets"))
        self.assertContains(response, "Revisar cinta")
        self.assertEqual(response.context["page_obj"][0].incident_count, 1)
        self.assertEqual(self.client.get(reverse("dashboard")).context["metrics"]["unavailable_assets"], 1)
        self.assertContains(self.client.get(reverse("report-detail", args=[self.incident.pk])), "Revisar cinta")

    def test_inventory_filters_and_counts(self):
        category = EquipmentCategory.objects.create(code="TEST-INV", name="Cardio")
        asset = GymAsset.objects.create(branch=self.branch, branch_area=self.area, category=category, code="TEST-ASSET", name="Trotadora", operational_status="out_of_service")
        self.incident.gym_asset = asset
        self.incident.save()
        url = reverse("dashboard-assets")
        response = self.client.get(url, {"search": asset.code, "category": category.pk, "operational_status": "out_of_service", "area": "Cardio"})
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertEqual(response.context["page_obj"][0].incident_count, 1)
        self.assertContains(response, "Estado operativo")
        self.assertNotContains(response, 'id="id_report_type"')
        self.assertEqual(self.client.get(url, {"branch": self.other.pk}).context["page_obj"].paginator.count, 0)
        self.assertEqual(self.client.get(url, {"operational_status": "invalid"}).context["page_obj"].paginator.count, 0)

    def test_no_brand_or_external_scripts_and_accessible_structure(self):
        response = self.client.get(reverse("dashboard"))
        for text in ("Secuoya", "Canal 13", "TVTEL", "<script", "https://"):
            self.assertNotContains(response, text)
        for text in ('lang="es"', "Saltar al contenido", 'aria-current="page"', 'id="main"', 'for="id_branch"'):
            self.assertContains(response, text)

    def test_empty_center_never_fabricates_metrics(self):
        response = self.client.get(reverse("dashboard"), {"search": "sin coincidencias"})
        self.assertEqual(response.context["metrics"]["total"], 0)
        self.assertEqual(response.context["timeline"], [])
        self.assertContains(response, "La evolución aparecerá")

    def test_legacy_api_routes_and_codes_still_work(self):
        response = self.client.get(reverse("incidents:incident-list"), {"search": self.incident.code, "branch": self.branch.pk})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["code"], self.incident.code)
        self.assertEqual(self.client.get(reverse("incidents:incident-list"), {"search": self.incident.code, "priority": "low"}).data["count"], 0)
        self.assertEqual(self.client.get(reverse("incidents:incident-list"), {"search": "GP-" + "9" * 5000}).data["count"], 0)


class ReportCodeConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.reporter = Technician.objects.create(full_name="Cuenta de concurrencia de prueba")

    def test_concurrent_creations_use_unique_postgresql_sequence(self):
        reporter_id = self.reporter.pk
        def create(index):
            close_old_connections()
            try:
                self.assertEqual(connection.settings_dict["NAME"], "test_incidentes_gymplanet_db")
                report = Incident.objects.create(technician_id=reporter_id, equipment="Equipo", failure_type="Falla", description=f"Concurrente {index}", priority="low")
                return report.pk, report.code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(create, range(12)))
        self.assertEqual(len({code for _, code in results}), 12)
        self.assertEqual(len({pk for pk, _ in results}), 12)
        self.assertTrue(all(code == f"GP-{pk:06d}" for pk, code in results))

    def test_migration_preserves_old_codes_and_next_number(self):
        self.assertEqual(connection.settings_dict["NAME"], "test_incidentes_gymplanet_db")
        executor = MigrationExecutor(connection)
        executor.migrate([("incidents", "0017_protect_messaging_history")])
        try:
            old_apps = executor.loader.project_state([("incidents", "0017_protect_messaging_history")]).apps
            old = old_apps.get_model("incidents", "Incident").objects.create(technician_id=self.reporter.pk, equipment="Histórico", failure_type="Falla", description="Preservar", priority="low")
            executor = MigrationExecutor(connection)
            executor.migrate([("incidents", "0018_gymplanet_report_codes")])
            historical = Incident.objects.get(pk=old.pk)
            self.assertEqual(historical.code, f"SC-{old.pk:06d}")
            MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
            new = Incident.objects.create(technician=self.reporter, equipment="Nuevo", failure_type="Falla", description="Nuevo reporte", priority="low")
            self.assertEqual(new.pk, old.pk+1)
            self.assertEqual(new.code, f"GP-{new.pk:06d}")
        finally:
            MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())


@override_settings(AI_PROVIDER="mock", GEMINI_API_KEY="", TELEGRAM_BOT_TOKEN="")
class ConversationPolishTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="TEST-CORP", name="Sede autorizada")
        self.reporter = Technician.objects.create(full_name="Cuenta compartida de prueba")
        TechnicianChannel.objects.create(branch=self.branch, technician=self.reporter, provider="telegram", external_user_id="synthetic-polish")
        self.service = IncomingMessageService()
        self.number = 0

    def send(self, text):
        self.number += 1
        return self.service.process_text(provider="telegram", external_user_id="synthetic-polish", external_chat_id="synthetic-polish", external_message_id=f"polish:{self.number}", text=text, raw_payload={}, verified_transport=True)["processing"]

    def provider(self, **kwargs):
        provider = Mock()
        provider.extract_incident.return_value = IncidentExtractionResult(area="Cardio", location_context="Cardio", equipment="Trotadora 3", failure_type="Se detiene", description="Se detiene la cinta", **kwargs)
        self.service = IncomingMessageService(incident_processor=IncidentProcessor(ai_provider=provider))
        return provider

    def test_gemini_question_cannot_request_branch(self):
        provider = self.provider(clarification_question="¿En qué sucursal se encuentra esta trotadora?")
        result = self.send("Área Cardio, trotadora 3 se detiene")
        self.assertNotIn("sucursal", result["reply"].lower())
        self.assertIn("impacto", result["reply"].lower())
        self.assertIn(self.branch.name, provider.extract_incident.call_args.kwargs["previous_data"]["_authorized_branch"])

    def test_visible_priority_question_is_spanish(self):
        self.provider(operational_impact="Impide entrenar", clarification_question="Choose low, medium, high, critical")
        result = self.send("Área Cardio, trotadora 3 se detiene")
        self.assertEqual(result["missing_fields"], ["priority"])
        for word in ("Baja", "Media", "Alta", "Crítica"):
            self.assertIn(word, result["reply"])
        for word in ("low", "medium", "high", "critical"):
            self.assertNotIn(word, result["reply"])

    def test_ai_conversation_cannot_ask_for_branch(self):
        provider = Mock()
        provider.extract_incident.return_value = IncidentExtractionResult(intent="conversation", conversation_reply="¿En qué sucursal? Choose high")
        self.service = IncomingMessageService(incident_processor=IncidentProcessor(ai_provider=provider))
        result = self.send("Tengo una consulta operativa")
        self.assertNotIn("¿En qué sucursal", result["reply"])
        self.assertNotIn("Choose high", result["reply"])

    def test_explicit_out_of_service_is_not_asked_again(self):
        result = self.send("Área Cardio, trotadora 3 no enciende, está fuera de servicio y no puede utilizarse, prioridad alta")
        self.assertNotIn("operational_impact", result["missing_fields"])
        self.assertEqual(result["missing_fields"], [])
        self.assertFalse(Incident.objects.exists())
        self.send("Sí")
        self.assertTrue(Incident.objects.get().code.startswith("GP-"))

    def test_no_impact_is_inferred_from_failure_alone(self):
        result = self.send("Área Cardio, trotadora 3 no enciende, prioridad alta")
        self.assertIn("operational_impact", result["missing_fields"])

    def test_negative_out_of_service_does_not_assert_unavailability(self):
        from incidents.services.gymplanet_conversation import explicit_operational_impact
        self.assertIsNone(explicit_operational_impact("No está fuera de servicio"))

    def test_incident_summary_omits_legacy_irrelevant_fields(self):
        result = self.send("Área Cardio, trotadora 3 no enciende, impide entrenar, prioridad alta")
        self.assertNotIn("Familia:", result["reply"])
        self.assertNotIn("Solicitud:", result["reply"])
        self.assertIn("Prioridad: Alta", result["reply"])
        self.assertIn("¿Confirmas", result["reply"])

    def test_urgency_does_not_choose_priority(self):
        result = self.send("Área Cardio, trotadora 3 no enciende, fuera de servicio, urgente riesgo de caída")
        self.assertIn("priority", result["missing_fields"])
        self.assertIsNone(result["extracted_data"]["priority"])

    def test_gemini_prompt_includes_authorized_branch_and_spanish_rules(self):
        provider = GeminiAIProvider.__new__(GeminiAIProvider)
        prompt = provider._build_prompt("Reporte", {"_authorized_branch": "Sede autorizada (TEST-CORP)"})
        self.assertIn("Sede autorizada (TEST-CORP)", prompt)
        self.assertIn("Nunca preguntes por la sucursal", prompt)
        self.assertIn("Baja, Media, Alta o Crítica", prompt)

    def test_mock_and_request_flow_still_require_confirmation(self):
        result = self.send("Solicito reponer 5 colchonetas, impide entrenar, prioridad media")
        self.assertEqual(result["missing_fields"], [])
        self.assertFalse(Incident.objects.exists())
        self.send("Sí")
        self.assertEqual(Incident.objects.get().report_type, "request")

    def test_request_summary_omits_empty_catalog_and_technical_fields(self):
        result = self.send("Solicito reponer 5 colchonetas, impide entrenar, prioridad media")
        for text in ("No informado", "No corresponde", "Familia:", "Síntoma:", "Categoría:"):
            self.assertNotIn(text, result["reply"])

    def test_summary_does_not_repeat_same_area_and_location(self):
        result = self.send("Área Cardio, trotadora 3 no enciende, impide entrenar, prioridad alta")
        self.assertIn("• Área: Cardio", result["reply"])
        self.assertNotIn("• Ubicación/contexto: Cardio", result["reply"])
