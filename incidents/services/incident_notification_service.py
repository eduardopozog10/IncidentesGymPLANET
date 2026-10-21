"""One durable creation event per report. SMTP delivery is never part of report creation."""
import logging
import smtplib
from email.mime.image import MIMEImage
from datetime import timedelta
from urllib.parse import urlsplit
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from incidents.models import NotificationOutbox

logger = logging.getLogger(__name__)


def queue_created_notification(report):
    payload = {
        "code":report.code,"kind":report.get_report_type_display(),
        "branch":report.branch.name if report.branch_id else "",
        "area":report.branch_area.name if report.branch_area_id else report.area,
        "equipment":report.request_kind if report.report_type=="request" else report.equipment,
        "description":report.description,"priority":report.get_priority_display(),
        "status":report.get_status_display(),
        "justification":report.operational_impact,
        "category":report.request_category.name if report.request_category_id else '',
        "items":list(report.request_items.order_by('position').values('official_name','original_name','quantity','unit','presentation','outside_catalog')) if report.report_type=='request' else [],
        "documents":list(report.request_documents.filter(is_current=True).values('id','original_name','sha256','size','media_type')) if report.report_type=='request' else [],
        "created":timezone.localtime(report.created_at).strftime("%d/%m/%Y %H:%M"),
    }
    for item in payload['items']:
        item['quantity'] = str(item['quantity'])
    row, created = NotificationOutbox.objects.get_or_create(report=report,defaults={"payload":payload,"recipient":settings.INCIDENT_NOTIFICATION_EMAIL.strip()})
    if created and settings.EMAIL_NOTIFICATIONS_ENABLED:
        transaction.on_commit(lambda: dispatch_safely(row.pk), robust=True)
    return row


def detail_url(report_id):
    base=settings.APP_BASE_URL.rstrip("/")
    parts=urlsplit(base)
    if parts.scheme not in {"http","https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("APP_BASE_URL inválida")
    if parts.scheme=="http" and parts.hostname not in {"localhost","127.0.0.1","::1"}:
        raise ValueError("Se requiere HTTPS público")
    return base + reverse("report-detail",args=[report_id])


def maximum_attempts():
    return max(1,min(settings.NOTIFICATION_MAX_ATTEMPTS,10))


def dispatch_safely(pk):
    try:
        return dispatch(pk)
    except Exception:
        # The row stays durable for a later administrative pass. Never log SDK bodies.
        logger.error("No se pudo procesar outbox %s; requiere revisión.",pk)
        return False


def dispatch(pk):
    if not settings.EMAIL_NOTIFICATIONS_ENABLED:
        return False
    with transaction.atomic():
        row=NotificationOutbox.objects.select_for_update().get(pk=pk)
        if row.status not in {"pending","failed"} or row.attempts>=maximum_attempts() or (row.next_attempt_at and row.next_attempt_at>timezone.now()):
            return False
        row.recipient=row.recipient or settings.INCIDENT_NOTIFICATION_EMAIL.strip()
        try:
            validate_email(row.recipient)
            url=detail_url(row.report_id)
            if settings.EMAIL_BACKEND=="django.core.mail.backends.smtp.EmailBackend" and (not settings.EMAIL_HOST_USER or not settings.EMAIL_HOST_PASSWORD or not (settings.EMAIL_USE_TLS or settings.EMAIL_USE_SSL)):
                raise ValueError("SMTP incompleto")
        except Exception:
            row.last_error="Configuración incompleta o inválida; revisar destinatario, URL y SMTP."
            row.save(update_fields=["last_error"])
            return False
        row.status="sending"
        row.attempts+=1
        row.attempted_at=timezone.now()
        row.last_error=""
        row.save()
    # Claim committed before touching SMTP. Concurrent workers cannot claim it again.
    try:
        # Hide only the generated cleaning-item enumeration, never a factual description.
        hide_item_description = bool(
            row.payload.get('items') and row.payload.get('equipment') == 'Aseo y limpieza'
            and row.payload.get('description', '').lstrip().startswith('Solicitud de artículos:')
        )
        context={"report":row.payload,"url":url,"hide_item_description":hide_item_description}
        body=render_to_string("incidents/email/report_created.txt",context)
        html=render_to_string("incidents/email/report_created.html",context)
        email=EmailMultiAlternatives(subject=f"Gym Planet · {row.payload['code']} · {row.payload['priority']}",body=body,from_email=settings.DEFAULT_FROM_EMAIL,to=[row.recipient],headers={"Message-ID":f"<gymplanet-creation-{row.pk}@notifications.local>"})
        email.attach_alternative(html,"text/html")
        logo=MIMEImage((settings.BASE_DIR/"incidents/static/incidents/brand/gymplanet-favicon.png").read_bytes(),_subtype="png")
        logo.add_header("Content-ID","<gymplanet-brand>")
        logo.add_header("Content-Disposition","inline",filename="gymplanet.png")
        email.attach(logo)
        from incidents.models import RequestDocument
        from incidents.services.request_documents import original_bytes
        for attachment in row.payload.get('documents',[]):
            document = RequestDocument.objects.get(pk=attachment['id'],report_id=row.report_id,sha256=attachment['sha256'])
            email.attach(attachment['original_name'], original_bytes(document), attachment['media_type'])
        if email.send(fail_silently=False)!=1:
            raise RuntimeError("Backend sin confirmación")
    except Exception as error:
        # An interrupted connection may mean SMTP accepted the message. Do not retry blindly.
        uncertain=isinstance(error,(TimeoutError,ConnectionError,smtplib.SMTPServerDisconnected))
        NotificationOutbox.objects.filter(pk=pk,status="sending").update(status="sending" if uncertain else "failed",last_error=("Entrega incierta: " if uncertain else "Fallo de envío: ")+type(error).__name__,next_attempt_at=timezone.now()+timedelta(minutes=5))
        return False
    NotificationOutbox.objects.filter(pk=pk,status="sending").update(status="sent",sent_at=timezone.now(),last_error="",next_attempt_at=None)
    return True


def send_incident_created_email(incident_id):
    """Compatibility entry point; never create another logical event or backfill old reports."""
    row=NotificationOutbox.objects.filter(report_id=incident_id).first()
    return dispatch_safely(row.pk) if row else False
