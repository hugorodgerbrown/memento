from django.contrib import admin
from django.urls import path

from memories import oauth
from memories.views import healthz, pocket_webhook

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
    path("ingest/pocket/", pocket_webhook, name="pocket-webhook"),
    # OAuth 2.1 (M8, 0025). The well-known paths are fixed by RFC 9728 and
    # RFC 8414; the resource path is appended to the first, because the resource
    # is /mcp and not the site root.
    path(
        ".well-known/oauth-protected-resource/mcp",
        oauth.protected_resource_metadata,
        name="oauth-protected-resource",
    ),
    path(
        ".well-known/oauth-protected-resource",
        oauth.protected_resource_metadata,
        name="oauth-protected-resource-root",
    ),
    path(
        ".well-known/oauth-authorization-server",
        oauth.authorization_server_metadata,
        name="oauth-authorization-server",
    ),
    path("oauth/authorize", oauth.authorize, name="oauth-authorize"),
    path("oauth/token", oauth.token, name="oauth-token"),
    path("oauth/register", oauth.register, name="oauth-register"),
    path("oauth/revoke", oauth.revoke, name="oauth-revoke"),
]
