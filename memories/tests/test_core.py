"""Each test maps to a principle in the brief."""

import re
from datetime import UTC, datetime
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from memories import services as s
from memories.models import Entry


def dt(*a):
    return datetime(*a, tzinfo=UTC)


class MementoTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.me = User.objects.create(username="me")
        self.other = User.objects.create(username="other")

    def mem(self, raw, claim, owner=None, **kw):
        kw.setdefault("kind", "memory")
        return s.remember(
            owner or self.me, client_name="claude.ai", raw_text=raw, claim=claim, **kw
        )

    # Principle 1: raw is sacred
    def test_raw_text_cannot_be_rewritten_via_save(self):
        e = self.mem("I think we should hire slower", "Hire slower")
        e.raw_text = "I think we should hire faster"
        with self.assertRaises(ValidationError):
            e.save()

    def test_raw_text_cannot_be_rewritten_via_queryset(self):
        e = self.mem("Original words", "Claim")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Entry.objects.filter(pk=e.pk).update(raw_text="Rewritten")

    def test_derived_fields_can_be_rebuilt(self):
        e = self.mem("Original words", "Old claim")
        Entry.objects.filter(pk=e.pk).update(claim="Better claim", tags=["rebuilt"])
        e.refresh_from_db()
        self.assertEqual(e.claim, "Better claim")

    # Principle 3: every answer cites
    def test_digest_must_cite(self):
        with self.assertRaises(ValidationError):
            s.remember(
                self.me,
                client_name="c",
                kind="digest",
                raw_text="Summary",
                claim="Summary",
                covers_from=dt(2026, 1, 1),
                covers_to=dt(2026, 2, 1),
            )

    def test_cannot_cite_someone_elses_entry(self):
        theirs = self.mem("Private", "Private", owner=self.other)
        with self.assertRaises(ValidationError):
            s.remember(
                self.me,
                client_name="c",
                kind="digest",
                raw_text="S",
                claim="S",
                sources=[str(theirs.pk)],
                covers_from=dt(2026, 1, 1),
                covers_to=dt(2026, 2, 1),
            )

    # Principle 4: corrections append
    def test_correction_hides_old_version_but_keeps_it(self):
        old = self.mem("Meeting with Ana is Tuesday", "Ana meeting Tuesday")
        new = self.mem(
            "Correction: it's Wednesday",
            "Ana meeting Wednesday",
            supersedes=str(old.pk),
            supersede_reason="correction",
        )
        self.assertEqual([e.pk for e in s.recall(self.me, query="Ana meeting")], [new.pk])
        self.assertEqual(len(s.recall(self.me, query="Ana meeting", view="all")), 2)

    def test_correction_chain_cannot_fork(self):
        old = self.mem("A", "A")
        self.mem("B", "B", supersedes=str(old.pk), supersede_reason="correction")
        with self.assertRaises(ValidationError):
            self.mem("C", "C", supersedes=str(old.pk), supersede_reason="correction")

    # Principle 5: forgetting is a right, and it cascades
    def test_forget_removes_chain_and_citing_digests_transitively(self):
        a = self.mem("Something I regret writing", "Regret")
        a2 = self.mem(
            "Correction of it",
            "Regret, corrected",
            supersedes=str(a.pk),
            supersede_reason="correction",
        )
        keep = self.mem("Unrelated", "Unrelated")
        window = {"covers_from": dt(2026, 1, 1), "covers_to": dt(2026, 12, 31)}
        d1 = s.remember(
            self.me,
            client_name="c",
            kind="digest",
            raw_text="Month",
            claim="Month",
            sources=[str(a2.pk), str(keep.pk)],
            **window,
        )
        d2 = s.remember(
            self.me,
            client_name="c",
            kind="digest",
            raw_text="Year",
            claim="Year",
            sources=[str(d1.pk)],
            **window,
        )
        plan = {e.pk for e in s.forget_plan(self.me, str(a.pk)).entries}
        self.assertEqual(plan, {a.pk, a2.pk, d1.pk, d2.pk})
        s.forget(self.me, str(a.pk))
        self.assertEqual(list(Entry.objects.values_list("pk", flat=True)), [keep.pk])

    # Principle 6: time has two axes
    def test_precision_required_with_happened_at(self):
        with self.assertRaises(ValidationError):
            self.mem("Last March", "March thing", happened_at=dt(2026, 3, 1))

    def test_reminder_needs_due_at(self):
        with self.assertRaises(ValidationError):
            self.mem("Renew the domain", "Renew domain", kind="reminder")

    # Search and shape
    def test_full_text_search_stems_and_ranks(self):
        self.mem(
            "We decided to stop hiring contractors", "Stop hiring contractors", kind="decision"
        )
        self.mem("Lunch was good", "Lunch")
        self.assertEqual(len(s.recall(self.me, query="hire contractor")), 1)

    def test_owner_isolation(self):
        self.mem("Secret plan", "Secret plan", owner=self.other)
        self.assertEqual(len(s.recall(self.me, query="secret")), 0)

    def test_timeline_counts_by_tag_and_month(self):
        for m, d in ((3, 10), (3, 17), (4, 10)):
            self.mem(
                f"Hiring note {m}/{d}",
                "Hiring",
                tags=["Hiring", "project:memento"],
                happened_at=dt(2026, m, d),
                happened_precision="day",
            )
        rows = s.timeline(
            self.me,
            since=dt(2026, 1, 1),
            until=dt(2026, 12, 31),
            bucket="month",
            tag_prefix="hiring",
        )
        self.assertEqual(
            [(r.period.month, r.tag, r.count) for r in rows], [(3, "hiring", 2), (4, "hiring", 1)]
        )

    def test_tag_rules(self):
        self.assertEqual(
            s.normalise_tags(["Hiring ", "person:Alice", "hiring"]), ["hiring", "person:alice"]
        )
        with self.assertRaises(ValidationError):
            s.normalise_tags(["mood:happy"])

    def test_due_reminders(self):
        r = self.mem("Renew memento.app", "Renew domain", kind="reminder", due_at=dt(2026, 3, 1))
        self.assertEqual(list(s.due_reminders(dt(2026, 3, 2))), [r])
        s.complete_reminder(self.me, str(r.pk))
        self.assertEqual(list(s.due_reminders(dt(2026, 3, 2))), [])


class ServerNeverGeneratesTests(SimpleTestCase):
    """
    Principle 2. The distiller (distiller/) calls a model, so the repository holds an
    LLM SDK; the server's own environment and code must never reach it.
    """

    ROOT = Path(__file__).resolve().parents[2]
    SDKS = ("anthropic", "openai", "google-genai", "google-generativeai", "mistralai", "cohere")

    def test_no_model_sdk_in_the_server_environment(self):
        locked = re.findall(r'^name = "([^"]+)"', (self.ROOT / "uv.lock").read_text(), re.M)
        self.assertFalse(set(locked) & set(self.SDKS), "a model SDK is in the server's lockfile")

    def test_no_server_code_imports_a_model_sdk_or_the_distiller(self):
        for path in [*self.ROOT.glob("memories/**/*.py"), *self.ROOT.glob("config/**/*.py")]:
            with self.subTest(path=path.name):
                imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", path.read_text(), re.M)
                roots = {i.split(".")[0] for i in imports}
                self.assertFalse(roots & {"anthropic", "openai", "distil"}, path)
