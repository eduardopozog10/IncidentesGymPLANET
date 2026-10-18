from django.contrib import admin

from .forms import GymAssetAdminForm, IncidentAdminForm, MaintenanceAdminForm
from .models import (
    AssetMovement,
    Branch,
    BranchArea,
    ChannelMessage,
    EquipmentCategory,
    GymAsset,
    Incident,
    IncidentDraft,
    MaintenanceRecord,
    RequestCategory,
    Technician,
    TechnicianChannel,
)


class SecureModelAdmin(admin.ModelAdmin):
    """Until branch-scoped supervision exists, operational access is superuser-only."""
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)


@admin.register(Branch)
class BranchAdmin(SecureModelAdmin):
    list_display = ("name", "code", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("name", "code")
    list_per_page = 25


@admin.register(BranchArea)
class BranchAreaAdmin(SecureModelAdmin):
    list_display = ("name", "code", "branch", "is_active", "created_at")
    list_filter = ("branch", "is_active")
    search_fields = ("name", "code", "branch__name", "branch__code")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("branch",)
    list_per_page = 25


class CategoryAdmin(SecureModelAdmin):
    list_display = ("name", "code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code", "description")
    readonly_fields = ("created_at", "updated_at")


admin.site.register(EquipmentCategory, CategoryAdmin)
admin.site.register(RequestCategory, CategoryAdmin)


class MaintenanceInline(admin.TabularInline):
    model = MaintenanceRecord
    extra = 0
    fields = ("maintenance_type", "status", "branch", "scheduled_at", "performed_at", "responsible")
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class AssetMovementInline(admin.TabularInline):
    model = AssetMovement
    extra = 0
    fields = ("from_branch", "from_area", "from_code", "to_branch", "to_area", "to_code", "created_at")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(GymAsset)
class GymAssetAdmin(SecureModelAdmin):
    form = GymAssetAdminForm
    list_display = ("code", "name", "branch", "branch_area", "category", "operational_status", "is_active")
    list_filter = ("branch", "branch_area", "category", "operational_status", "is_active")
    search_fields = ("code", "name", "brand", "model", "serial_number")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("branch", "branch_area", "category")
    inlines = (MaintenanceInline, AssetMovementInline)


@admin.register(MaintenanceRecord)
class MaintenanceRecordAdmin(SecureModelAdmin):
    form = MaintenanceAdminForm
    list_display = ("asset", "branch", "maintenance_type", "status", "scheduled_at", "performed_at", "responsible")
    list_filter = ("branch", "maintenance_type", "status", "scheduled_at")
    search_fields = ("asset__code", "asset__name", "description", "responsible")
    readonly_fields = ("branch", "created_at", "updated_at")
    list_select_related = ("asset__branch", "branch", "incident")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AssetMovement)
class AssetMovementAdmin(SecureModelAdmin):
    list_display = ("asset", "from_branch", "to_branch", "created_at")
    list_filter = ("from_branch", "to_branch")
    search_fields = ("asset__code", "asset__name", "from_code", "to_code")
    readonly_fields = tuple(field.name for field in AssetMovement._meta.fields)
    list_select_related = ("asset__branch", "from_branch", "to_branch")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class TechnicianChannelInline(admin.TabularInline):
    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request, obj=None):
        return self.has_view_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self.has_view_permission(request, obj)

    model = TechnicianChannel
    extra = 0
    fields = (
        "branch",
        "provider",
        "external_user_id",
        "external_chat_id",
        "username",
        "is_active",
    )


@admin.register(Technician)
class TechnicianAdmin(SecureModelAdmin):
    list_display = (
        "full_name",
        "whatsapp_number",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active",)
    search_fields = (
        "full_name",
        "whatsapp_number",
        "channels__external_user_id",
        "channels__username",
    )
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
    )
    ordering = ("full_name",)
    list_per_page = 25
    inlines = (TechnicianChannelInline,)


@admin.register(TechnicianChannel)
class TechnicianChannelAdmin(SecureModelAdmin):
    list_display = (
        "technician",
        "branch",
        "provider",
        "external_user_id",
        "external_chat_id",
        "username",
        "is_active",
    )
    list_filter = (
        "branch",
        "provider",
        "is_active",
    )
    search_fields = (
        "technician__full_name",
        "external_user_id",
        "external_chat_id",
        "username",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )
    list_select_related = ("technician", "branch")
    ordering = (
        "technician__full_name",
        "branch",
        "provider",
    )
    list_per_page = 25


@admin.register(Incident)
class IncidentAdmin(SecureModelAdmin):
    form = IncidentAdminForm
    list_display = (
        "incident_code",
        "technician",
        "branch",
        "branch_area",
        "report_type",
        "gym_asset",
        "request_category",
        "unit",
        "equipment",
        "failure_type",
        "priority",
        "status",
        "created_at",
    )
    list_filter = (
        "branch",
        "branch_area",
        "report_type",
        "request_category",
        "priority",
        "status",
        "created_at",
    )
    search_fields = (
        "branch__name",
        "branch__code",
        "branch_area__name",
        "branch_area__code",
        "gym_asset__code",
        "gym_asset__name",
        "unit",
        "equipment",
        "failure_type",
        "description",
        "original_message",
        "technician__full_name",
        "technician__whatsapp_number",
        "technician__channels__external_user_id",
    )
    readonly_fields = (
        "incident_code",
        "created_at",
        "updated_at",
        "resolved_at",
    )
    list_select_related = ("technician", "branch", "branch_area__branch", "gym_asset__branch", "request_category")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    list_per_page = 25

    fieldsets = (
        (
            "Identificación",
            {
                "fields": (
                    "incident_code",
                    "technician",
                    "branch",
                    "branch_area",
                    "report_type",
                    "gym_asset",
                    "request_category",
                    "request_kind",
                    "status",
                    "priority",
                )
            },
        ),
        (
            "Información del incidente",
            {
                "fields": (
                    "unit",
                    "area",
                    "equipment",
                    "failure_type",
                    "description",
                    "operational_impact",
                    "original_message",
                )
            },
        ),
        (
            "Fechas",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                    "resolved_at",
                )
            },
        ),
    )

    @admin.display(
        description="Código",
        ordering="id",
    )
    def incident_code(self, obj):
        return obj.code


@admin.register(ChannelMessage)
class ChannelMessageAdmin(SecureModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    list_display = (
        "created_at",
        "provider",
        "external_sender_id",
        "direction",
        "message_type",
        "status",
        "technician",
        "incident",
    )
    list_filter = (
        "provider",
        "direction",
        "message_type",
        "status",
        "created_at",
    )
    search_fields = (
        "external_message_id",
        "external_sender_id",
        "external_chat_id",
        "content",
        "technician__full_name",
    )
    readonly_fields = (
        "created_at",
        "processed_at",
    )
    list_select_related = (
        "technician",
        "incident",
        "channel_account",
    )
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    list_per_page = 50


@admin.register(IncidentDraft)
class IncidentDraftAdmin(SecureModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    list_display = (
        "channel_account",
        "technician",
        "status",
        "incident",
        "updated_at",
        "expires_at",
    )
    list_filter = (
        "status",
        "updated_at",
    )
    search_fields = (
        "technician__full_name",
        "technician__whatsapp_number",
        "technician__channels__external_user_id",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
        "completed_at",
    )
    list_select_related = (
        "channel_account",
        "technician",
        "incident",
    )
    ordering = ("-updated_at",)
    date_hierarchy = "updated_at"
    list_per_page = 25
