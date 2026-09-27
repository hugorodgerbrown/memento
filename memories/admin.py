"""
Admin for dogfooding and debugging. Deletion is disabled everywhere: forgetting
must go through services.forget(), which cascades and leaves a tombstone.
"""

from django.contrib import admin

from .models import (
    Capture,
    Client,
    Entry,
    IngestLog,
    OAuthClient,
    OAuthToken,
    PocketLink,
    Profile,
    Tombstone,
)


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


@admin.register(OAuthClient)
class OAuthClientAdmin(NoDeleteAdmin):
    """
    Clients that asked for access (M8, 0025). Revoking a client's access means
    revoking its tokens below, or the Client row it acts as; this page is the
    record of who asked and what they registered.
    """

    list_display = ("client_name", "client_id", "from_metadata_document", "registered_at")
    list_filter = ("from_metadata_document",)
    search_fields = ("client_name", "client_id")
    readonly_fields = (
        "client_id",
        "client_name",
        "client_uri",
        "redirect_uris",
        "from_metadata_document",
        "metadata_fresh_until",
        "registered_at",
    )

    def has_add_permission(self, request):
        return False


@admin.register(OAuthToken)
class OAuthTokenAdmin(NoDeleteAdmin):
    """
    The consent screen promises the owner can revoke a connection at any time, so
    this page has to be able to do it. Set `revoked_at` and the token stops
    working on the next request; only its hash is stored, so it cannot be read.
    """

    list_display = ("oauth_client", "client", "use", "scopes", "expires_at", "revoked_at")
    list_filter = ("use",)
    readonly_fields = (
        "oauth_client",
        "client",
        "use",
        "scopes",
        "resource",
        "expires_at",
        "created_at",
    )
    actions = ["revoke_selected"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Revoke the selected tokens")
    def revoke_selected(self, request, queryset):
        from django.utils import timezone

        updated = queryset.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
        self.message_user(request, f"Revoked {updated} token(s).")
