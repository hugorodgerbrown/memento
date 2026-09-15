"""Voice capture via Pocket. Each test maps to a decision in the Phase 3 addendum."""

import copy
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

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
        PocketLink.objects.create(
            owner=self.me, pocket_user_id="user_me", speaker_label="Sam", timezone="Europe/London"
        )

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
        self.assertEqual(Entry.objects.filter(kind="reminder").count(), 1)

    # Reminders at once, without breaking "raw is sacred"
    def test_dated_action_item_becomes_reminder_with_your_words_as_raw(self):
        pocket.ingest(payload())
        r = Entry.objects.get(kind="reminder")
        self.assertEqual(r.claim, "Renew memento.app domain")
        self.assertIn("remind me to renew the memento dot app domain", r.raw_text)
        self.assertEqual((r.client_name, r.model_name), ("pocket", "gpt5"))
        self.assertEqual(r.due_at, datetime(2026, 9, 19, 23, 0, tzinfo=UTC))  # BST midnight
        self.assertEqual(r.capture.external_id, "rec_1")

    def test_undated_action_item_stays_a_hint(self):
        pocket.ingest(payload())
        self.assertFalse(Entry.objects.filter(claim="Think about pricing").exists())

    def test_ticking_off_in_pocket_completes_the_reminder(self):
        pocket.ingest(payload())
        ticked = payload(event="action_items.updated")
        ticked["summarizations"]["sum_1"]["v2"]["actionItems"]["actionItems"][0]["isCompleted"] = (
            True
        )
        self.assertEqual(pocket.ingest(ticked), "updated")
        self.assertIsNotNone(Entry.objects.get(kind="reminder").completed_at)

    def test_ticking_off_completes_the_version_that_stands(self):
        """A client sharpens Pocket's claim; external_ref stays on the row it replaced."""
        pocket.ingest(payload())
        theirs = Entry.objects.get(kind="reminder")
        mine = s.remember(
            self.me,
            client_name="claude",
            kind="reminder",
            raw_text="remind me to renew the memento dot app domain",
            claim="Renew the memento.app domain before it lapses.",
            due_at=theirs.due_at,
            capture=str(theirs.capture_id),
            supersedes=str(theirs.pk),
            supersede_reason="correction",
        )
        self.assertEqual(pocket.ingest(self._ticked()), "updated")
        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertIsNotNone(mine.completed_at)  # the one that would have kept firing
        self.assertIsNone(theirs.completed_at)  # the replaced row is left as it was

    def test_ticking_off_a_reminder_replaced_by_a_memory_completes_nothing(self):
        """ "I've done it" may arrive as a memory. Nothing to complete, and no crash."""
        pocket.ingest(payload())
        theirs = Entry.objects.get(kind="reminder")
        s.remember(
            self.me,
            client_name="claude",
            kind="memory",
            raw_text="remind me to renew the memento dot app domain",
            claim="Renewed the memento.app domain.",
            capture=str(theirs.capture_id),
            supersedes=str(theirs.pk),
            supersede_reason="change",
            happened_at=datetime(2026, 9, 12, 9, 0, tzinfo=UTC),
            happened_precision="day",
        )
        self.assertEqual(pocket.ingest(self._ticked()), "updated")
        theirs.refresh_from_db()
        self.assertIsNone(theirs.completed_at)

    def _ticked(self):
        p = payload(event="action_items.updated")
        p["summarizations"]["sum_1"]["v2"]["actionItems"]["actionItems"][0]["isCompleted"] = True
        return p

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
        (c,) = s.inbox(self.me)
        self.assertEqual([e.kind for e in c.entries.all()], ["reminder"])
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
        self.assertEqual(len(plan.entries), 3)  # thought, Pocket reminder, digest
        self.assertFalse(Capture.objects.exists())
        self.assertEqual(list(Entry.objects.values_list("pk", flat=True)), [other.pk])
