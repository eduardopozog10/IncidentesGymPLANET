import secrets
import re
import unicodedata
from datetime import timedelta

from django.conf import settings

from django.db.models import (
    Avg,
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    Q,
)
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from incidents.messaging.telegram_update_handler import (
    TelegramUpdateHandler,
)
from incidents.models import Incident
from incidents.permissions import IsActiveSuperuser
from incidents.services.report_status import update_report_status
from incidents.permissions import CanManageOperations
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError as APIValidationError
from incidents.serializers import (
    IncidentSerializer,
    IncidentStatusUpdateSerializer,
)


def filter_queryset_by_date_range(queryset, request):
    date_from_value = request.query_params.get(
        "date_from"
    )
    date_to_value = request.query_params.get(
        "date_to"
    )

    date_from = (
        parse_date(date_from_value)
        if date_from_value
        else None
    )

    date_to = (
        parse_date(date_to_value)
        if date_to_value
        else None
    )

    if date_from:
        queryset = queryset.filter(
            created_at__date__gte=date_from,
        )

    if date_to:
        queryset = queryset.filter(
            created_at__date__lte=date_to,
        )

    return queryset

AREA_CANONICAL_LABELS = {
    "audio": "Audio",
    "produccion": "Producción",
    "operaciones": "Operaciones",
    "redes": "Redes",
    "sistemas": "Sistemas",
    "transmision": "Transmisión",
    "senal": "Señal",
    "video": "Video",
    "camaras": "Cámaras",
    "iluminacion": "Iluminación",
    "infraestructura": "Infraestructura",
    "digital": "Digital",
    "streaming": "Streaming",
    "estudios": "Estudios",
    "control tecnico": "Control Técnico",
    "tecnologia": "Tecnología",
}


def normalize_grouping_text(value: str) -> str:
    cleaned_value = " ".join(
        (value or "").strip().split()
    ).casefold()

    normalized_value = unicodedata.normalize(
        "NFD",
        cleaned_value,
    )

    return "".join(
        character
        for character in normalized_value
        if unicodedata.category(character) != "Mn"
    )


def build_normalized_area_data(queryset):
    area_counts = {}

    areas = queryset.exclude(
        area=""
    ).values_list(
        "area",
        flat=True,
    )

    for area in areas:
        cleaned_area = " ".join(
            str(area).strip().split()
        )

        if not cleaned_area:
            continue

        normalized_area = normalize_grouping_text(
            cleaned_area
        )

        if not normalized_area:
            continue

        if normalized_area not in area_counts:
            area_counts[normalized_area] = {
                "area": AREA_CANONICAL_LABELS.get(
                    normalized_area,
                    cleaned_area,
                ),
                "count": 0,
            }

        area_counts[normalized_area]["count"] += 1

    return sorted(
        area_counts.values(),
        key=lambda item: (
            -item["count"],
            normalize_grouping_text(item["area"]),
        ),
    )[:10]


class IncidentListView(generics.ListAPIView):
    permission_classes = [CanManageOperations]
    serializer_class = IncidentSerializer

    ALLOWED_ORDERING_FIELDS = {
        "created_at",
        "updated_at",
        "priority",
        "status",
        "area",
        "location_context",
        "asset_category",
        "unit",
        "equipment",
    }

    def get_queryset(self):
        queryset = Incident.objects.select_related(
            "technician"
        )

        queryset = filter_queryset_by_date_range(
            queryset,
            self.request,
        )

        status_value = self.request.query_params.get(
            "status"
        )
        priority = self.request.query_params.get(
            "priority"
        )
        area = self.request.query_params.get(
            "area"
        )

        location_context = self.request.query_params.get(
            "location_context"
        )

        asset_category = self.request.query_params.get(
            "asset_category"
        )

        unit = self.request.query_params.get(
            "unit"
        )

        equipment = self.request.query_params.get(
            "equipment"
        )
        search = self.request.query_params.get(
            "search"
        )
        ordering = self.request.query_params.get(
            "ordering",
            "-created_at",
        )

        if status_value:
            queryset = queryset.filter(
                status=status_value,
            )

        if priority:
            queryset = queryset.filter(
                priority=priority,
            )

        if area:
            queryset = queryset.filter(
                area__icontains=area,
            )

        if location_context:
            queryset = queryset.filter(
                location_context__icontains=location_context,
            )

        if asset_category:
            queryset = queryset.filter(
                asset_category=asset_category,
            )

        if unit:
            queryset = queryset.filter(
                unit__icontains=unit,
            )

        if equipment:
            queryset = queryset.filter(
                equipment__icontains=equipment,
            )

        if search:
            code = re.fullmatch(r"(SC|GP)-(\d{1,19})", search, re.I)
            code_query = Q(pk=int(code[2]), code_prefix=code[1].upper()) if code else Q(pk__in=[])
            queryset = queryset.filter(
                code_query | Q(area__icontains=search)
                | Q(location_context__icontains=search)
                | Q(unit__icontains=search)
                | Q(equipment__icontains=search)
                | Q(failure_type__icontains=search)
                | Q(description__icontains=search)
                | Q(operational_impact__icontains=search)
                | Q(original_message__icontains=search)
                | Q(
                    technician__full_name__icontains=search
                )
            )

        for key in ("branch", "report_type", "request_category"):
            value = self.request.query_params.get(key)
            if value:
                if key in ("branch", "request_category") and not value.isdecimal():
                    return queryset.none()
                queryset = queryset.filter(**{key: value})
        ordering_field = ordering.lstrip("-")

        if ordering_field not in self.ALLOWED_ORDERING_FIELDS:
            ordering = "-created_at"

        return queryset.order_by(ordering)


class IncidentSummaryView(APIView):
    permission_classes = [CanManageOperations]

    def get(self, request):
        queryset = Incident.objects.all()

        queryset = filter_queryset_by_date_range(
            queryset,
            request,
        )

        summary = queryset.aggregate(
            total=Count("id"),
            reported=Count(
                "id",
                filter=Q(
                    status=Incident.Status.REPORTED
                ),
            ),
            under_review=Count(
                "id",
                filter=Q(
                    status=Incident.Status.UNDER_REVIEW
                ),
            ),
            in_progress=Count(
                "id",
                filter=Q(
                    status=Incident.Status.IN_PROGRESS
                ),
            ),
            resolved=Count(
                "id",
                filter=Q(
                    status=Incident.Status.RESOLVED
                ),
            ),
            closed=Count(
                "id",
                filter=Q(
                    status=Incident.Status.CLOSED
                ),
            ),
            cancelled=Count(
                "id",
                filter=Q(
                    status=Incident.Status.CANCELLED
                ),
            ),
            low=Count(
                "id",
                filter=Q(
                    priority=Incident.Priority.LOW
                ),
            ),
            medium=Count(
                "id",
                filter=Q(
                    priority=Incident.Priority.MEDIUM
                ),
            ),
            high=Count(
                "id",
                filter=Q(
                    priority=Incident.Priority.HIGH
                ),
            ),
            critical=Count(
                "id",
                filter=Q(
                    priority=Incident.Priority.CRITICAL
                ),
            ),
        )

        return Response(
            {
                "total": summary["total"],
                "by_status": {
                    "reported": summary["reported"],
                    "under_review": summary[
                        "under_review"
                    ],
                    "in_progress": summary[
                        "in_progress"
                    ],
                    "resolved": summary["resolved"],
                    "closed": summary["closed"],
                    "cancelled": summary["cancelled"],
                },
                "by_priority": {
                    "low": summary["low"],
                    "medium": summary["medium"],
                    "high": summary["high"],
                    "critical": summary["critical"],
                },
            },
            status=status.HTTP_200_OK,
        )


class IncidentAnalyticsView(APIView):
    permission_classes = [CanManageOperations]

    def get(self, request):
        queryset = Incident.objects.all()

        queryset = filter_queryset_by_date_range(
            queryset,
            request,
        )

        open_statuses = [
            Incident.Status.REPORTED,
            Incident.Status.UNDER_REVIEW,
            Incident.Status.IN_PROGRESS,
        ]

        resolution_duration = ExpressionWrapper(
            F("resolved_at") - F("created_at"),
            output_field=DurationField(),
        )

        metrics = queryset.aggregate(
            total=Count("id"),
            open=Count(
                "id",
                filter=Q(
                    status__in=open_statuses
                ),
            ),
            resolved=Count(
                "id",
                filter=Q(
                    status=Incident.Status.RESOLVED
                ),
            ),
            critical=Count(
                "id",
                filter=Q(
                    priority=Incident.Priority.CRITICAL
                ),
            ),
            critical_open=Count(
                "id",
                filter=Q(
                    priority=Incident.Priority.CRITICAL,
                    status__in=open_statuses,
                ),
            ),
            average_resolution=Avg(
                resolution_duration,
                filter=Q(
                    resolved_at__isnull=False
                ),
            ),
        )

        average_resolution = metrics[
            "average_resolution"
        ]

        average_resolution_seconds = None

        if average_resolution is not None:
            average_resolution_seconds = int(
                average_resolution.total_seconds()
            )

        older_than_24_hours = queryset.filter(
            status__in=open_statuses,
            created_at__lt=(
                timezone.now()
                - timedelta(hours=24)
            ),
        ).count()

        by_status = queryset.values(
            "status"
        ).annotate(
            count=Count("id")
        ).order_by(
            "-count"
        )

        by_priority = queryset.values(
            "priority"
        ).annotate(
            count=Count("id")
        ).order_by(
            "-count"
        )

        area_data = build_normalized_area_data(
            queryset
        )

        by_location_context = queryset.exclude(
            location_context=""
        ).values(
            "location_context"
        ).annotate(
            count=Count("id")
        ).order_by(
            "-count",
            "location_context",
        )[:10]

        by_asset_category = queryset.exclude(
            asset_category=""
        ).values(
            "asset_category"
        ).annotate(
            count=Count("id")
        ).order_by(
            "-count",
            "asset_category",
        )

        by_equipment = queryset.exclude(
            equipment=""
        ).values(
            "equipment"
        ).annotate(
            count=Count("id")
        ).order_by(
            "-count",
            "equipment",
        )[:10]

        by_unit = queryset.exclude(
            unit=""
        ).values(
            "unit"
        ).annotate(
            count=Count("id")
        ).order_by(
            "-count",
            "unit",
        )[:10]

        daily_trend = queryset.annotate(
            day=TruncDate("created_at")
        ).values(
            "day"
        ).annotate(
            count=Count("id")
        ).order_by(
            "day"
        )

        status_labels = dict(
            Incident.Status.choices
        )

        priority_labels = dict(
            Incident.Priority.choices
        )

        asset_category_labels = dict(
            Incident.AssetCategory.choices
        )

        status_data = [
            {
                "value": item["status"],
                "label": status_labels.get(
                    item["status"],
                    item["status"],
                ),
                "count": item["count"],
            }
            for item in by_status
        ]

        priority_data = [
            {
                "value": item["priority"],
                "label": priority_labels.get(
                    item["priority"],
                    item["priority"],
                ),
                "count": item["count"],
            }
            for item in by_priority
        ]

        location_context_data = [
            {
                "location_context": item["location_context"],
                "count": item["count"],
            }
            for item in by_location_context
        ]

        asset_category_data = [
            {
                "value": item["asset_category"],
                "label": asset_category_labels.get(
                    item["asset_category"],
                    item["asset_category"],
                ),
                "count": item["count"],
            }
            for item in by_asset_category
        ]

        equipment_data = [
            {
                "equipment": item["equipment"],
                "count": item["count"],
            }
            for item in by_equipment
        ]

        unit_data = [
            {
                "unit": item["unit"],
                "count": item["count"],
            }
            for item in by_unit
        ]

        trend_data = [
            {
                "date": (
                    item["day"].isoformat()
                    if item["day"]
                    else None
                ),
                "count": item["count"],
            }
            for item in daily_trend
        ]

        return Response(
            {
                "metrics": {
                    "total": metrics["total"],
                    "open": metrics["open"],
                    "resolved": metrics["resolved"],
                    "critical": metrics["critical"],
                    "critical_open": metrics[
                        "critical_open"
                    ],
                    "average_resolution_seconds": (
                        average_resolution_seconds
                    ),
                    "open_over_24_hours": (
                        older_than_24_hours
                    ),
                },
                "by_status": status_data,
                "by_priority": priority_data,
                "by_area": area_data,
                "by_location_context": location_context_data,
                "by_asset_category": asset_category_data,
                "by_equipment": equipment_data,
                "by_unit": unit_data,
                "daily_trend": trend_data,
            },
            status=status.HTTP_200_OK,
        )


class IncidentDetailView(generics.RetrieveAPIView):
    permission_classes = [CanManageOperations]
    serializer_class = IncidentSerializer

    def get_queryset(self):
        return Incident.objects.select_related(
            "technician"
        )

    def patch(self, request, *args, **kwargs):
        incident = self.get_object()

        serializer = IncidentStatusUpdateSerializer(
            data=request.data,
        )
        serializer.is_valid(raise_exception=True)

        try:
            incident = update_report_status(incident.pk, serializer.validated_data["status"], actor=request.user, solution=request.data.get("solution", ""), reason=request.data.get("reason", ""))
        except DjangoValidationError as error:
            raise APIValidationError(error.messages)

        response_serializer = IncidentSerializer(
            incident,
        )

        return Response(
            response_serializer.data,
            status=status.HTTP_200_OK,
        )


class IncomingTextMessageTestView(APIView):
    permission_classes = [IsActiveSuperuser]

    def post(self, request):
        # HTTP client IDs cannot authenticate a Telegram/WhatsApp sender.
        if not settings.DEBUG or not settings.ENABLE_TEST_MESSAGE_ENDPOINT:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(
            {"detail": "La recepción exige un transporte verificado. Use el adaptador de mensajería; este endpoint no permite suplantar cuentas."},
            status=status.HTTP_403_FORBIDDEN,
        )


class TelegramWebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        if not settings.TELEGRAM_WEBHOOK_ENABLED or not settings.TELEGRAM_WEBHOOK_SECRET:
            return Response(status=status.HTTP_404_NOT_FOUND)
        provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not provided or not secrets.compare_digest(provided.encode("utf-8"), settings.TELEGRAM_WEBHOOK_SECRET.encode("utf-8")):
            return Response(status=status.HTTP_403_FORBIDDEN)
        try:
            result = TelegramUpdateHandler().handle(request.data)
        except Exception:
            return Response({"detail": "No fue posible procesar la actualización de Telegram."}, status=500)
        return Response({"ok": True, "result": result})
