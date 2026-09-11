from django.contrib import admin
from django.urls import path

from memories.views import healthz, pocket_webhook

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
    path("ingest/pocket/", pocket_webhook, name="pocket-webhook"),
]
