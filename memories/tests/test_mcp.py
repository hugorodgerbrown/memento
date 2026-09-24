"""
The MCP layer (M2). Every tool is called through the MCP protocol, in-process,
so these tests see exactly what a client sees: schemas, descriptions, results
and errors. The last class goes through HTTP and the real ASGI entry point.
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx2
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from mcp import Client as MCPClient

from memories import services as s
from memories.mcp_server import current_client, http_app, mcp
from memories.models import Capture, Entry, Scope

SPEC = Path(__file__).resolve().parents[2] / "docs" / "mcp-tools.md"
TOOLS = [
    "remember",
    "save_digest",
    "recall",
    "timeline",
    "list_tags",
    "complete_reminder",
    "inbox",
    "close_capture",
    "forget",
]


def spec_description(tool: str) -> str:
    """The first blockquote under the tool's heading in docs/mcp-tools.md."""
    section = re.search(rf"^## {tool}\n(.*?)(?=^## |\Z)", SPEC.read_text(), re.M | re.S).group(1)
    lines, started = [], False
    for line in section.splitlines():
        if line.startswith(">"):
            started = True
            lines.append(line[2:] if line.startswith("> ") else line[1:])
        elif started:
            break
    return "\n".join(lines)


class MCPTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create(username="sam")
        self.stranger = User.objects.create(username="alex")
        self.client_row, self.token = s.create_client(
            self.me, "claude-code", scopes=[Scope.READ, Scope.WRITE, Scope.FORGET]
        )

    def call(self, tool, client=None, **arguments):
        """Call a tool through MCP as `client` (default: full access) and return the result."""

        async def go():
            reset = current_client.set(client or self.client_row)
            try:
                async with MCPClient(mcp) as c:
                    return await c.call_tool(tool, arguments)
            finally:
                current_client.reset(reset)

        return async_to_sync(go)()

    def ok(self, tool, **arguments):
        result = self.call(tool, **arguments)
        self.assertFalse(result.is_error, result.content)
        return result.structured_content

    def error(self, tool, **arguments) -> str:
        result = self.call(tool, **arguments)
        self.assertTrue(result.is_error, result.structured_content)
        return result.content[0].text

    def save(self, raw="Played padel with Fred", claim="Played padel with Fred.", **kw):
        kw.setdefault("kind", "memory")
        return self.ok("remember", raw_text=raw, claim=claim, **kw)["saved"][0]["id"]


class SurfaceTests(MCPTestCase):
    """Tool descriptions are the product (Principle 7)."""

    def list_tools(self):
        async def go():
            async with MCPClient(mcp) as c:
                return (await c.list_tools()).tools

        return {t.name: t for t in async_to_sync(go)()}

    def test_nine_tools_and_no_more(self):
        self.assertEqual(sorted(self.list_tools()), sorted(TOOLS))

    def test_descriptions_match_the_spec_exactly(self):
        tools = self.list_tools()
        for name in TOOLS:
            with self.subTest(tool=name):
                self.assertEqual(tools[name].description, spec_description(name))

    def test_remember_description_fits_in_500_characters(self):
        """claude.ai truncates at about 500: the capture policy must survive whole."""
        self.assertLessEqual(len(self.list_tools()["remember"].description), 500)

    def test_annotations_are_honest(self):
        tools = self.list_tools()
        for name in ("recall", "timeline", "list_tags", "inbox"):
            self.assertTrue(tools[name].annotations.read_only_hint, name)
        self.assertTrue(tools["forget"].annotations.destructive_hint)
        for name in ("remember", "save_digest", "complete_reminder", "close_capture"):
            self.assertFalse(tools[name].annotations.read_only_hint, name)
            self.assertFalse(tools[name].annotations.destructive_hint, name)

    def test_field_guidance_lives_in_the_schema(self):
        """It survives description truncation."""
        props = self.list_tools()["remember"].input_schema["properties"]
        self.assertIn("copied exactly", props["raw_text"]["description"])
        self.assertIn("never guess", props["happened_at"]["description"])

    def test_server_sends_instructions(self):
        async def go():
            async with MCPClient(mcp) as c:
                return c.instructions

        self.assertIn("remember", async_to_sync(go)())


class WriteTests(MCPTestCase):
    def test_every_save_returns_a_receipt(self):
        """Nothing is saved silently (Principle 9)."""
        result = self.ok("remember", raw_text="Slept badly", claim="Slept badly.", kind="memory")
        self.assertEqual(result["saved"][0]["claim"], "Slept badly.")
        self.assertFalse(result["duplicate"])
        self.assertIn("don't log that", result["say"])

    def test_client_name_comes_from_the_token_not_the_model(self):
        entry_id = self.save(model_name="opus")
        entry = Entry.objects.get(pk=entry_id)
        self.assertEqual((entry.client_name, entry.model_name), ("claude-code", "opus"))

    def test_same_observation_twice_is_one_entry(self):
        first = self.save()
        again = self.ok(
            "remember", raw_text="Played padel with Fred", claim="Padel.", kind="memory"
        )
        self.assertEqual((again["saved"][0]["id"], again["duplicate"]), (first, True))

    def test_errors_teach(self):
        message = self.error(
            "remember", raw_text="Renew the domain", claim="Renew domain", kind="reminder"
        )
        self.assertIn("needs due_at", message)
        message = self.error(
            "remember", raw_text="x", claim="x", kind="memory", happened_at="last tuesday"
        )
        self.assertIn("ISO 8601", message)

    def test_a_date_alone_means_day_precision(self):
        entry_id = self.save(happened_at="2026-09-10")
        entry = Entry.objects.get(pk=entry_id)
        self.assertEqual(entry.happened_precision, "day")
        (shown,) = self.ok("recall", ids=[entry_id])["entries"]
        self.assertEqual(shown["happened_at"], "2026-09-10")

    def test_change_closes_the_old_entry(self):
        """Changes append (Principle 4)."""
        left = self.save("PF in the left foot", "Plantar fasciitis in the left foot.")
        right = self.save(
            "PF has moved to the right foot",
            "Plantar fasciitis moved to the right foot.",
            supersedes=left,
            supersede_reason="change",
            happened_at="2026-09-14T11:21:00+00:00",
        )
        now = self.ok("recall", query="plantar fasciitis")["entries"]
        self.assertEqual([e["id"] for e in now], [right])
        history = self.ok("recall", query="plantar fasciitis", view="history")["entries"]
        old = next(e for e in history if e["id"] == left)
        self.assertEqual((old["superseded_by"], old["valid_until"][:10]), (right, "2026-09-14"))

    def test_digest_cites_its_sources(self):
        """Every answer cites (Principle 3)."""
        a, b = self.save(), self.save("Swam 2km", "Swam 2 km.")
        result = self.ok(
            "save_digest",
            raw_text="Two sessions of exercise.",
            claim="Exercised twice in September.",
            sources=[a, b],
            covers_from="2026-09-01",
            covers_to="2026-09-30",
        )
        (digest,) = self.ok("recall", ids=[result["saved"][0]["id"]])["entries"]
        self.assertEqual(sorted(digest["cites"]), sorted([a, b]))

    def test_complete_reminder(self):
        entry_id = self.save(
            "Remind me to renew the domain",
            "Renew the domain.",
            kind="reminder",
            due_at="2026-10-01T09:00:00+01:00",
        )
        done = self.ok("complete_reminder", entry_id=entry_id)["completed"]
        self.assertIsNotNone(done["completed_at"])
        self.assertFalse(self.call("complete_reminder", entry_id=entry_id).is_error)


class ReadTests(MCPTestCase):
    def test_recall_sees_only_the_owners_entries(self):
        mine = self.save()
        theirs = s.remember(
            self.stranger, client_name="c", kind="memory", raw_text="Padel", claim="Padel."
        )
        found = [e["id"] for e in self.ok("recall", query="padel")["entries"]]
        self.assertEqual(found, [mine])
        self.assertEqual(self.ok("recall", ids=[str(theirs.pk)])["entries"], [])

    def test_forgotten_ids_say_so(self):
        entry_id = self.save()
        s.forget(self.me, entry_id)
        result = self.ok("recall", ids=[entry_id])
        self.assertEqual((result["entries"], list(result["forgotten"])), ([], [entry_id]))

    def test_timeline_and_list_tags_count_mentions(self):
        self.save(tags=["padel", "person:fred"], happened_at="2026-09-10")
        self.save("Padel again", "Padel again.", tags=["padel"], happened_at="2026-09-12")
        tags = self.ok("list_tags")["tags"]
        self.assertEqual(tags[0], {"tag": "padel", "mentions": 2})
        buckets = self.ok("timeline", since="2026-09-01", until="2026-09-30", bucket="month")[
            "buckets"
        ]
        self.assertIn({"period": "2026-09-01", "tag": "padel", "mentions": 2}, buckets)


class InboxTests(MCPTestCase):
    def setUp(self):
        super().setUp()
        self.capture = Capture.objects.create(
            owner=self.me,
            external_id="rec_1",
            segments=[{"speaker": "Sam", "text": "PF is very mild on the right foot."}],
            text="PF is very mild on the right foot.",
            captured_at=datetime(2026, 9, 15, 9, 23, tzinfo=UTC),
            hints={"summary_markdown": "Foot update"},
        )

    def test_inbox_carries_current_entries_to_supersede(self):
        """0014: the client sees what the note might update."""
        existing = self.save("PF moved to the right foot", "Plantar fasciitis moved right.")
        result = self.ok("inbox")
        self.assertEqual(result["waiting"], 1)
        (capture,) = result["captures"]
        self.assertEqual(capture["transcript"], "PF is very mild on the right foot.")
        self.assertEqual([e["id"] for e in result["current_entries"]], [existing])
        self.assertEqual(result["current_entries_held_back"], 0)

    def test_distil_and_close(self):
        self.ok(
            "remember",
            raw_text="PF is very mild on the right foot",
            claim="Plantar fasciitis very mild in the right foot.",
            kind="memory",
            capture=str(self.capture.pk),
        )
        (capture,) = self.ok("inbox")["captures"]
        self.assertEqual(len(capture["entries"]), 1)
        self.ok("close_capture", capture_id=str(self.capture.pk), status="processed")
        self.assertEqual(self.ok("inbox")["waiting"], 0)

    def test_paraphrase_of_a_voice_note_is_refused(self):
        """Raw is sacred (Principle 1)."""
        message = self.error(
            "remember",
            raw_text="My plantar fasciitis is mild",
            claim="Mild plantar fasciitis.",
            kind="memory",
            capture=str(self.capture.pk),
        )
        self.assertIn("copied exactly", message)


class ForgetTests(MCPTestCase):
    """Forgetting is a right (Principle 5), and it is previewed first."""

    def old_entry(self):
        """Saved over 15 minutes ago, so "don't log that" no longer applies."""
        entry_id = self.save()
        later = timezone.now() + timedelta(hours=1)
        patcher = patch("memories.services.timezone.now", return_value=later)
        patcher.start()
        self.addCleanup(patcher.stop)
        return entry_id

    def test_preview_deletes_nothing(self):
        entry_id = self.old_entry()
        preview = self.ok("forget", entry_id=entry_id)
        self.assertEqual([e["id"] for e in preview["would_delete"]["entries"]], [entry_id])
        self.assertTrue(Entry.objects.filter(pk=entry_id).exists())

    def test_confirm_without_the_preview_token_is_refused(self):
        entry_id = self.old_entry()
        self.assertIn("confirm: false first", self.error("forget", entry_id=entry_id, confirm=True))
        self.assertTrue(Entry.objects.filter(pk=entry_id).exists())

    def test_confirm_with_the_token_deletes(self):
        entry_id = self.old_entry()
        token = self.ok("forget", entry_id=entry_id)["confirm_token"]
        self.ok("forget", entry_id=entry_id, confirm=True, confirm_token=token)
        self.assertFalse(Entry.objects.filter(pk=entry_id).exists())

    def test_token_is_void_if_the_plan_grew(self):
        """What is deleted is what the user was shown."""
        entry_id = self.old_entry()
        token = self.ok("forget", entry_id=entry_id)["confirm_token"]
        s.remember(
            self.me,
            client_name="c",
            kind="digest",
            raw_text="Sum",
            claim="Sum",
            sources=[entry_id],
            covers_from=timezone.now(),
            covers_to=timezone.now(),
        )
        self.error("forget", entry_id=entry_id, confirm=True, confirm_token=token)
        self.assertTrue(Entry.objects.filter(pk=entry_id).exists())

    def test_dont_log_that_skips_the_preview(self):
        """A single entry saved moments ago: one-step undo (Principle 9)."""
        entry_id = self.save()
        self.ok("forget", entry_id=entry_id, confirm=True)
        self.assertFalse(Entry.objects.filter(pk=entry_id).exists())


class ScopeTests(MCPTestCase):
    def test_forget_needs_its_own_scope(self):
        writer, _ = s.create_client(self.me, "chatgpt", scopes=[Scope.READ, Scope.WRITE])
        entry_id = self.save()
        result = self.call("forget", client=writer, entry_id=entry_id, confirm=True)
        self.assertTrue(result.is_error)
        self.assertIn("memento:forget", result.content[0].text)

    def test_read_only_client_cannot_write(self):
        reader, _ = s.create_client(self.me, "reader", scopes=[Scope.READ])
        result = self.call("remember", client=reader, raw_text="x", claim="x", kind="memory")
        self.assertTrue(result.is_error)
        self.assertFalse(Entry.objects.exists())


class ClientTokenTests(TestCase):
    def setUp(self):
        self.me = get_user_model().objects.create(username="sam")

    def test_only_a_hash_is_stored(self):
        client, token = s.create_client(self.me, "claude-code")
        self.assertTrue(token.startswith("mem_"))
        self.assertNotIn(token, str(client.__dict__))
        self.assertEqual(s.authenticate(token), client)

    def test_revoked_and_unknown_tokens_fail(self):
        client, token = s.create_client(self.me, "claude-code")
        self.assertIsNone(s.authenticate("mem_nope"))
        s.revoke_client(client)
        self.assertIsNone(s.authenticate(token))


class HTTPTests(TestCase):
    """Through config.asgi, exactly as gunicorn's uvicorn workers serve it."""

    def setUp(self):
        self.me = get_user_model().objects.create(username="sam")
        _, self.token = s.create_client(self.me, "claude-code")

    def request(self, go):
        app = http_app()  # a fresh session manager: each can only run once

        async def run():
            async with app.app.router.lifespan_context(app.app):
                transport = httpx2.ASGITransport(app=app)
                async with httpx2.AsyncClient(
                    transport=transport, base_url="http://localhost"
                ) as http:
                    return await go(http)

        return async_to_sync(run)()

    def test_no_token_no_access(self):
        async def go(http):
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            missing = await http.post("/mcp", json=body)
            wrong = await http.post("/mcp", json=body, headers={"Authorization": "Bearer mem_x"})
            return missing, wrong

        missing, wrong = self.request(go)
        self.assertEqual((missing.status_code, wrong.status_code), (401, 401))
        self.assertIn("Bearer", missing.headers["www-authenticate"])

    def test_remember_and_recall_over_http(self):
        from mcp.client.streamable_http import streamable_http_client

        async def go(http):
            http.headers["Authorization"] = f"Bearer {self.token}"
            transport = streamable_http_client("http://localhost/mcp", http_client=http)
            async with MCPClient(transport) as c:
                await c.call_tool(
                    "remember",
                    {"raw_text": "Swam 2km", "claim": "Swam 2 km.", "kind": "memory"},
                )
                return await c.call_tool("recall", {"query": "swam"})

        result = self.request(go)
        self.assertFalse(result.is_error, result.content)
        (entry,) = result.structured_content["entries"]
        self.assertEqual(entry["claim"], "Swam 2 km.")
        self.assertEqual(Entry.objects.get().client_name, "claude-code")
