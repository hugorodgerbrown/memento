"""Change vs correction, validity over time, and tombstones."""

import copy
from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase

from memories import pocket
from memories import services as s
from memories.models import Capture, Entry, PocketLink, Tombstone
from memories.tests.test_pocket import SOLO


def dt(*a):
    return datetime(*a, tzinfo=UTC)


def ids(qs):
    return {e.claim for e in qs}


class ChangeVersusCorrection(TestCase):
    def setUp(self):
        self.me = get_user_model().objects.create(username="me")

    def mem(self, claim, **kw):
        kw.setdefault("kind", "memory")
        return s.remember(self.me, client_name="claude.ai", raw_text=claim, claim=claim, **kw)

    def moved(self):
        lisbon = self.mem(
            "I live in Lisbon", happened_at=dt(2023, 1, 1), happened_precision="month"
        )
        porto = self.mem(
            "I moved to Porto",
            happened_at=dt(2026, 2, 1),
            happened_precision="month",
            supersedes=str(lisbon.pk),
            supersede_reason="change",
        )
        return lisbon, porto

    def test_the_lisbon_bug_is_fixed(self):
        lisbon, porto = self.moved()
        lisbon.refresh_from_db()
        self.assertEqual(lisbon.valid_until, porto.happened_at)
        self.assertEqual(ids(s.recall(self.me)), {"I moved to Porto"})
        self.assertEqual(ids(s.recall(self.me, query="Lisbon")), set())
        self.assertEqual(
            ids(s.recall(self.me, query="Lisbon", view="history")), {"I live in Lisbon"}
        )
        self.assertEqual(ids(s.recall(self.me, as_of=dt(2025, 6, 1))), {"I live in Lisbon"})
        self.assertEqual(
            ids(s.recall(self.me, view="history")), {"I live in Lisbon", "I moved to Porto"}
        )

    def test_correcting_a_change_moves_the_end_date(self):
        lisbon, porto = self.moved()
        march = self.mem(
            "Actually I moved to Porto in March",
            happened_at=dt(2026, 3, 1),
            happened_precision="month",
            supersedes=str(porto.pk),
            supersede_reason="correction",
        )
        lisbon.refresh_from_db()
        self.assertEqual(lisbon.valid_until, dt(2026, 3, 1))
        self.assertEqual(ids(s.recall(self.me, as_of=dt(2026, 2, 15))), {"I live in Lisbon"})
        # The February version was never true: hidden from history, kept in "all".
        self.assertEqual(ids(s.recall(self.me, view="history")), {"I live in Lisbon", march.claim})
        self.assertEqual(len(s.recall(self.me, view="all")), 3)

    def test_known_end_expires_from_current_view(self):
        self.mem(
            "On parental leave",
            happened_at=dt(2019, 12, 1),
            happened_precision="month",
            valid_until=dt(2020, 3, 1),
        )
        self.assertEqual(ids(s.recall(self.me)), set())
        self.assertEqual(ids(s.recall(self.me, as_of=dt(2020, 2, 1))), {"On parental leave"})

    def test_future_change_shows_both_until_it_happens(self):
        lisbon = self.mem(
            "I live in Lisbon", happened_at=dt(2023, 1, 1), happened_precision="month"
        )
        self.mem(
            "Moving to Porto",
            happened_at=dt(2099, 12, 1),
            happened_precision="month",
            supersedes=str(lisbon.pk),
            supersede_reason="change",
        )
        self.assertEqual(ids(s.recall(self.me)), {"I live in Lisbon", "Moving to Porto"})

    def test_rules(self):
        a = self.mem("A", happened_at=dt(2026, 5, 1), happened_precision="day")
        with self.assertRaises(ValidationError):  # a reason is required
            self.mem("B", supersedes=str(a.pk))
        with self.assertRaises(ValidationError):  # reason without supersedes
            self.mem(
                "B", supersede_reason="change", happened_at=dt(2026, 6, 1), happened_precision="day"
            )
        with self.assertRaises(ValidationError):  # a change must say when
            self.mem("B", supersedes=str(a.pk), supersede_reason="change")
        with self.assertRaises(ValidationError):  # and can't predate what it changes
            self.mem(
                "B",
                supersedes=str(a.pk),
                supersede_reason="change",
                happened_at=dt(2026, 1, 1),
                happened_precision="day",
            )

    def test_timeline_counts_what_was_true_not_what_was_wrong(self):
        tagged = {"tags": ["home"], "happened_precision": "month"}
        lisbon = self.mem("Lisbon", happened_at=dt(2026, 1, 1), **tagged)
        porto = self.mem(
            "Porto",
            happened_at=dt(2026, 2, 1),
            supersedes=str(lisbon.pk),
            supersede_reason="change",
            **tagged,
        )
        self.mem(
            "Porto, March",
            happened_at=dt(2026, 3, 1),
            supersedes=str(porto.pk),
            supersede_reason="correction",
            **tagged,
        )
        rows = s.timeline(self.me, since=dt(2026, 1, 1), until=dt(2026, 12, 31), bucket="month")
        self.assertEqual([r.period.month for r in rows], [1, 3])

    def test_chains_can_only_be_cut_through_forget(self):
        a = self.mem("A")
        self.mem("B", supersedes=str(a.pk), supersede_reason="correction")
        with self.assertRaises(ProtectedError):
            a.delete()


class Tombstones(TestCase):
    def setUp(self):
        self.me = get_user_model().objects.create(username="sam")
        PocketLink.objects.create(owner=self.me, pocket_user_id="user_me", speaker_label="Sam")

    def test_tombstone_holds_ids_and_date_only(self):
        e = s.remember(
            self.me, client_name="c", kind="thought", raw_text="Private", claim="Private"
        )
        s.forget(self.me, str(e.pk))
        fields = {f.name for f in Tombstone._meta.get_fields()}
        self.assertEqual(
            fields, {"id", "owner", "object_id", "object_type", "external_ref", "forgotten_at"}
        )
        self.assertIn(e.pk, s.forgotten(self.me, [str(e.pk)]))
        self.assertNotIn("Private", str(Tombstone.objects.values().get()))

    def test_forgotten_note_is_not_resurrected_by_redelivery(self):
        pocket.ingest(copy.deepcopy(SOLO))
        capture = Capture.objects.get()
        s.forget(self.me, capture_id=str(capture.pk))
        self.assertEqual(pocket.ingest(copy.deepcopy(SOLO)), "ignored")
        self.assertFalse(Capture.objects.exists())
        self.assertFalse(Entry.objects.exists())

    def test_forgotten_reminder_is_not_recreated(self):
        pocket.ingest(copy.deepcopy(SOLO))
        reminder = Entry.objects.get(kind="reminder")
        s.forget(self.me, str(reminder.pk))
        again = copy.deepcopy(SOLO)
        again["event"] = "action_items.updated"
        pocket.ingest(again)
        self.assertFalse(Entry.objects.filter(kind="reminder").exists())
        self.assertEqual(Capture.objects.count(), 1)  # the note itself stays
