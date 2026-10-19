"""All operational writes lock the report and record an authenticated actor."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from incidents.models import Incident, ReportActivity, ReportAssignment


def can_operate(user):
    return bool(user and user.is_authenticated and user.is_active and (user.is_superuser or user.has_perm("incidents.manage_operations")))


def authorize(user):
    if not can_operate(user):
        raise PermissionDenied("Acceso operativo no autorizado.")


def text(value, limit, required=False):
    value = str(value or "").strip()
    if (required and not value) or len(value) > limit:
        raise ValidationError("Completa el dato requerido respetando el largo máximo.")
    return value


@transaction.atomic
def manage_report(pk, actor, action, **data):
    authorize(actor)
    report = Incident.objects.select_for_update().get(pk=pk)
    before, after = {}, {}
    if action == "assign":
        previous = report.reportassignments.first()
        before = {key:getattr(previous,key) for key in ("responsible","person_name","notes")} if previous else {}
        after = dict(responsible=text(data.get("responsible"),200,True), person_name=text(data.get("person_name"),200), notes=text(data.get("notes"),4000))
        ReportAssignment.objects.create(report=report,actor=actor,**after)
        action = "reassigned" if previous else "assigned"
        description = "Responsable: " + after["responsible"] + (" — " + after["person_name"] if after["person_name"] else "")
    elif action == "comment":
        description = text(data.get("comment"),4000,True)
    elif action == "status":
        target = data.get("status")
        if target not in Incident.Status.values:
            raise ValidationError("Selecciona un estado válido.")
        if target == report.status:
            return report
        before = {"status":report.status}
        after = {"status":target}
        if target == "resolved":
            solution = text(data.get("solution"),4000,True)
            ReportActivity.objects.create(report=report,actor=actor,action="solution",description=solution)
            report.resolved_at = timezone.now()
        elif target != "closed":
            report.resolved_at = None
        reason = text(
            data.get("reason"),
            4000,
            required=(target == "cancelled"),
        )
        if reason:
            after["reason"] = reason
        report.status = target
        action = "reopened" if before["status"] in {"resolved","closed","cancelled"} and target not in {"resolved","closed","cancelled"} else target
        description = "Estado: " + dict(Incident.Status.choices)[target]
    else:
        raise ValidationError("Acción no reconocida.")
    report.save(update_fields=["status","resolved_at","updated_at"])
    ReportActivity.objects.create(report=report,actor=actor,action=action,description=description,before=before,after=after)
    return report
