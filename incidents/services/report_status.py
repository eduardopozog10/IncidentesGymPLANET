from django.db import transaction
from django.utils import timezone
from incidents.models import Incident
from incidents.serializers import IncidentStatusUpdateSerializer


@transaction.atomic
def update_report_status(pk, value):
    serializer = IncidentStatusUpdateSerializer(data={"status": value})
    serializer.is_valid(raise_exception=True)
    report = Incident.objects.select_for_update().get(pk=pk)
    report.status = serializer.validated_data["status"]
    report.resolved_at = (report.resolved_at or timezone.now()) if report.status == Incident.Status.RESOLVED else None
    report.save(update_fields=["status", "resolved_at", "updated_at"])
    return report
