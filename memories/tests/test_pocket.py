"""Voice capture via Pocket. Each test maps to a decision in the Phase 3 addendum."""

import copy
import hashlib
import hmac
import io
import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from memories import pocket
from memories import services as s
from memories.models import Capture, Entry, IngestLog, PocketLink

SECRET = "whsec_test"

SOLO = {
    "event": "summary.completed",
    "timestamp": "2026-09-11T08:00:05.000Z",
    "user": {"id": "user_me", "email": "me@example.com"},
    "recording": {
        "id": "rec_1",
        "title": "Walk thoughts",
        "duration": 95,
        "language": "English",
        "createdAt": "2026-09-11T07:40:00.000Z",
    },
    "summarizations": {
        "sum_1": {
            "processingStatus": "completed",
            "updatedAt": "2026-09-11T08:00:05.000Z",
            "settings": {"modelId": "gpt5"},
            "v2": {
                "summary": {"markdown": "## Ideas", "bulletPoints": ["Hire slower"]},
                "actionItems": {
                    "actionItems": [
                        {
                            "id": "t1",
                            "globalActionItemId": "gai_1",
                            "title": "Renew memento.app domain",
                            "dueDate": "2026-09-20",
                            "isCompleted": False,
                        },
                        {
                            "id": "t2",
                            "globalActionItemId": "gai_2",
                            "title": "Think about pricing",
                            "dueDate": None,
                            "isCompleted": False,
                        },
                    ]
                },
            },
        }
    },
    "transcript": [
        {
            "speaker": "Sam",
            "text": "I think we should hire slower next quarter.",
            "start": 0,
            "end": 4,
        },
        {
            "speaker": "Sam",
            "text": "Oh and remind me to renew the memento dot app domain.",
            "start": 4,
            "end": 9,
        },
    ],
}


def payload(**changes):
    p = copy.deepcopy(SOLO)
    for key, value in changes.items():
        p[key] = value
    return p


class PocketIngestTests(TestCase):
    def setUp(self):
        self.me = get_user_model().objects.create(username="sam")
        PocketLink.objects.create(owner=self.me, pocket_user_id="user_me", speaker_label="Sam")

    # Signature
    def test_signature_valid_tampered_and_stale(self):
        body = json.dumps(SOLO).encode()
        ts = str(int(time.time() * 1000))
        sig = hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        self.assertTrue(pocket.verify_signature(SECRET, ts, body, sig))
        self.assertTrue(pocket.verify_signature(SECRET, ts, body, "sha256=" + sig))
        self.assertFalse(pocket.verify_signature(SECRET, ts, body + b" ", sig))
        stale = str(int((time.time() - 3600) * 1000))
        stale_sig = hmac.new(
            SECRET.encode(), f"{stale}.".encode() + body, hashlib.sha256
        ).hexdigest()
        self.assertFalse(pocket.verify_signature(SECRET, stale, body, stale_sig))

    @override_settings(POCKET_WEBHOOK_SECRET=SECRET)
    def test_webhook_end_to_end(self):
        body = json.dumps(SOLO).encode()
        ts = str(int(time.time() * 1000))
        sig = hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        bad = self.client.post(
            "/ingest/pocket/",
            body,
            content_type="application/json",
            headers={"X-HeyPocket-Timestamp": ts, "X-HeyPocket-Signature": "0" * 64},
        )
        self.assertEqual(bad.status_code, 401)
        ok = self.client.post(
            "/ingest/pocket/",
            body,
            content_type="application/json",
            headers={"X-HeyPocket-Timestamp": ts, "X-HeyPocket-Signature": sig},
        )
        self.assertEqual((ok.status_code, ok.content), (200, b"stored"))

    # Solo recordings only, and skips keep no content
    def test_solo_note_is_stored_with_hints_apart(self):
        self.assertEqual(pocket.ingest(payload()), "stored")
        c = Capture.objects.get()
        self.assertTrue(c.text.startswith("I think we should hire slower"))
        self.assertEqual(c.hints["model"], "gpt5")
        self.assertEqual(c.status, "inbox")

    def test_conversation_is_skipped_and_leaves_no_content(self):
        convo = payload(
            transcript=SOLO["transcript"]
            + [{"speaker": "Ana", "text": "My salary is confidential.", "start": 9, "end": 11}]
        )
        self.assertEqual(pocket.ingest(convo), "skipped")
        self.assertFalse(Capture.objects.exists())
        self.assertFalse(Entry.objects.exists())
        log = IngestLog.objects.get()
        self.assertEqual(log.reason, "2 speakers")
        self.assertNotIn("salary", str(log.__dict__))

    def test_unlabelled_speaker_waits_for_voice_print(self):
        unlabelled = [dict(seg, speaker="Speaker 1") for seg in SOLO["transcript"]]
        self.assertEqual(
            pocket.ingest(payload(event="transcription.completed", transcript=unlabelled)),
            "skipped",
        )
        self.assertEqual(pocket.ingest(payload(event="speakers.labeled")), "stored")

    def test_retries_are_idempotent(self):
        pocket.ingest(payload())
        self.assertEqual(pocket.ingest(payload()), "duplicate")
        self.assertEqual(Capture.objects.count(), 1)

    # Action items are Pocket's to-do list, not memories (0015)
    def test_action_items_are_ignored(self):
        pocket.ingest(payload())
        self.assertFalse(Entry.objects.exists())
        self.assertNotIn("action_items", Capture.objects.get().hints)

    def test_action_item_events_store_nothing(self):
        pocket.ingest(payload())
        self.assertEqual(pocket.ingest(payload(event="action_items.updated")), "ignored")
        self.assertFalse(Entry.objects.exists())

    # Pocket deletion doesn't reach Memento
    def test_deleting_in_pocket_keeps_the_memory(self):
        pocket.ingest(payload())
        self.assertEqual(pocket.ingest(payload(event="recording.deleted")), "ignored")
        self.assertEqual(Capture.objects.count(), 1)

    # Transcript edits append, never overwrite
    def test_transcript_edit_appends_revision(self):
        pocket.ingest(payload())
        edited = payload(
            event="transcript.edited",
            transcript=[
                {
                    "speaker": "Sam",
                    "text": "I think we should hire slower next quarter.",
                    "start": 0,
                    "end": 4,
                },
                {"speaker": "Sam", "text": "Remind me to renew memento.app.", "start": 4, "end": 9},
            ],
        )
        self.assertEqual(pocket.ingest(edited), "updated")
        c = Capture.objects.get()
        self.assertIn("memento dot app", c.text)
        self.assertEqual(len(c.revisions), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Capture.objects.filter(pk=c.pk).update(text="rewritten")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Capture.objects.filter(pk=c.pk).update(revisions=[])

    def test_unknown_pocket_user_rejected(self):
        self.assertEqual(pocket.ingest(payload(user={"id": "stranger"})), "rejected")


class DistillingTests(TestCase):
    """What your LLM does with the inbox."""

    def setUp(self):
        self.me = get_user_model().objects.create(username="sam")
        PocketLink.objects.create(owner=self.me, pocket_user_id="user_me", speaker_label="Sam")
        pocket.ingest(payload())
        self.capture = Capture.objects.get()

    def distil(self, raw, **kw):
        return s.remember(
            self.me,
            client_name="claude.ai",
            kind="thought",
            raw_text=raw,
            claim="Hire more slowly next quarter",
            capture=str(self.capture.pk),
            **kw,
        )

    def test_exact_excerpt_accepted_and_dated_from_the_recording(self):
        e = self.distil("we should hire   slower next quarter")
        self.assertEqual(e.happened_at, self.capture.captured_at)
        self.assertEqual(e.happened_precision, "exact")

    def test_paraphrase_rejected(self):
        with self.assertRaises(ValidationError):
            self.distil("We need to slow down hiring next quarter")

    def test_excerpt_from_later_revision_accepted(self):
        pocket.ingest(
            payload(
                event="transcript.edited",
                transcript=[
                    {
                        "speaker": "Sam",
                        "text": "Remind me to renew memento.app.",
                        "start": 0,
                        "end": 3,
                    }
                ],
            )
        )
        self.distil("renew memento.app")

    def test_inbox_shows_what_already_came_of_a_note(self):
        self.distil("we should hire slower next quarter")
        (c,) = s.inbox(self.me)
        self.assertEqual([e.kind for e in c.entries.all()], ["thought"])
        s.close_capture(self.me, str(c.pk), "processed")
        self.assertEqual(s.inbox_count(self.me), 0)

    def test_forget_entry_warns_transcript_remains(self):
        e = self.distil("we should hire slower next quarter")
        plan = s.forget_plan(self.me, str(e.pk))
        self.assertEqual([x.pk for x in plan.entries], [e.pk])
        self.assertIn("transcript still contains", plan.note)

    def test_forget_source_removes_note_all_entries_and_digests(self):
        e = self.distil("we should hire slower next quarter")
        other = s.remember(
            self.me, client_name="c", kind="memory", raw_text="Unrelated", claim="Unrelated"
        )
        s.remember(
            self.me,
            client_name="c",
            kind="digest",
            raw_text="Sept",
            claim="Sept",
            sources=[str(e.pk)],
            covers_from=datetime(2026, 9, 1, tzinfo=UTC),
            covers_to=datetime(2026, 9, 30, tzinfo=UTC),
        )
        plan = s.forget(self.me, str(e.pk), scope="source")
        self.assertEqual(len(plan.entries), 2)  # thought, digest
        self.assertFalse(Capture.objects.exists())
        self.assertEqual(list(Entry.objects.values_list("pk", flat=True)), [other.pk])


# --- the pull (0021): Pocket's REST shape, into the same ingest rules --------------

PULLED = {
    "id": "rec_9",
    "title": "Foot update",
    "duration": 40,
    "language": "en",
    "created_at": "2026-09-15T08:24:10Z",
    "recording_at": "2026-09-15T08:23:00Z",
    "updated_at": "2026-09-15T08:30:00Z",
    "transcript": [
        {"speaker": "Sam", "text": "PF is fine on left foot,", "start": 0, "end": 3},
        {"speaker": "Sam", "text": "and on right foot is very mild.", "start": 3, "end": 6},
    ],
    "summarizations": SOLO["summarizations"],
}


def pulled(**changes):
    rec = copy.deepcopy(PULLED)
    rec.update(changes)
    return rec


class FakePocket:
    """Pocket's REST API as the pull sees it: a page of recordings, then each one."""

    def __init__(self, recordings, page_size=2, status=200):
        self.recordings, self.page_size, self.status = recordings, page_size, status
        self.calls: list[tuple[str, dict]] = []

    def get(self, path, params=None):
        self.calls.append((path, params or {}))
        if self.status != 200:
            raise pocket.PocketAPIError(self.status)
        if path == "/recordings":
            page = int(params.get("page", 1))
            chunk = self.recordings[(page - 1) * self.page_size : page * self.page_size]
            more = page * self.page_size < len(self.recordings)
            return {"data": [{"id": r["id"]} for r in chunk], "pagination": {"has_more": more}}
        rec_id = path.rsplit("/", 1)[1]
        return {"data": next(r for r in self.recordings if r["id"] == rec_id)}


class PullTestCase(TestCase):
    def setUp(self):
        self.me = get_user_model().objects.create(username="sam")
        self.link = PocketLink.objects.create(
            owner=self.me, pocket_user_id="user_me", speaker_label="Sam"
        )

    def pull(self, *recordings, **kw):
        api = FakePocket(list(recordings))
        kw.setdefault("since", datetime(2026, 9, 14, tzinfo=UTC))
        results = pocket.pull(self.link, api, **kw)
        return api, results


class PocketPullTests(PullTestCase):
    def test_a_pulled_solo_note_is_stored_as_a_webhook_one_would_be(self):
        _, results = self.pull(pulled())
        self.assertEqual(results, [("rec_9", "stored")])
        c = Capture.objects.get()
        self.assertEqual(c.text, "PF is fine on left foot, and on right foot is very mild.")
        self.assertEqual(c.captured_at, datetime(2026, 9, 15, 8, 23, tzinfo=UTC))  # when spoken
        self.assertEqual(c.hints["summary_markdown"], "## Ideas")

    def test_pulling_again_is_silent(self):
        """0021: every 15 minutes over a 36-hour window; repeats add nothing, not even a log row."""
        self.pull(pulled())
        logs = IngestLog.objects.count()
        _, results = self.pull(pulled())
        self.assertEqual(results, [])
        self.assertEqual((Capture.objects.count(), IngestLog.objects.count()), (1, logs))

    def test_a_pulled_conversation_is_skipped_once_and_leaves_no_content(self):
        """Principle 8, through the pull."""
        talk = pulled(transcript=[
            {"speaker": "Sam", "text": "Shall we?", "start": 0, "end": 1},
            {"speaker": "Priya", "text": "My news is private.", "start": 1, "end": 2},
        ])  # fmt: skip
        self.pull(talk)
        self.pull(talk)
        (log,) = IngestLog.objects.all()
        self.assertEqual((log.decision, log.reason), ("skipped", "2 speakers"))
        self.assertFalse(Capture.objects.exists())
        self.assertNotIn("private", json.dumps(list(IngestLog.objects.values()), default=str))

    def test_a_pulled_edit_appends_a_revision(self):
        self.pull(pulled())
        edited = pulled(
            transcript=[{"speaker": "Sam", "text": "PF is gone.", "start": 0, "end": 1}]
        )
        _, results = self.pull(edited)
        self.assertEqual(results, [("rec_9", "updated")])
        c = Capture.objects.get()
        self.assertEqual((c.texts()[0], c.texts()[-1]), (PULLED_TEXT, "PF is gone."))

    def test_what_you_forgot_is_never_pulled_back(self):
        """Principle 5: the tombstone keeps Pocket from re-delivering it, by pull as by push."""
        self.pull(pulled())
        s.forget(self.me, capture_id=str(Capture.objects.get().pk), scope="source")
        _, results = self.pull(pulled())
        self.assertEqual(results, [])
        self.assertFalse(Capture.objects.exists())

    def test_pages_are_followed(self):
        recs = [pulled(id=f"rec_{i}") for i in range(5)]
        api, results = self.pull(*recs)
        self.assertEqual(len(results), 5)
        self.assertEqual([p["page"] for path, p in api.calls if path == "/recordings"], [1, 2, 3])
        self.assertEqual(api.calls[0][1]["start_date"], "2026-09-14")

    def test_a_dry_run_stores_nothing(self):
        _, results = self.pull(pulled(), dry_run=True)
        self.assertEqual(results, [("rec_9", "stored")])  # what would happen
        self.assertFalse(Capture.objects.exists() or IngestLog.objects.exists())

    def test_pocket_is_asked_for_a_day_not_a_time(self):
        """Pocket answers 400 to a full time in start_date. The UTC day never starts late."""
        api, _ = self.pull(since=datetime.fromisoformat("2026-09-14T00:30:00+01:00"))
        self.assertEqual(api.calls[0][1]["start_date"], "2026-09-13")

    def test_the_rest_of_the_day_before_since_is_not_stored(self):
        """Pocket answers for the whole day; the cutoff is still the time asked for."""
        early = pulled(id="rec_early", created_at="2026-09-15T07:59:00Z")
        on_time = pulled(id="rec_on_time", created_at="2026-09-15T08:00:00Z")
        api, results = self.pull(early, on_time, since=datetime(2026, 9, 15, 8, tzinfo=UTC))
        self.assertEqual(api.calls[0][1]["start_date"], "2026-09-15")
        self.assertEqual(results, [("rec_on_time", "stored")])
        self.assertEqual(Capture.objects.get().external_id, "rec_on_time")

    def test_pockets_own_error_is_kept(self):
        """A 400 names the parameter Pocket refused; the owner sees it, not just "Bad Request"."""
        body = io.BytesIO(b'{"error":"bad start_date"}')
        error = urllib.error.HTTPError("https://pocket/recordings", 400, "Bad Request", {}, body)
        said = 'Bad Request: {"error":"bad start_date"}'
        with (
            patch.object(urllib.request, "urlopen", side_effect=error),
            self.assertRaisesMessage(pocket.PocketAPIError, said),
        ):
            pocket.PocketAPI("pk_test", "https://pocket").get("/recordings")

    def test_a_rejected_key_says_what_to_do(self):
        api = FakePocket([], status=401)
        with self.assertRaisesMessage(pocket.PocketAPIError, "Settings > Developer > API Keys"):
            pocket.pull(self.link, api, since=datetime(2026, 9, 14, tzinfo=UTC))


PULLED_TEXT = "PF is fine on left foot, and on right foot is very mild."


class PocketPullCommandTests(TestCase):
    def setUp(self):
        self.me = get_user_model().objects.create(username="sam")
        PocketLink.objects.create(owner=self.me, pocket_user_id="user_me", speaker_label="Sam")

    def run_pull(self, *args):
        out = io.StringIO()
        call_command("pocket_pull", *args, stdout=out)
        return out.getvalue()

    @override_settings(POCKET_API_KEY="")
    def test_without_a_key_it_says_where_to_get_one(self):
        with self.assertRaisesMessage(CommandError, "Settings > Developer > API Keys"):
            self.run_pull()

    @override_settings(POCKET_API_KEY="pk_test")
    def test_it_looks_back_36_hours_and_reports(self):
        an_hour_ago = (timezone.now() - timedelta(hours=1)).isoformat()
        api = FakePocket([pulled(created_at=an_hour_ago)])
        before = timezone.now() - timedelta(hours=36)
        with patch.object(pocket, "PocketAPI", return_value=api):
            out = self.run_pull()
        after = timezone.now() - timedelta(hours=36)
        days = {before.astimezone(UTC).date().isoformat(), after.astimezone(UTC).date().isoformat()}
        self.assertIn(api.calls[0][1]["start_date"], days)
        self.assertIn("rec_9: stored", out)

    @override_settings(POCKET_API_KEY="pk_test")
    def test_dry_run_says_what_it_would_do(self):
        with patch.object(pocket, "PocketAPI", return_value=FakePocket([pulled()])):
            out = self.run_pull("--dry-run", "--since", "2026-09-14T00:00:00+01:00")
        self.assertIn("Would have 1 change(s)", out)
        self.assertFalse(Capture.objects.exists())

    @override_settings(POCKET_API_KEY="pk_test")
    def test_since_takes_a_plain_date_for_backfill(self):
        """The first recordings predate the pull: make pocket-pull SINCE=2026-09-14."""
        api = FakePocket([])
        with patch.object(pocket, "PocketAPI", return_value=api):
            self.run_pull("--since", "2026-09-14")
        self.assertEqual(api.calls[0][1]["start_date"], "2026-09-14")
        with self.assertRaisesMessage(CommandError, "isn't a time I can read"):
            self.run_pull("--since", "last week")


class PocketRevisionTests(PullTestCase):
    """Review of 0021: what a later version of a recording may and may not change."""

    TWO_VOICES = [
        {"speaker": "Sam", "text": "PF is fine on left foot,", "start": 0, "end": 3},
        {"speaker": "Priya", "text": "That's my private news.", "start": 3, "end": 6},
    ]

    def test_a_revision_with_another_voice_is_never_stored(self):
        """Principle 8: relabelled or re-transcribed with someone else in it, it stays as it was."""
        self.pull(pulled())
        _, results = self.pull(pulled(transcript=self.TWO_VOICES))
        self.assertEqual(results, [("rec_9", "skipped")])
        c = Capture.objects.get()
        self.assertEqual((c.revisions, c.text), ([], PULLED_TEXT))
        self.assertNotIn("private", json.dumps(list(IngestLog.objects.values()), default=str))
        _, again = self.pull(pulled(transcript=self.TWO_VOICES))
        self.assertEqual(again, [])  # logged once

    def test_a_webhook_edit_with_another_voice_is_never_stored(self):
        pocket.ingest(payload())
        edited = payload(event="transcript.edited", transcript=self.TWO_VOICES)
        self.assertEqual(pocket.ingest(edited), "skipped")
        self.assertEqual(Capture.objects.get().revisions, [])

    def test_a_summary_that_arrives_later_is_picked_up(self):
        """Hints are derived and may be refreshed; the transcript never changes."""
        self.pull(pulled(summarizations={}, title=""))
        _, results = self.pull(pulled(title="Foot update"))
        self.assertEqual(results, [])  # not a change worth logging
        c = Capture.objects.get()
        self.assertEqual((c.title, c.hints["summary_markdown"]), ("Foot update", "## Ideas"))

    def test_a_bare_list_of_recordings_is_read_too(self):
        class BareList(FakePocket):
            def get(self, path, params=None):
                body = super().get(path, params)
                return body["data"] if path == "/recordings" else body

        api = BareList([pulled()])
        results = pocket.pull(self.link, api, since=datetime(2026, 9, 14, tzinfo=UTC))
        self.assertEqual(results, [("rec_9", "stored")])
