"""Operational surface: health check, admin, and the settings a deploy depends on."""

import importlib
import json
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


class ConnectDesktopTests(TestCase):
    """make connect-desktop: one step from a running Memento to Claude Desktop using it."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        self.me = get_user_model().objects.create_superuser("hugo", "h@example.com", "pw")
        self.dir = Path(tempfile.mkdtemp())
        self.config = self.dir / "Claude" / "claude_desktop_config.json"
        self.token_file = self.dir / ".memento-claude-desktop"

    def connect(self):
        import io

        from django.core.management import call_command

        out = io.StringIO()
        call_command("connect_desktop", config=self.config, token_file=self.token_file, stdout=out)
        return out.getvalue()

    def token(self):
        return self.token_file.read_text().removeprefix("Authorization: Bearer ").strip()

    def test_it_connects_with_forget_and_a_private_token_file(self):
        from memories import services
        from memories.models import Scope

        out = self.connect()
        self.assertIn("quit Claude Desktop (Cmd-Q)", out)
        client = services.authenticate(self.token())
        self.assertEqual(client.owner, self.me)
        self.assertIn(Scope.FORGET, client.scopes)  # "don't log that" must work
        self.assertEqual(self.token_file.stat().st_mode & 0o777, 0o600)
        server = json.loads(self.config.read_text())["mcpServers"]["memento"]
        self.assertIn("http://127.0.0.1:8000/mcp", server["args"])
        self.assertEqual(server["args"][-1], str(self.token_file))

    def test_other_servers_are_kept_and_the_old_settings_backed_up(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "k": 1}))
        self.connect()
        settings = json.loads(self.config.read_text())
        self.assertEqual(set(settings["mcpServers"]), {"other", "memento"})
        self.assertEqual(settings["k"], 1)
        backup = self.config.with_name(self.config.name + ".before-memento")
        self.assertNotIn("memento", json.loads(backup.read_text())["mcpServers"])

    def test_running_it_again_replaces_the_token(self):
        from memories import services

        self.connect()
        first = self.token()
        self.connect()
        self.assertIsNone(services.authenticate(first))
        self.assertIsNotNone(services.authenticate(self.token()))

    def test_broken_settings_change_nothing(self):
        from django.core.management.base import CommandError

        from memories.models import Client

        self.config.parent.mkdir(parents=True)
        self.config.write_text("{not json")
        with self.assertRaisesMessage(CommandError, "Nothing was changed"):
            self.connect()
        self.assertEqual(self.config.read_text(), "{not json")
        self.assertFalse(self.token_file.exists() or Client.objects.exists())
