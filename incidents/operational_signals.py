from django.db.models.signals import post_save
from django.dispatch import receiver
from incidents.models import Incident, ReportActivity


@receiver(post_save, sender=Incident)
def record_creation(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        ReportActivity.objects.create(report=instance, action="created", description="Reporte recibido.", after={"status":instance.status})
        from incidents.services.incident_notification_service import queue_created_notification
        if not getattr(instance,'_defer_creation_notification',False):
            queue_created_notification(instance)
