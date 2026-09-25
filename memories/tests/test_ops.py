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
    """make connect-desktop (0023): Claude Desktop starts Memento itself. No network, no token."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        self.me = get_user_model().objects.create_superuser("hugo", "h@example.com", "pw")
        self.config = Path(tempfile.mkdtemp()) / "Claude" / "claude_desktop_config.json"

    def connect(self):
        import io

        from django.core.management import call_command

        out = io.StringIO()
        with mock.patch("shutil.which", return_value="/opt/homebrew/bin/uv"):
            call_command("connect_desktop", config=self.config, stdout=out)
        return out.getvalue()

    def test_claude_desktop_starts_memento_directly(self):
        out = self.connect()
        self.assertIn("quit Claude Desktop (Cmd-Q)", out)
        server = json.loads(self.config.read_text())["mcpServers"]["memento"]
        self.assertEqual(server["command"], "/opt/homebrew/bin/uv")
        self.assertEqual(server["args"][-3:], ["python", "manage.py", "mcp_stdio"])
        self.assertNotIn("token", json.dumps(server).lower())

    def test_its_entries_are_recorded_against_claude_desktop_which_can_forget(self):
        from memories.models import Client, Scope

        self.connect()
        self.connect()  # again: still one client
        client = Client.objects.get(owner=self.me)
        self.assertEqual(client.name, "claude-desktop")
        self.assertIn(Scope.FORGET, client.scopes)  # "don't log that" must work

    def test_a_revoked_desktop_client_is_restored(self):
        from memories import services
        from memories.management.commands.mcp_stdio import local_client
        from memories.models import Scope

        client, _ = services.create_client(self.me, "claude-desktop", scopes=[Scope.READ])
        services.revoke_client(client)
        client = local_client(self.me)
        self.assertIsNone(client.revoked_at)
        self.assertIn(Scope.FORGET, client.scopes)

    def test_other_servers_are_kept_and_the_old_settings_backed_up(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "k": 1}))
        self.connect()
        current = json.loads(self.config.read_text())
        self.assertEqual(set(current["mcpServers"]), {"other", "memento"})
        self.assertEqual(current["k"], 1)
        backup = self.config.with_name(self.config.name + ".before-memento")
        self.assertNotIn("memento", json.loads(backup.read_text())["mcpServers"])

    def test_broken_settings_change_nothing(self):
        from django.core.management.base import CommandError

        from memories.models import Client

        self.config.parent.mkdir(parents=True)
        self.config.write_text("{not json")
        with self.assertRaisesMessage(CommandError, "Nothing was changed"):
            self.connect()
        self.assertEqual(self.config.read_text(), "{not json")
        self.assertFalse(Client.objects.exists())
