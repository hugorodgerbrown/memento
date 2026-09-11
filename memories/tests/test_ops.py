"""Operational surface: health check, admin, and the settings a deploy depends on."""

import importlib
import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse


class OpsTests(TestCase):
    def test_healthz(self):
        response = self.client.get("/healthz")
        self.assertEqual((response.status_code, response.content), (200, b"ok"))

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_healthz_answers_instead_of_redirecting_to_https(self):
        # The host counts a 301 as healthy, so a redirect here would report a
        # healthy service without ever asking the database whether it is up.
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


class DeploySettingsTests(SimpleTestCase):
    """
    The hostname Render serves on only exists once the service does, so it can't
    be written into render.yaml. Losing this wiring turns every request, health
    checks included, into a 400 DisallowedHost.
    """

    def reloaded_with(self, **environ):
        import config.settings

        # Restore the ambient values for every later test in this process.
        self.addCleanup(importlib.reload, config.settings)
        with mock.patch.dict(os.environ, environ):
            return importlib.reload(config.settings)

    def test_render_hostname_is_allowed_and_csrf_trusted(self):
        settings = self.reloaded_with(
            DJANGO_DEBUG="0",
            DJANGO_SECRET_KEY="deploy-test",
            DJANGO_ALLOWED_HOSTS="",
            DJANGO_CSRF_TRUSTED_ORIGINS="",
            RENDER_EXTERNAL_HOSTNAME="memento-staging.onrender.com",
        )
        self.assertEqual(settings.ALLOWED_HOSTS, ["memento-staging.onrender.com"])
        self.assertEqual(settings.CSRF_TRUSTED_ORIGINS, ["https://memento-staging.onrender.com"])

    def test_production_is_secure_by_default(self):
        settings = self.reloaded_with(DJANGO_DEBUG="0", DJANGO_SECRET_KEY="deploy-test")
        self.assertTrue(settings.SECURE_SSL_REDIRECT)
        self.assertTrue(settings.SESSION_COOKIE_SECURE)
        self.assertTrue(settings.CSRF_COOKIE_SECURE)
        self.assertEqual(settings.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https"))
        # Hashed and compressed, which is what makes collectstatic a build step.
        self.assertEqual(
            settings.STORAGES["staticfiles"]["BACKEND"],
            "whitenoise.storage.CompressedManifestStaticFilesStorage",
        )
