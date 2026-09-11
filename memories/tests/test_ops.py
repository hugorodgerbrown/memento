"""Operational surface: health check and admin."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class OpsTests(TestCase):
    def test_healthz(self):
        response = self.client.get("/healthz")
        self.assertEqual((response.status_code, response.content), (200, b"ok"))

    def test_admin_pages_load_and_forbid_deletion(self):
        admin_user = get_user_model().objects.create_superuser("admin", "a@example.com", "pw")
        self.client.force_login(admin_user)
        for model in ("entry", "capture", "pocketlink", "ingestlog", "tombstone"):
            url = reverse(f"admin:memories_{model}_changelist")
            self.assertEqual(self.client.get(url).status_code, 200, model)
        from django.contrib import admin

        from memories.models import Entry

        self.assertFalse(admin.site._registry[Entry].has_delete_permission(None))
