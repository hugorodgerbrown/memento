"""
The contract (M3, ADR 0013 part 1): the server is the arbiter of time and
consistency across clients, and no capture is ever lost to a weak client.
Each test is named for the rule it protects.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from mcp import Client as MCPClient

from memories import services as s
from memories.mcp_server import current_client, mcp
from memories.models import Capture, ClientMode, Entry, Profile, Scope

# Thursday 24 Sep 2026, 23:30 UTC: already Friday in London (BST, UTC+1).
NOW = datetime(2026, 9, 24, 23, 30, tzinfo=UTC)


class ContractTestCase(TestCase):
    def setUp(self):
        self.me = get_user_model().objects.create(username="sam")
        Profile.objects.create(owner=self.me, timezone="Europe/London")
        self.client_row, _ = s.create_client(
            self.me, "claude-code", scopes=[Scope.READ, Scope.WRITE]
        )
        patcher = patch("django.utils.timezone.now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, tool, client=None, **arguments):
        async def go():
            reset = current_client.set(client or self.client_row)
            try:
                async with MCPClient(mcp) as c:
                    return await c.call_tool(tool, arguments)
            finally:
                current_client.reset(reset)

        return async_to_sync(go)()

    def remember(self, **kw):
        kw.setdefault("client_name", "c")
        kw.setdefault("kind", "memory")
        kw.setdefault("claim", kw.get("raw_text", ""))
        return s.remember(self.me, **kw)


class TimeTests(ContractTestCase):
    def test_time_zone_lives_on_the_profile(self):
        self.assertEqual(str(s.user_timezone(self.me)), "Europe/London")
        stranger = get_user_model().objects.create(username="alex")
        self.assertEqual(str(s.user_timezone(stranger)), "UTC")

    def test_every_tool_result_includes_now_in_the_users_time_zone(self):
        for tool in ("list_tags", "inbox", "recall", "timeline"):
            with self.subTest(tool=tool):
                result = self.call(tool).structured_content
                self.assertEqual(result["now"], "2026-09-25T00:30:00+01:00")

    def test_claim_with_relative_time_is_rejected_with_todays_date(self):
        for claim in ("Slept badly last night.", "Physio yesterday.", "Swim tomorrow morning."):
            with self.subTest(claim=claim), self.assertRaises(ValidationError) as e:
                self.remember(raw_text=claim, claim=claim)
            self.assertIn("Friday 25 Sep 2026", str(e.exception))
            self.assertIn("Europe/London", str(e.exception))

    def test_dated_claims_are_fine(self):
        entry = self.remember(
            raw_text="Slept badly last night", claim="Slept badly on 24 Sep 2026."
        )
        self.assertEqual(entry.claim, "Slept badly on 24 Sep 2026.")

    def test_memory_dated_in_the_future_is_rejected(self):
        with self.assertRaises(ValidationError) as e:
            self.remember(
                raw_text="Swam 2km",
                claim="Swam 2 km on 26 Sep 2026.",
                happened_at=NOW + timedelta(days=1),
                happened_precision="day",
            )
        self.assertIn("Friday 25 Sep 2026", str(e.exception))

    def test_a_scheduled_change_may_be_dated_in_the_future(self):
        """0009: a future-dated change shows both entries until it happens."""
        home = self.remember(raw_text="I live in Lisbon", claim="Lives in Lisbon.")
        move = self.remember(
            raw_text="Moving to Porto in December",
            claim="Moves to Porto in December 2026.",
            happened_at=datetime(2026, 12, 1, tzinfo=UTC),
            happened_precision="month",
            supersedes=str(home.pk),
            supersede_reason="change",
        )
        self.assertEqual(move.supersedes, home)

    def test_plans_may_be_dated_in_the_future(self):
        thought = self.remember(
            kind="thought",
            raw_text="Might swim on Saturday",
            claim="Might swim on 26 Sep 2026.",
            happened_at=NOW + timedelta(days=1),
            happened_precision="day",
        )
        self.assertEqual(thought.kind, "thought")

    def test_dates_without_a_zone_are_read_in_the_users_zone(self):
        result = self.call(
            "remember",
            raw_text="Swam",
            claim="Swam on 24 Sep 2026.",
            kind="memory",
            happened_at="2026-09-24T18:00:00",
        )
        self.assertFalse(result.is_error, result.content)
        entry = Entry.objects.get()
        self.assertEqual(entry.happened_at, datetime(2026, 9, 24, 17, 0, tzinfo=UTC))


class TagTests(ContractTestCase):
    def test_near_duplicate_tag_warns_without_rejecting(self):
        self.remember(raw_text="Swam", claim="Swam.", tags=["swimming", "person:rob"])
        entry = self.remember(
            raw_text="Swam again", claim="Swam again.", tags=["swiming", "person:robb"]
        )
        self.assertEqual(entry.tags, ["person:robb", "swiming"])
        self.assertEqual(len(entry.warnings), 2)
        self.assertIn("swimming", " ".join(entry.warnings))

    def test_plural_of_an_existing_tag_warns(self):
        self.remember(raw_text="Physio", claim="Physio.", tags=["exercise"])
        entry = self.remember(raw_text="Stretches", claim="Stretches.", tags=["exercises"])
        self.assertIn("exercise", " ".join(entry.warnings))

    def test_reusing_a_tag_or_a_distinct_one_does_not_warn(self):
        self.remember(raw_text="Swam", claim="Swam.", tags=["swimming"])
        entry = self.remember(raw_text="Ran", claim="Ran.", tags=["swimming", "running"])
        self.assertEqual(entry.warnings, [])

    def test_warnings_reach_the_client(self):
        self.remember(raw_text="Swam", claim="Swam.", tags=["swimming"])
        result = self.call(
            "remember", raw_text="Swam more", claim="Swam more.", kind="memory", tags=["swiming"]
        ).structured_content
        self.assertTrue(result["warnings"])


class InboxFallbackTests(ContractTestCase):
    def test_raw_text_alone_goes_to_the_inbox(self):
        result = self.call("remember", raw_text="Knee feels odd after the run").structured_content
        self.assertEqual(result["saved"], [])
        capture = Capture.objects.get()
        self.assertEqual((capture.source, capture.status), ("chat", "inbox"))
        self.assertEqual(capture.text, "Knee feels odd after the run")
        self.assertEqual(result["inbox"]["id"], str(capture.pk))
        self.assertFalse(Entry.objects.exists())

    def test_raw_only_capture_is_stored_once(self):
        """The duplicate guard covers raw-only captures: same words, same day."""
        first = s.capture_text(self.me, raw_text="Knee feels odd", client_name="c")
        again = s.capture_text(self.me, raw_text="knee  feels odd", client_name="c")
        self.assertEqual((first.pk, again.was_duplicate), (again.pk, True))
        self.assertEqual(Capture.objects.count(), 1)

    def test_claim_without_kind_is_refused(self):
        result = self.call("remember", raw_text="Swam", claim="Swam.")
        self.assertTrue(result.is_error)

    def test_every_remember_error_offers_the_inbox(self):
        cases = [
            {"raw_text": "x", "claim": "Did x yesterday.", "kind": "memory"},
            {"raw_text": "x", "claim": "x", "kind": "reminder"},
            {"raw_text": "x", "claim": "x", "kind": "memory", "happened_at": "last tuesday"},
            {"raw_text": "x", "claim": "x", "kind": "memory", "happened_at": "2026-10-01"},
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self.call("remember", **args)
                self.assertTrue(result.is_error)
                self.assertIn("raw_text alone", result.content[0].text)

    def test_inbox_mode_client_always_goes_to_the_inbox(self):
        weak, _ = s.create_client(self.me, "weak", mode=ClientMode.INBOX)
        result = self.call(
            "remember",
            client=weak,
            raw_text="Swam 2km",
            claim="Swam 2 km.",
            kind="memory",
            tags=["swimming"],
        ).structured_content
        self.assertEqual(result["saved"], [])
        self.assertFalse(Entry.objects.exists())
        capture = Capture.objects.get()
        self.assertEqual(capture.hints["fields"]["claim"], "Swam 2 km.")
        self.assertEqual(capture.hints["client"], "weak")

    def test_chat_capture_is_distilled_with_exact_excerpts(self):
        capture = s.capture_text(
            self.me, raw_text="Swam 2km and my knee hurt after", client_name="weak"
        )
        entry = self.remember(
            raw_text="my knee hurt after", claim="Knee hurt after swimming.", capture=capture
        )
        self.assertEqual(entry.capture, capture)
        with self.assertRaises(ValidationError):
            self.remember(raw_text="knee was sore", claim="Knee sore.", capture=capture)

    def test_dont_log_that_undoes_an_inbox_save_in_one_step(self):
        """Principle 9 holds for raw-only saves too."""
        receipt = self.call("remember", raw_text="Knee feels odd").structured_content
        self.assertIn("confirm: true", receipt["say"])
        capture_id = receipt["inbox"]["id"]
        result = self.call("forget", client=self.forgetter(), capture_id=capture_id, confirm=True)
        self.assertFalse(result.is_error, result.content)
        self.assertFalse(Capture.objects.exists())

    def test_a_distilled_inbox_note_still_needs_the_preview(self):
        capture = s.capture_text(self.me, raw_text="Swam 2km", client_name="c")
        self.remember(raw_text="Swam 2km", claim="Swam 2 km.", capture=capture)
        result = self.call(
            "forget", client=self.forgetter(), capture_id=str(capture.pk), confirm=True
        )
        self.assertTrue(result.is_error)

    def forgetter(self):
        client, _ = s.create_client(self.me, "forgetter", scopes=[Scope.READ, Scope.FORGET])
        return client

    def test_chat_captures_appear_in_the_inbox(self):
        self.call("remember", raw_text="Knee feels odd")
        (capture,) = self.call("inbox").structured_content["captures"]
        self.assertEqual((capture["source"], capture["transcript"]), ("chat", "Knee feels odd"))

    def test_weak_client_never_loses_a_capture(self):
        """Repeated invalid calls end in an inbox capture, never a lost one."""
        words = "Great session with Travis yesterday, 90-90s are helping"
        attempts = [
            {"claim": "Great physio session yesterday.", "kind": "memory"},
            {"claim": "Great physio session.", "kind": "memory", "happened_at": "the other day"},
            {"claim": "Great physio session.", "kind": "memory", "happened_at": "2026-12-25"},
        ]
        for attempt in attempts:
            result = self.call("remember", raw_text=words, **attempt)
            self.assertTrue(result.is_error)
            self.assertIn("raw_text alone", result.content[0].text)
        # Following the way out every error offered:
        result = self.call("remember", raw_text=words)
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(Capture.objects.get().text, words)
