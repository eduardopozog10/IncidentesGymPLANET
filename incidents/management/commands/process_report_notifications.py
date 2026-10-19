from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone
from incidents.models import NotificationOutbox
from incidents.services.incident_notification_service import dispatch_safely, maximum_attempts


class Command(BaseCommand):
    help="Simula la outbox. --apply procesa pendientes; --retry-failed incluye fallidas vencidas."

    def add_arguments(self,parser):
        parser.add_argument("--apply",action="store_true")
        parser.add_argument("--retry-failed",action="store_true")
        parser.add_argument("--limit",type=int,default=50)

    def handle(self,*args,**options):
        if not 1<=options["limit"]<=500: raise CommandError("El límite debe estar entre 1 y 500.")
        states=["pending","failed"] if options["retry_failed"] else ["pending"]
        rows=NotificationOutbox.objects.filter(status__in=states,attempts__lt=maximum_attempts()).filter(Q(next_attempt_at__isnull=True)|Q(next_attempt_at__lte=timezone.now())).order_by("pk")
        ids=list(rows.values_list("pk",flat=True)[:options["limit"]])
        self.stdout.write(f"Notificaciones elegibles: {len(ids)}")
        if not options["apply"]:
            self.stdout.write("Simulación: sin envíos ni modificaciones.")
            return
        if not settings.EMAIL_NOTIFICATIONS_ENABLED: raise CommandError("Notificaciones desactivadas.")
        sent=sum(bool(dispatch_safely(pk)) for pk in ids)
        self.stdout.write(f"Enviadas: {sent}. Revisar estados restantes en outbox.")
