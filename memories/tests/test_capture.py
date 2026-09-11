"""Proactive capture: the same observation must never be stored twice."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from memories import services as s
from memories.models import Entry


def dt(*a):
    return datetime(*a, tzinfo=UTC)


class DuplicateGuard(TestCase):
    def setUp(self):
        self.me = get_user_model().objects.create(username="me")

    def log(self, raw, when, **kw):
        return s.remember(
            self.me,
            client_name=kw.pop("client", "claude.ai"),
            kind="memory",
            raw_text=raw,
            claim=raw,
            happened_at=when,
            happened_precision="day",
            **kw,
        )

    def test_same_observation_twice_is_stored_once(self):
        first = self.log("Good sleep", dt(2026, 9, 11, 7))
        again = self.log("good  sleep", dt(2026, 9, 11, 9), client="chatgpt")
        self.assertEqual(first.pk, again.pk)
        self.assertTrue(again.was_duplicate)
        self.assertEqual(Entry.objects.count(), 1)

    def test_same_words_on_different_days_are_different_entries(self):
        self.log("Good sleep", dt(2026, 9, 11, 7))
        second = self.log("Good sleep", dt(2026, 9, 12, 7))
        self.assertFalse(second.was_duplicate)
        self.assertEqual(Entry.objects.count(), 2)

    def test_undated_repeats_only_collapse_within_an_hour(self):
        a = s.remember(
            self.me, client_name="c", kind="thought", raw_text="Pricing idea", claim="Pricing idea"
        )
        b = s.remember(
            self.me, client_name="c", kind="thought", raw_text="Pricing idea", claim="Pricing idea"
        )
        self.assertEqual(a.pk, b.pk)
        later = timezone.now() + timedelta(hours=2)
        with patch("memories.services.timezone.now", return_value=later):
            c = s.remember(
                self.me,
                client_name="c",
                kind="thought",
                raw_text="Pricing idea",
                claim="Pricing idea",
            )
        self.assertNotEqual(a.pk, c.pk)


class OneStepUndo(TestCase):
    def setUp(self):
        self.me = get_user_model().objects.create(username="me")

    def save(self, raw="Good sleep", **kw):
        return s.remember(self.me, client_name="c", kind="memory", raw_text=raw, claim=raw, **kw)

    def test_fresh_single_entry_is_a_simple_undo(self):
        e = self.save()
        self.assertTrue(s.is_simple_undo(s.forget_plan(self.me, str(e.pk))))

    def test_old_entry_needs_a_preview(self):
        e = self.save()
        later = timezone.now() + timedelta(minutes=16)
        with patch("memories.services.timezone.now", return_value=later):
            self.assertFalse(s.is_simple_undo(s.forget_plan(self.me, str(e.pk))))

    def test_cited_entry_needs_a_preview(self):
        e = self.save()
        s.remember(
            self.me,
            client_name="c",
            kind="digest",
            raw_text="D",
            claim="D",
            sources=[str(e.pk)],
            covers_from=dt(2026, 9, 1),
            covers_to=dt(2026, 9, 30),
        )
        self.assertFalse(s.is_simple_undo(s.forget_plan(self.me, str(e.pk))))

    def test_part_of_a_chain_needs_a_preview(self):
        a = self.save("A")
        b = self.save("B", supersedes=str(a.pk), supersede_reason="correction")
        self.assertFalse(s.is_simple_undo(s.forget_plan(self.me, str(b.pk))))
