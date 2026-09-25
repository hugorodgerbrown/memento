"""
Admin for dogfooding and debugging. Deletion is disabled everywhere: forgetting
must go through services.forget(), which cascades and leaves a tombstone.
"""

from django.contrib import admin

from .models import Capture, Client, Entry, IngestLog, PocketLink, Profile, Tombstone


class NoDeleteAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyAdmin(NoDeleteAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Entry)
class EntryAdmin(NoDeleteAdmin):
    list_display = ("claim", "kind", "happened_at", "valid_until", "client_name", "recorded_at")
    list_filter = ("kind", "client_name", "supersede_reason")
    search_fields = ("claim", "raw_text", "tags")
    date_hierarchy = "recorded_at"
    readonly_fields = (
        "id",
        "owner",
        "raw_text",
        "capture",
        "client_name",
        "model_name",
        "recorded_at",
        "supersedes",
        "supersede_reason",
        "external_ref",
    )


@admin.register(Capture)
class CaptureAdmin(NoDeleteAdmin):
    list_display = ("title", "status", "captured_at", "received_at", "external_id")
    list_filter = ("status", "source")
    search_fields = ("title", "text")
    readonly_fields = (
        "id",
        "owner",
        "source",
        "external_id",
        "segments",
        "text",
        "captured_at",
        "revisions",
        "hints",
        "received_at",
    )


@admin.register(PocketLink)
class PocketLinkAdmin(admin.ModelAdmin):
    list_display = ("owner", "pocket_user_id", "speaker_label", "one_speaker_is_me")


@admin.register(IngestLog)
class IngestLogAdmin(ReadOnlyAdmin):
    list_display = ("received_at", "event", "decision", "reason", "external_id")
    list_filter = ("decision", "event")


@admin.register(Tombstone)
class TombstoneAdmin(ReadOnlyAdmin):
    list_display = ("forgotten_at", "object_type", "object_id", "external_ref")


@admin.register(Client)
class ClientAdmin(NoDeleteAdmin):
    """Create clients with `manage.py create_client`, which shows the token once. Revoke here."""

    list_display = ("name", "owner", "mode", "token_prefix", "last_used_at", "revoked_at")
    list_filter = ("mode",)
    readonly_fields = ("owner", "name", "token_prefix", "created_at", "last_used_at")

    def has_add_permission(self, request):
        return False


@admin.register(Profile)
class ProfileAdmin(NoDeleteAdmin):
    list_display = ("owner", "timezone")
