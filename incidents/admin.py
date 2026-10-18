from django.contrib import admin

from .models import (
    Branch,
    ChannelMessage,
    Incident,
    IncidentDraft,
    Technician,
    TechnicianChannel,
)


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("name", "code")
    list_per_page = 25


class TechnicianChannelInline(admin.TabularInline):
    model = TechnicianChannel
    extra = 0
    fields = (
        "provider",
        "external_user_id",
        "external_chat_id",
        "username",
        "is_active",
    )


@admin.register(Technician)
class TechnicianAdmin(admin.ModelAdmin):
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
class TechnicianChannelAdmin(admin.ModelAdmin):
    list_display = (
        "technician",
        "provider",
        "external_user_id",
        "external_chat_id",
        "username",
        "is_active",
    )
    list_filter = (
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
    list_select_related = ("technician",)
    ordering = (
        "technician__full_name",
        "provider",
    )
    list_per_page = 25


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = (
        "incident_code",
        "technician",
        "branch",
        "unit",
        "equipment",
        "failure_type",
        "priority",
        "status",
        "created_at",
    )
    list_filter = (
        "branch",
        "priority",
        "status",
        "created_at",
    )
    search_fields = (
        "branch__name",
        "branch__code",
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
    list_select_related = ("technician", "branch")
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
                    "equipment",
                    "failure_type",
                    "description",
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
class ChannelMessageAdmin(admin.ModelAdmin):
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
class IncidentDraftAdmin(admin.ModelAdmin):
    list_display = (
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
        "technician",
        "incident",
    )
    ordering = ("-updated_at",)
    date_hierarchy = "updated_at"
    list_per_page = 25
