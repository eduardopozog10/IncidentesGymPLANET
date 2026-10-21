import uuid
from django.core.validators import MinValueValidator
from decimal import Decimal
from incidents.private_files import private_storage, request_upload_path

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.functions import Now
from django.utils import timezone


def relations_changed(instance, fields):
    """Only revalidate current placement when an association is actually changed."""
    if instance._state.adding:
        return True
    previous = type(instance).objects.filter(pk=instance.pk).values(*fields).first()
    return previous is None or any(previous[field] != getattr(instance, field) for field in fields)


class Branch(models.Model):
    name = models.CharField(
        max_length=150,
        verbose_name="nombre",
    )
    code = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="código",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="activa",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="fecha de creación",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="última actualización",
    )

    class Meta:
        db_table = "branches"
        verbose_name = "sucursal"
        verbose_name_plural = "sucursales"
        ordering = ["name", "code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class BranchArea(models.Model):
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="areas",
        verbose_name="sucursal",
    )
    name = models.CharField(max_length=150, verbose_name="nombre")
    code = models.CharField(max_length=50, verbose_name="código interno")
    is_active = models.BooleanField(default=True, verbose_name="activa")
    created_at = models.DateTimeField(
        auto_now_add=True, verbose_name="fecha de creación",
    )
    updated_at = models.DateTimeField(
        auto_now=True, verbose_name="última actualización",
    )

    class Meta:
        db_table = "branch_areas"
        verbose_name = "área de sucursal"
        verbose_name_plural = "áreas de sucursales"
        ordering = ["branch__name", "name", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "code"], name="unique_area_code_per_branch",
            ),
            # Referenced by the composite foreign key installed in migration 0012.
            models.UniqueConstraint(
                fields=["id", "branch"], name="unique_area_id_branch",
            ),
        ]

    def clean(self):
        super().clean()
        if self.pk and self.incidents.exclude(branch_id=self.branch_id).exists():
            raise ValidationError({
                "branch": "No se puede trasladar un área utilizada por incidencias de otra sucursal.",
            })
        if self.pk and self.assets.exclude(branch_id=self.branch_id).exists():
            raise ValidationError({"branch": "El área contiene activos de otra sucursal."})
        if self.pk and (
            self.asset_departures.exclude(from_branch_id=self.branch_id).exists()
            or self.asset_arrivals.exclude(to_branch_id=self.branch_id).exists()
        ):
            raise ValidationError({"branch": "El área forma parte del historial de movimientos."})

    def __str__(self):
        return f"{self.branch.code} / {self.code} - {self.name}"


class EquipmentCategory(models.Model):
    code = models.CharField(max_length=50, unique=True, verbose_name="código")
    name = models.CharField(max_length=150, verbose_name="nombre")
    description = models.TextField(blank=True, verbose_name="descripción")
    is_active = models.BooleanField(default=True, verbose_name="activa")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="fecha de creación")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="última actualización")

    class Meta:
        db_table = "equipment_categories"
        verbose_name = "categoría de equipamiento"
        verbose_name_plural = "categorías de equipamiento"
        ordering = ["name", "code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class RequestCategory(models.Model):
    code = models.CharField(max_length=50, unique=True, verbose_name="código")
    name = models.CharField(max_length=150, verbose_name="nombre")
    description = models.TextField(blank=True, verbose_name="descripción")
    is_active = models.BooleanField(default=True, verbose_name="activa")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="fecha de creación")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="última actualización")

    class Meta:
        db_table = "request_categories"
        verbose_name = "categoría de solicitud"
        verbose_name_plural = "categorías de solicitudes"
        ordering = ["name", "code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class GymAsset(models.Model):
    class OperationalStatus(models.TextChoices):
        OPERATIONAL = "operational", "Operativo"
        RESTRICTED = "restricted", "Operativo con restricciones"
        OUT_OF_SERVICE = "out_of_service", "Fuera de servicio"
        MAINTENANCE = "maintenance", "En mantenimiento"

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="assets", verbose_name="sucursal")
    branch_area = models.ForeignKey(BranchArea, on_delete=models.PROTECT, related_name="assets", null=True, blank=True, verbose_name="área")
    category = models.ForeignKey(EquipmentCategory, on_delete=models.PROTECT, related_name="assets", verbose_name="categoría")
    code = models.CharField(max_length=50, verbose_name="código interno")
    name = models.CharField(max_length=150, verbose_name="nombre")
    brand = models.CharField(max_length=150, blank=True, verbose_name="marca")
    model = models.CharField(max_length=150, blank=True, verbose_name="modelo")
    serial_number = models.CharField(max_length=150, blank=True, verbose_name="número de serie")
    description = models.TextField(blank=True, verbose_name="descripción")
    operational_status = models.CharField(max_length=20, choices=OperationalStatus.choices, default=OperationalStatus.OPERATIONAL, verbose_name="estado operativo")
    acquired_on = models.DateField(null=True, blank=True, verbose_name="fecha de incorporación")
    next_maintenance_at = models.DateTimeField(null=True, blank=True, verbose_name="próximo mantenimiento")
    notes = models.TextField(blank=True, verbose_name="observaciones")
    is_active = models.BooleanField(default=True, verbose_name="activo")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="fecha de creación")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="última actualización")

    class Meta:
        db_table = "gym_assets"
        verbose_name = "equipo o activo"
        verbose_name_plural = "equipos y activos"
        ordering = ["branch__name", "name", "code"]
        constraints = [
            models.UniqueConstraint(fields=["branch", "code"], name="unique_asset_code_per_branch"),
            models.CheckConstraint(condition=models.Q(operational_status__in=["operational", "restricted", "out_of_service", "maintenance"]), name="asset_valid_operational_status"),
        ]

    def clean(self):
        super().clean()
        if self.branch_area_id and not BranchArea.objects.filter(pk=self.branch_area_id, branch_id=self.branch_id).exists():
            raise ValidationError({"branch_area": "El área debe pertenecer a la sucursal del activo."})

    @transaction.atomic
    def transfer_to(self, branch, *, branch_area=None, code=None):
        """Move explicitly; PostgreSQL records the old and new placement atomically."""
        asset = type(self).objects.select_for_update().get(pk=self.pk)
        asset.branch, asset.branch_area = branch, branch_area
        if code is not None:
            asset.code = code
        asset.full_clean()
        asset.save(update_fields=["branch", "branch_area", "code", "updated_at"])
        self.refresh_from_db()
        return self

    def deactivate(self):
        self.is_active = False
        self.save(update_fields=["is_active", "updated_at"])

    def __str__(self):
        return f"{self.branch.code} / {self.code} - {self.name}"


class AssetMovement(models.Model):
    asset = models.ForeignKey(GymAsset, on_delete=models.PROTECT, related_name="movements", verbose_name="activo")
    from_branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="asset_departures", verbose_name="sucursal anterior")
    to_branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="asset_arrivals", verbose_name="sucursal nueva")
    from_area = models.ForeignKey(BranchArea, on_delete=models.PROTECT, related_name="asset_departures", null=True, blank=True, verbose_name="área anterior")
    to_area = models.ForeignKey(BranchArea, on_delete=models.PROTECT, related_name="asset_arrivals", null=True, blank=True, verbose_name="área nueva")
    from_code = models.CharField(max_length=50, verbose_name="código anterior")
    to_code = models.CharField(max_length=50, verbose_name="código nuevo")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="fecha")

    class Meta:
        db_table = "asset_movements"
        verbose_name = "movimiento de activo"
        verbose_name_plural = "movimientos de activos"
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.asset_id}: {self.from_code} → {self.to_code}"


class Technician(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    full_name = models.CharField(
        max_length=150,
        verbose_name="nombre completo",
    )
    whatsapp_number = models.CharField(
        max_length=20,
        unique=True,
        null=True,
        blank=True,
        verbose_name="número de WhatsApp",
        help_text=(
            "Campo temporal para conservar los datos existentes. "
            "Las nuevas integraciones usarán canales de mensajería."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="activo",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="fecha de creación",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="última actualización",
    )

    class Meta:
        db_table = "technicians"
        verbose_name = "reportante"
        verbose_name_plural = "reportantes"
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name


class TechnicianChannel(models.Model):
    class Provider(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        WHATSAPP = "whatsapp", "WhatsApp"

    branch = models.ForeignKey(
        Branch, on_delete=models.PROTECT, related_name="corporate_accounts",
        null=True, blank=True, verbose_name="sucursal autorizada",
        help_text="Cuenta corporativa compartida; no identifica necesariamente a una persona.",
    )

    technician = models.ForeignKey(
        Technician,
        on_delete=models.PROTECT,
        related_name="channels",
        verbose_name="reportante",
    )
    provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
        verbose_name="proveedor",
    )
    external_user_id = models.CharField(
        max_length=255,
        verbose_name="identificador externo del usuario",
    )
    external_chat_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="identificador externo del chat",
    )
    username = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="nombre de usuario externo",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="activo",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="fecha de creación",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="última actualización",
    )

    class Meta:
        db_table = "technician_channels"
        verbose_name = "canal de reportante"
        verbose_name_plural = "canales de reportantes"
        ordering = ["technician__full_name", "provider"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "external_user_id"],
                name="unique_user_per_messaging_provider",
            ),
        ]

    def clean(self):
        super().clean()
        self.external_user_id = self.external_user_id.strip()
        if not self.external_user_id:
            raise ValidationError({"external_user_id": "Se requiere una identidad del proveedor."})
        if self.is_active and self.branch_id and not self.branch.is_active:
            raise ValidationError({"branch": "La sucursal debe estar activa para autorizar la cuenta."})
        if self.pk and ChannelMessage.objects.filter(channel_account_id=self.pk).exists():
            previous = type(self).objects.get(pk=self.pk)
            if any(getattr(previous, f) != getattr(self, f) for f in ("provider", "external_user_id", "technician_id")):
                raise ValidationError("No se puede reasignar la identidad de una cuenta con historial. Desactívela y cree otra.")

    def __str__(self):
        return (
            f"{self.technician.full_name} "
            f"- {self.get_provider_display()}"
        )


class Incident(models.Model):
    class ReportType(models.TextChoices):
        INCIDENT = "incident", "Incidencia o falla"
        REQUEST = "request", "Solicitud operativa"

    class AssetCategory(models.TextChoices):
        VIDEO_CAMERAS = "video_cameras", "Video / Cámaras"
        AUDIO = "audio", "Audio"
        STUDIOS_PRODUCTION = "studios_production", "Estudios / Producción"
        SIGNAL_TRANSMISSION = "signal_transmission", "Señal / Transmisión"
        MOBILE_FIELD = "mobile_field", "Móviles / Terreno"
        NETWORKS = "networks", "Redes"
        SYSTEMS = "systems", "Sistemas"
        DIGITAL_STREAMING = "digital_streaming", "Digital / Streaming"
        INFRASTRUCTURE = "infrastructure", "Infraestructura"

    class Priority(models.TextChoices):
        LOW = "low", "Baja"
        MEDIUM = "medium", "Media"
        HIGH = "high", "Alta"
        CRITICAL = "critical", "Crítica"

    class Status(models.TextChoices):
        REPORTED = "reported", "Reportado"
        IN_PROGRESS = "in_progress", "En atención"
        RESOLVED = "resolved", "Resuelto"
        CLOSED = "closed", "Cerrado"
        CANCELLED = "cancelled", "Cancelado"

    code_prefix = models.CharField(max_length=2, default="GP", editable=False)

    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="incidents",
        null=True,
        blank=True,
        verbose_name="sucursal",
    )
    branch_area = models.ForeignKey(
        BranchArea,
        on_delete=models.PROTECT,
        related_name="incidents",
        null=True,
        blank=True,
        verbose_name="área de sucursal",
    )
    report_type = models.CharField(
        max_length=20,
        choices=ReportType.choices,
        default=ReportType.INCIDENT,
        verbose_name="tipo de reporte",
    )
    gym_asset = models.ForeignKey(
        GymAsset, on_delete=models.PROTECT, related_name="incidents",
        null=True, blank=True, verbose_name="activo registrado",
    )
    request_category = models.ForeignKey(
        RequestCategory, on_delete=models.PROTECT, related_name="requests",
        null=True, blank=True, verbose_name="categoría de solicitud",
    )
    request_kind = models.CharField(max_length=150, blank=True, verbose_name="tipo de requerimiento")
    technician = models.ForeignKey(
        Technician,
        on_delete=models.PROTECT,
        related_name="incidents",
        verbose_name="reportante",
    )
    unit = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="unidad",
    )

    area = models.CharField(
        max_length=150,
        blank=True,
        default="",
        verbose_name="área",
    )

    location_context = models.CharField(
        max_length=150,
        blank=True,
        default="",
        verbose_name="ubicación o contexto",
    )

    asset_category = models.CharField(
        max_length=30,
        choices=AssetCategory.choices,
        blank=True,
        default="",
        verbose_name="familia de equipo o servicio",
    )

    equipment = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="equipo o servicio afectado",
    )
    failure_type = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="tipo de falla",
    )
    description = models.TextField(
        verbose_name="descripción",
    )

    operational_impact = models.TextField(
        blank=True,
        default="",
        verbose_name="impacto operacional",
        help_text="Describe riesgos de lesiones, seguridad de usuarios, máquinas no disponibles, cierre de áreas o interrupción de la sede. La prioridad la define el reportante.",
    )

    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.MEDIUM,
        verbose_name="prioridad",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.REPORTED,
        verbose_name="estado",
    )
    original_message = models.TextField(
        verbose_name="mensaje original",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="fecha de creación",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="última actualización",
    )
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="fecha de resolución",
    )

    class Meta:
        permissions = [("manage_operations", "Gestionar operaciones de todas las sucursales")]
        db_table = "incidents"
        verbose_name = "incidente"
        verbose_name_plural = "incidentes"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(status__in=["reported","in_progress","resolved","closed","cancelled"]), name="incident_valid_status"),
            models.CheckConstraint(
                condition=models.Q(branch_area__isnull=True) | models.Q(branch__isnull=False),
                name="incident_area_requires_branch",
            ),
            models.CheckConstraint(
                condition=models.Q(report_type__in=["incident", "request"]),
                name="incident_valid_report_type",
            ),
            models.CheckConstraint(condition=models.Q(gym_asset__isnull=True) | models.Q(branch__isnull=False), name="incident_asset_requires_branch"),
            models.CheckConstraint(condition=models.Q(request_category__isnull=True) | models.Q(report_type="request"), name="request_category_requires_request"),
            models.CheckConstraint(condition=models.Q(priority__in=["low", "medium", "high", "critical"]), name="incident_valid_priority"),
            models.CheckConstraint(condition=models.Q(report_type="request") | (~models.Q(equipment="") & ~models.Q(failure_type="")), name="incident_technical_fields"),
        ]

    @transaction.atomic
    def save(self, *args, **kwargs):
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.report_type == self.ReportType.INCIDENT:
            missing = {key: "Este dato es necesario para una incidencia." for key in ("equipment", "failure_type") if not getattr(self, key)}
            if missing:
                raise ValidationError(missing)
        if self.branch_area_id is not None:
            if self.branch_id is None:
                raise ValidationError({
                    "branch_area": "Selecciona una sucursal antes de asignar un área.",
                })
            if not BranchArea.objects.filter(
                pk=self.branch_area_id, branch_id=self.branch_id,
            ).exists():
                raise ValidationError({
                    "branch_area": "El área debe pertenecer a la sucursal seleccionada.",
                })
        if self.request_category_id and self.report_type != self.ReportType.REQUEST:
            raise ValidationError({"request_category": "Solo las solicitudes admiten categoría de solicitud."})
        if self.gym_asset_id and relations_changed(self, ["gym_asset_id", "branch_id", "branch_area_id"]):
            asset = GymAsset.objects.filter(pk=self.gym_asset_id).first()
            if asset and (self.branch_id != asset.branch_id or (
                self.branch_area_id and asset.branch_area_id and self.branch_area_id != asset.branch_area_id
            )):
                raise ValidationError({"gym_asset": "El activo debe corresponder a la sucursal y área indicadas."})
        if self.pk:
            records = self.maintenance_records.all()
            if records.exclude(branch_id=self.branch_id).exists():
                raise ValidationError({"branch": "La sucursal está conservada en el historial de mantenimiento."})
            if self.gym_asset_id and records.exclude(asset_id=self.gym_asset_id).exists():
                raise ValidationError({"gym_asset": "El activo no coincide con el mantenimiento asociado."})

    @property
    def code(self):
        if self.pk is None:
            return "GP-PENDIENTE"

        return f"{self.code_prefix}-{self.pk:06d}"

    def __str__(self):
        return f"{self.code} - {self.equipment}"


class MaintenanceRecord(models.Model):
    class MaintenanceType(models.TextChoices):
        PREVENTIVE = "preventive", "Preventivo"
        CORRECTIVE = "corrective", "Correctivo"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Programado"
        IN_PROGRESS = "in_progress", "En progreso"
        COMPLETED = "completed", "Completado"
        CANCELLED = "cancelled", "Cancelado"

    asset = models.ForeignKey(GymAsset, on_delete=models.PROTECT, related_name="maintenance_records", verbose_name="activo")
    incident = models.ForeignKey(Incident, on_delete=models.PROTECT, related_name="maintenance_records", null=True, blank=True, verbose_name="reporte relacionado")
    # Snapshot: asset transfers must never rewrite the maintenance branch.
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="maintenance_records", editable=False, blank=True, verbose_name="sucursal del registro")
    maintenance_type = models.CharField(max_length=20, choices=MaintenanceType.choices, verbose_name="tipo")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCHEDULED, verbose_name="estado")
    scheduled_at = models.DateTimeField(null=True, blank=True, verbose_name="fecha programada")
    performed_at = models.DateTimeField(null=True, blank=True, verbose_name="fecha de realización")
    description = models.TextField(verbose_name="trabajo a realizar o realizado")
    result = models.TextField(blank=True, verbose_name="resultado")
    observations = models.TextField(blank=True, verbose_name="observaciones")
    responsible = models.CharField(max_length=200, blank=True, verbose_name="responsable o proveedor")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="fecha de creación")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="última actualización")

    class Meta:
        db_table = "maintenance_records"
        verbose_name = "registro de mantenimiento"
        verbose_name_plural = "registros de mantenimiento"
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(maintenance_type__in=["preventive", "corrective"]), name="maintenance_valid_type"),
            models.CheckConstraint(condition=models.Q(status__in=["scheduled", "in_progress", "completed", "cancelled"]), name="maintenance_valid_status"),
            models.CheckConstraint(condition=(models.Q(status="completed", performed_at__isnull=False) | (~models.Q(status="completed") & models.Q(performed_at__isnull=True))), name="maintenance_completion_date"),
            models.CheckConstraint(condition=models.Q(performed_at__isnull=True) | models.Q(performed_at__lte=Now()), name="maintenance_not_performed_future"),
        ]

    def clean(self):
        super().clean()
        if self.performed_at and self.performed_at > timezone.now():
            raise ValidationError({"performed_at": "La realización no puede estar en el futuro."})
        if (self.status == self.Status.COMPLETED) != (self.performed_at is not None):
            raise ValidationError({"performed_at": "Solo un mantenimiento completado debe indicar fecha de realización."})
        if not self._state.adding:
            previous = type(self).objects.filter(pk=self.pk).values("asset_id", "branch_id").first()
            if previous and self.asset_id != previous["asset_id"]:
                raise ValidationError({"asset": "El equipo de un mantenimiento registrado no puede cambiarse."})
            if previous and self.branch_id != previous["branch_id"]:
                raise ValidationError({"branch": "La sucursal histórica no puede reescribirse."})
        if self.asset_id and relations_changed(self, ["asset_id", "incident_id"]):
            asset = GymAsset.objects.filter(pk=self.asset_id).first()
            if asset:
                if self._state.adding:
                    self.branch_id = asset.branch_id
                if self.incident_id:
                    incident = Incident.objects.filter(pk=self.incident_id).first()
                    if incident and (incident.branch_id != self.branch_id or incident.gym_asset_id not in (None, asset.pk)):
                        raise ValidationError({"incident": "El reporte debe corresponder a la sucursal y al activo del mantenimiento."})

    def __str__(self):
        return f"{self.asset} - {self.get_maintenance_type_display()}"

    def save(self, *args, **kwargs):
        if self.asset_id and self._state.adding:
            self.branch_id = GymAsset.objects.values_list("branch_id", flat=True).get(pk=self.asset_id)
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"branch"}
        super().save(*args, **kwargs)


class ChannelMessage(models.Model):
    class Provider(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        WHATSAPP = "whatsapp", "WhatsApp"
        TEST = "test", "Prueba"

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Recibido"
        OUTBOUND = "outbound", "Enviado"

    class MessageType(models.TextChoices):
        TEXT = "text", "Texto"
        AUDIO = "audio", "Audio"
        IMAGE = "image", "Imagen"
        DOCUMENT = "document", "Documento"
        UNKNOWN = "unknown", "Desconocido"

    class Status(models.TextChoices):
        RECEIVED = "received", "Recibido"
        PROCESSING = "processing", "Procesando"
        PROCESSED = "processed", "Procesado"
        SENT = "sent", "Enviado"
        DELIVERED = "delivered", "Entregado"
        READ = "read", "Leído"
        FAILED = "failed", "Fallido"

    provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
        default=Provider.WHATSAPP,
        verbose_name="proveedor",
    )
    external_message_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name="identificador externo del mensaje",
    )
    external_sender_id = models.CharField(
        max_length=255,
        verbose_name="identificador externo del remitente",
    )
    external_chat_id = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="identificador externo del chat",
    )
    channel_account = models.ForeignKey(
        TechnicianChannel,
        on_delete=models.PROTECT,
        related_name="messages",
        null=True,
        blank=True,
        verbose_name="canal del reportante",
    )
    technician = models.ForeignKey(
        Technician,
        on_delete=models.SET_NULL,
        related_name="messages",
        null=True,
        blank=True,
        verbose_name="reportante",
    )
    incident = models.ForeignKey(
        Incident,
        on_delete=models.SET_NULL,
        related_name="messages",
        null=True,
        blank=True,
        verbose_name="incidente",
    )
    draft = models.ForeignKey(
        "IncidentDraft",
        on_delete=models.SET_NULL,
        related_name="messages",
        null=True,
        blank=True,
        verbose_name="borrador",
    )
    direction = models.CharField(
        max_length=10,
        choices=Direction.choices,
        verbose_name="dirección",
    )
    message_type = models.CharField(
        max_length=20,
        choices=MessageType.choices,
        default=MessageType.TEXT,
        verbose_name="tipo de mensaje",
    )
    content = models.TextField(
        blank=True,
        verbose_name="contenido",
    )
    raw_payload = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="datos originales",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
        verbose_name="estado",
    )
    error_message = models.TextField(
        blank=True,
        verbose_name="detalle del error",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="fecha de creación",
    )
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="fecha de procesamiento",
    )

    class Meta:
        db_table = "channel_messages"
        verbose_name = "mensaje de canal"
        verbose_name_plural = "mensajes de canales"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "external_message_id"],
                condition=models.Q(
                    external_message_id__isnull=False,
                ),
                name="unique_message_per_provider",
            ),
        ]

    def __str__(self):
        return (
            f"{self.get_provider_display()} "
            f"- {self.get_direction_display()} "
            f"- {self.external_sender_id}"
        )


class IncidentDraft(models.Model):
    class Status(models.TextChoices):
        COLLECTING = "collecting", "Recopilando información"
        READY = "ready", "Información completa"
        COMPLETED = "completed", "Incidente creado"
        EXPIRED = "expired", "Expirado"
        CANCELLED = "cancelled", "Cancelado"

    channel_account = models.ForeignKey(
        TechnicianChannel, on_delete=models.PROTECT, null=True, blank=True,
        related_name="drafts", verbose_name="cuenta de origen",
    )
    technician = models.ForeignKey(
        Technician,
        on_delete=models.PROTECT,
        related_name="incident_drafts",
        verbose_name="reportante",
    )
    initial_message = models.OneToOneField(
        ChannelMessage,
        on_delete=models.SET_NULL,
        related_name="started_draft",
        null=True,
        blank=True,
        verbose_name="mensaje inicial",
    )
    incident = models.OneToOneField(
        Incident,
        on_delete=models.SET_NULL,
        related_name="source_draft",
        null=True,
        blank=True,
        verbose_name="incidente creado",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.COLLECTING,
        verbose_name="estado",
    )
    extracted_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="datos extraídos",
    )
    missing_fields = models.JSONField(
        default=list,
        blank=True,
        verbose_name="campos faltantes",
    )
    last_question = models.TextField(
        blank=True,
        verbose_name="última pregunta realizada",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="fecha de creación",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="última actualización",
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="fecha de expiración",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="fecha de finalización",
    )

    class Meta:
        db_table = "incident_drafts"
        verbose_name = "borrador de incidente"
        verbose_name_plural = "borradores de incidentes"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel_account"],
                condition=models.Q(
                    channel_account__isnull=False, status__in=["collecting", "ready"],
                ),
                name="unique_active_draft_per_account",
            ),
        ]

    def clean(self):
        super().clean()
        if self.channel_account_id and self.channel_account.technician_id != self.technician_id:
            raise ValidationError({"channel_account": "La cuenta debe corresponder al reportante del borrador."})
        if self.channel_account_id and self.initial_message_id and self.initial_message.channel_account_id != self.channel_account_id:
            raise ValidationError({"initial_message": "El mensaje inicial pertenece a otra cuenta."})

    def __str__(self):
        return (
            f"Borrador de {self.technician.full_name} "
            f"- {self.get_status_display()}"
        )


class OperationalRecord(models.Model):
    report = models.ForeignKey(Incident, on_delete=models.PROTECT, related_name="%(class)ss")
    actor = models.ForeignKey("auth.User", null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ["-created_at", "-pk"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("El historial es inmutable; registra una nueva acción.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("El historial no puede eliminarse.")


class ReportAssignment(OperationalRecord):
    responsible = models.CharField(max_length=200)
    person_name = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)

    class Meta(OperationalRecord.Meta):
        db_table = "report_assignments"
        constraints = [models.CheckConstraint(condition=~models.Q(responsible=""), name="assignment_requires_responsible")]


class ReportActivity(OperationalRecord):
    action = models.CharField(max_length=30)
    description = models.TextField()
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)

    class Meta(OperationalRecord.Meta):
        db_table = "report_activity"


class NotificationOutbox(models.Model):
    report = models.OneToOneField(Incident, on_delete=models.PROTECT, related_name="creation_notification")
    recipient = models.EmailField(blank=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=12, default="pending", choices=[("pending","Pendiente"),("sending","En envío / revisar"),("sent","Enviado"),("failed","Fallido")])
    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    attempted_at = models.DateTimeField(null=True)
    sent_at = models.DateTimeField(null=True)
    next_attempt_at = models.DateTimeField(null=True)
    last_error = models.CharField(max_length=160, blank=True)

    class Meta:
        db_table = "notification_outbox"
        indexes = [models.Index(fields=["status","next_attempt_at"],name="outbox_pending_idx")]


class AssetActivity(models.Model):
    asset = models.ForeignKey(GymAsset,on_delete=models.PROTECT,related_name="activity")
    actor = models.ForeignKey("auth.User",on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    description = models.TextField()

    class Meta:
        db_table = "asset_activity"
        ordering = ["-created_at","-pk"]

    def save(self,*args,**kwargs):
        if not self._state.adding: raise ValidationError("Registra una nueva acción; historial inmutable.")
        return super().save(*args,**kwargs)

    def delete(self,*args,**kwargs):
        raise ValidationError("Historial protegido.")


class RequestProduct(models.Model):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=120, verbose_name="nombre oficial")
    presentation = models.CharField(max_length=120, blank=True, verbose_name="presentación documentada")
    purchase_unit = models.CharField(max_length=40, blank=True, verbose_name="unidad de compra documentada")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "request_products"
        ordering = ["name", "code"]
        verbose_name = "producto de solicitudes"
        verbose_name_plural = "productos de solicitudes"

    def __str__(self):
        return f"{self.name} ({self.code})"


class ProductAlias(models.Model):
    product = models.ForeignKey(RequestProduct, on_delete=models.CASCADE, related_name="aliases")
    name = models.CharField(max_length=120, verbose_name="sinónimo")
    normalized = models.CharField(max_length=150, editable=False)

    class Meta:
        db_table = "product_aliases"
        constraints = [models.UniqueConstraint(fields=["product", "normalized"], name="product_alias_unique")]

    def save(self, *args, **kwargs):
        from incidents.services.gymplanet_conversation import normalize
        self.normalized = normalize(self.name)
        if not self.normalized:
            raise ValidationError("El sinónimo debe contener un nombre.")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class RequestItem(models.Model):
    report = models.ForeignKey(Incident, on_delete=models.PROTECT, related_name="request_items")
    position = models.PositiveSmallIntegerField()
    product = models.ForeignKey(RequestProduct, on_delete=models.PROTECT, null=True, blank=True, related_name="requested_items")
    official_name = models.CharField(max_length=120, blank=True)
    original_name = models.CharField(max_length=120)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    unit = models.CharField(max_length=40, blank=True)
    presentation = models.CharField(max_length=120, blank=True)
    outside_catalog = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "request_items"
        ordering = ["position", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="request_item_positive"),
            models.CheckConstraint(condition=(models.Q(product__isnull=True, outside_catalog=True) | models.Q(product__isnull=False, outside_catalog=False)), name="request_item_catalog_flag"),
            models.UniqueConstraint(fields=["report", "position"], name="request_item_position"),
        ]

    def clean(self):
        super().clean()
        if self.report_id and self.report.report_type != "request":
            raise ValidationError("Solo las solicitudes pueden contener artículos.")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Los artículos confirmados son históricos e inmutables.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Artículo histórico protegido.")


class RequestDocument(models.Model):
    message = models.OneToOneField(ChannelMessage, on_delete=models.PROTECT, related_name="request_document")
    draft = models.ForeignKey(IncidentDraft, on_delete=models.PROTECT, related_name="request_documents")
    report = models.ForeignKey(Incident, on_delete=models.PROTECT, related_name="request_documents", null=True, blank=True)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    file = models.FileField(storage=private_storage, upload_to=request_upload_path, max_length=180)
    original_name = models.CharField(max_length=120)
    media_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    analysis = models.JSONField(default=dict)
    is_current = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "request_documents"
        constraints = [models.CheckConstraint(condition=models.Q(size__gt=0), name="request_document_nonempty")]

    def clean(self):
        super().clean()
        if self.report_id and (self.report.report_type != "request" or self.report.branch_id != self.branch_id):
            raise ValidationError("El documento debe pertenecer a la solicitud y sucursal autorizadas.")
        if self.draft_id and self.draft.channel_account_id and self.draft.channel_account.branch_id != self.branch_id:
            raise ValidationError("El documento no corresponde a la cuenta del borrador.")
