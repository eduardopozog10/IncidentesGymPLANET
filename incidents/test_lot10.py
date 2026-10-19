import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction, DatabaseError
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from incidents.ai.gemini_provider import GeminiAIProvider
from incidents.ai.mock_provider import MockAIProvider
from incidents.models import Branch, BranchArea, GymAsset, EquipmentCategory, MaintenanceRecord, Incident, IncidentDraft, Technician, TechnicianChannel, AssetActivity, NotificationOutbox
from incidents.services.inventory import save_asset, save_maintenance
from incidents.services.operations import manage_report
from incidents.services.incident_processor import IncidentProcessor
from incidents.services.incoming_message_service import IncomingMessageService


@override_settings(AI_PROVIDER="mock", EMAIL_NOTIFICATIONS_ENABLED=False, TELEGRAM_BOT_TOKEN="", GEMINI_API_KEY="", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class InventoryOperationsTests(TestCase):
    def setUp(self):
        self.assertEqual(connection.settings_dict["NAME"],"test_incidentes_gymplanet_db")
        self.user=get_user_model().objects.create_user(username="lot10-manager")
        self.user.user_permissions.add(Permission.objects.get(codename="manage_operations"))
        self.branch=Branch.objects.create(code="TEST-10",name="Sede de prueba")
        self.other=Branch.objects.create(code="TEST-OTHER",name="Otra sede de prueba")
        self.area=BranchArea.objects.create(branch=self.branch,code="CARDIO",name="Cardio")
        self.category=EquipmentCategory.objects.create(code="TEST-CARDIO",name="Cardio")
        self.reporter=Technician.objects.create(full_name="Cuenta de prueba")
        self.asset=GymAsset.objects.create(branch=self.branch,category=self.category,name="Bicicleta",code="TEST-10",brand="Marca de prueba",model="Modelo de prueba",serial_number="SERIE-TEST")
        self.client.force_login(self.user)

    def asset_data(self,**kw):
        values=dict(code="TEST-NEW",name="Trotadora",category=self.category.pk,branch=self.branch.pk,operational_status="operational",is_active=True)
        values.update(kw)
        return values

    def report(self,**kw):
        return Incident.objects.create(branch=self.branch,technician=self.reporter,equipment="Bicicleta",failure_type="Ruido",description="Prueba",priority="high",**kw)

    def test_register_and_edit_asset_with_audit(self):
        form=save_asset(self.user,self.asset_data())
        self.assertFalse(form.errors)
        asset=form.instance
        form=save_asset(self.user,self.asset_data(name="Nombre actualizado",notes="Observación real"),asset.pk)
        self.assertFalse(form.errors)
        self.assertEqual(asset.activity.count(),2)
        self.assertEqual(asset.activity.first().actor,self.user)

    def test_foreign_area_rejected(self):
        form=save_asset(self.user,self.asset_data(branch=self.other.pk,branch_area=self.area.pk))
        self.assertTrue(form.errors)
        self.assertFalse(GymAsset.objects.filter(code="TEST-NEW").exists())

    def test_code_uniqueness_per_branch_is_preserved(self):
        self.assertTrue(save_asset(self.user,self.asset_data(code=self.asset.code)).errors)
        self.assertFalse(save_asset(self.user,self.asset_data(code=self.asset.code,branch=self.other.pk)).errors)

    def test_unauthorized_mutations_denied(self):
        user=get_user_model().objects.create_user(username="lot10-denied")
        self.client.force_login(user)
        for url in (reverse("asset-create"),reverse("asset-edit",args=[self.asset.pk]),reverse("maintenance-create",args=[self.asset.pk]),reverse("asset-categories")):
            self.assertEqual(self.client.post(url,self.asset_data()).status_code,403)
        with self.assertRaises(PermissionDenied): save_asset(user,self.asset_data())

    def test_asset_creation_csrf(self):
        client=Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        url=reverse("asset-create")
        self.assertEqual(client.post(url,self.asset_data()).status_code,403)
        client.get(url)
        self.assertEqual(client.post(url,self.asset_data(),HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value).status_code,302)

    def test_search_brand_model_series(self):
        for value in ("Marca de prueba","Modelo de prueba","SERIE-TEST"):
            response=self.client.get(reverse("dashboard-assets"),{"search":value})
            self.assertContains(response,self.asset.code)

    def test_all_inventory_pages_and_category_management(self):
        for name,args in (("asset-create",[]),("asset-edit",[self.asset.pk]),("maintenance-create",[self.asset.pk]),("maintenance-dashboard",[]),("asset-categories",[])):
            self.assertEqual(self.client.get(reverse(name,args=args)).status_code,200)
        self.assertEqual(self.client.post(reverse("asset-categories"),{"code":"TEST-NEW","name":"Categoría validada","is_active":True}).status_code,302)

    def test_asset_link_same_branch_and_audit(self):
        report=self.report()
        manage_report(report.pk,self.user,"asset",asset_id=self.asset.pk)
        report.refresh_from_db()
        self.assertEqual(report.gym_asset_id,self.asset.pk)
        self.assertEqual(report.reportactivitys.get(action="asset_link").after,{"gym_asset_id":self.asset.pk})
        manage_report(report.pk,self.user,"asset",asset_id="")
        self.assertEqual(report.reportactivitys.filter(action="asset_link").count(),2)

    def test_asset_link_foreign_branch_rejected(self):
        report=self.report()
        foreign=GymAsset.objects.create(branch=self.other,category=self.category,name="Bicicleta",code="FOREIGN")
        with self.assertRaises(ValidationError): manage_report(report.pk,self.user,"asset",asset_id=foreign.pk)
        self.assertFalse(report.reportactivitys.filter(action="asset_link").exists())

    def test_asset_link_dashboard_and_state_independence(self):
        report=self.report()
        self.assertEqual(self.client.post(reverse("report-detail",args=[report.pk]),{"action":"asset","asset_id":self.asset.pk}).status_code,302)
        manage_report(report.pk,self.user,"status",status="resolved",solution="Reparación informada")
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.operational_status,"operational")

    def maintenance_data(self,**kw):
        values=dict(maintenance_type="preventive",status="completed",performed_at=timezone.now().isoformat(),description="Trabajo informado",responsible="Proveedor de prueba",result="Resultado informado")
        values.update(kw)
        return values

    def test_completed_maintenance_and_next_date(self):
        next_date=timezone.now()+timedelta(days=40)
        form=save_maintenance(self.user,self.asset.pk,self.maintenance_data(next_maintenance_at=next_date.isoformat()))
        self.assertFalse(form.errors)
        self.assertEqual(form.instance.branch,self.branch)
        self.assertEqual(form.instance.result,"Resultado informado")
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.next_maintenance_at,next_date)
        self.assertEqual(self.asset.operational_status,"operational")
        self.assertEqual(self.asset.activity.count(),1)

    def test_scheduled_plan_can_be_completed(self):
        form=save_maintenance(self.user,self.asset.pk,self.maintenance_data(status="scheduled",performed_at="",scheduled_at=(timezone.now()+timedelta(days=10)).isoformat()))
        self.assertFalse(form.errors)
        response=self.client.get(reverse("maintenance-dashboard"),{"planning":"soon"})
        self.assertContains(response,self.asset.code)
        form=save_maintenance(self.user,self.asset.pk,self.maintenance_data(),form.instance.pk)
        self.assertFalse(form.errors)
        self.assertEqual(self.asset.activity.count(),2)

    def test_future_completion_rejected(self):
        form=save_maintenance(self.user,self.asset.pk,self.maintenance_data(performed_at=(timezone.now()+timedelta(days=1)).isoformat()))
        self.assertTrue(form.errors)
        self.assertFalse(MaintenanceRecord.objects.exists())

    def test_maintenance_edit_wrong_asset_is_404(self):
        form=save_maintenance(self.user,self.asset.pk,self.maintenance_data())
        other=GymAsset.objects.create(branch=self.other,category=self.category,code="OTHER",name="Otro")
        self.assertEqual(self.client.get(reverse("maintenance-edit",args=[other.pk,form.instance.pk])).status_code,404)

    def test_recurrence_is_asset_identity_not_name_and_no_join_inflation(self):
        for _ in range(2): self.report(gym_asset=self.asset)
        other=GymAsset.objects.create(branch=self.branch,category=self.category,code="OTHER",name=self.asset.name)
        for _ in range(3): save_maintenance(self.user,self.asset.pk,self.maintenance_data())
        rows=list(self.client.get(reverse("maintenance-dashboard"),{"planning":"recurrent"}).context["page_obj"])
        self.assertEqual([(r.pk,r.recent_failures) for r in rows],[(self.asset.pk,2)])
        self.assertNotIn(other.pk,[r.pk for r in rows])

    def test_overdue_and_no_plan(self):
        self.asset.next_maintenance_at=timezone.now()-timedelta(days=1)
        self.asset.save()
        self.assertContains(self.client.get(reverse("maintenance-dashboard"),{"planning":"overdue"}),self.asset.code)
        self.assertEqual(self.client.get(reverse("maintenance-dashboard"),{"planning":"none"}).context["page_obj"].paginator.count,0)

    def test_asset_history_is_immutable(self):
        form=save_asset(self.user,self.asset_data())
        event=form.instance.activity.get()
        with self.assertRaises(DatabaseError), transaction.atomic(): AssetActivity.objects.filter(pk=event.pk).update(description="Alterado")

    def test_inventory_does_not_create_creation_emails(self):
        save_asset(self.user,self.asset_data())
        save_maintenance(self.user,self.asset.pk,self.maintenance_data())
        self.assertFalse(NotificationOutbox.objects.exists())


@override_settings(AI_PROVIDER="mock",EMAIL_NOTIFICATIONS_ENABLED=False,TELEGRAM_BOT_TOKEN="",GEMINI_API_KEY="",EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ConversationMemoryTests(TestCase):
    initial="Quiero reportar una incidencia de prueba en la sucursal Quilín. Una trotadora presenta una falla en la pantalla, que no enciende. El equipo está fuera de servicio y no puede utilizarse. Prioridad baja"

    def setUp(self):
        self.branch=Branch.objects.create(code="QUILIN",name="Quilín")
        self.reporter=Technician.objects.create(full_name="Cuenta de prueba")
        TechnicianChannel.objects.create(technician=self.reporter,branch=self.branch,provider="telegram",external_user_id="test-memory")
        self.service=IncomingMessageService()
        self.number=0

    def send(self,text,mid=None):
        self.number+=1
        return self.service.process_text(provider="telegram",external_user_id="test-memory",external_chat_id="test-memory",external_message_id=mid or str(self.number),text=text,raw_payload={},verified_transport=True)

    def gemini(self):
        provider=GeminiAIProvider.__new__(GeminiAIProvider)
        provider.model="synthetic"
        provider._create_interaction_with_retry=Mock(return_value=SimpleNamespace(output_text=json.dumps({"intent":"incident_report","equipment":None,"area":None,"location_context":None,"failure_type":"No enciende","description":self.initial,"operational_impact":"Fuera de servicio","priority":"low"})))
        self.service=IncomingMessageService(incident_processor=IncidentProcessor(ai_provider=provider))
        return provider

    def test_treadmill_area_regression_controlled_gemini(self):
        provider=self.gemini()
        first=self.send(self.initial)["processing"]
        self.assertIn("area",first["missing_fields"])
        self.assertEqual(first["extracted_data"]["equipment"],"Trotadora")
        second=self.send("Cardio")["processing"]
        self.assertEqual(second["missing_fields"],[])
        self.assertEqual(second["extracted_data"]["equipment"],"Trotadora")
        self.assertEqual(second["extracted_data"]["priority"],"low")
        self.assertEqual(second["extracted_data"]["failure_type"],"No enciende")
        self.assertEqual(provider._create_interaction_with_retry.call_count,1)
        self.assertFalse(Incident.objects.exists())
        self.send("Sí",mid="confirm")
        self.send("Sí",mid="confirm")
        self.assertEqual(Incident.objects.count(),1)
        self.assertEqual(NotificationOutbox.objects.count(),1)

    def test_mock_retains_equipment_when_area_is_added(self):
        self.send(self.initial)
        result=self.send("Cardio")["processing"]
        self.assertEqual(result["missing_fields"],[])
        self.assertIn("trotadora",result["extracted_data"]["equipment"].lower())

    def test_multiple_facts_do_not_need_extra_question(self):
        result=self.send("La trotadora 3 del área de Cardio de Quilín no enciende y está fuera de servicio. Prioridad alta")["processing"]
        self.assertEqual(result["missing_fields"],[])
        self.assertIn("3",result["extracted_data"]["equipment"])

    def test_explicit_corrections_do_not_freeze_old_data(self):
        self.send("Área Cardio, trotadora no enciende, impide entrenar, prioridad baja")
        result=self.send("No es una trotadora, es una elíptica")["processing"]
        self.assertIn("elíptica",result["extracted_data"]["equipment"])
        result=self.send("La prioridad es alta, no media")["processing"]
        self.assertEqual(result["extracted_data"]["priority"],"high")
        result=self.send("El área correcta es Musculación")["processing"]
        self.assertEqual(result["extracted_data"]["area"],"Musculación")

    def test_unknown_equipment_stays_missing(self):
        provider=self.gemini()
        result=self.send("Área Cardio, no enciende y está fuera de servicio. Prioridad baja")["processing"]
        self.assertIn("equipment",result["missing_fields"])

    def test_ambiguity_does_not_invent_literal_equipment(self):
        from incidents.services.gym_language import explicit_gym_equipment
        for value in ("Una trotadora o bicicleta", "Quizás una trotadora", "Un equipo"):
            self.assertIsNone(explicit_gym_equipment(value))
        for value in ("Trotadora 3","Bicicleta estática","Máquina de pecho","Polea alta","Rack de sentadillas"):
            self.assertIsNotNone(explicit_gym_equipment(value))

    def test_missing_impact_remains_required(self):
        result=self.send("Área Cardio, trotadora 3 no enciende, prioridad baja")["processing"]
        self.assertIn("operational_impact",result["missing_fields"])

    def test_cancel_and_branch_authorization(self):
        self.send(self.initial)
        branch=Branch.objects.create(code="TEST-OTHER",name="Sede no autorizada")
        self.assertEqual(self.send("Sucursal TEST-OTHER;")["processing"]["action"],"branch_mismatch")
        self.send("/cancelar")
        self.assertFalse(Incident.objects.exists())

    def test_ambiguous_inventory_does_not_block_text_report(self):
        category=EquipmentCategory.objects.create(code="TEST",name="Cardio")
        for code in ("TEST-1","TEST-2"): GymAsset.objects.create(branch=self.branch,category=category,code=code,name="Trotadora")
        result=self.send("Área Cardio, trotadora no enciende, impide entrenar, prioridad baja")["processing"]
        self.assertEqual(result["missing_fields"],[])
        self.assertIsNone(result["extracted_data"]["gym_asset_id"])
