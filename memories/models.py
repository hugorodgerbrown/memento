"""
Memento data model.

Each principle from the brief is enforced here, not just documented:

1. Raw is sacred          Entry.raw_text is immutable (model guard + DB trigger).
2. Server never generates Every text field is written by the client; the server
                          only adds timestamps and the search vector.
3. Every answer cites     Digests link to their sources through Citation.
4. Corrections append     A correction is a new Entry that `supersedes` the old one.
5. Forgetting is a right  services.forget() hard-deletes, cascading to citing digests.
6. Time has two axes      happened_at (+ precision) is separate from recorded_at.
"""

import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

# Time-ordered ids (better index locality) on Python 3.14+, random ids before.
new_id = getattr(uuid, "uuid7", uuid.uuid4)


class Kind(models.TextChoices):
    MEMORY = "memory", "Memory"  # something that happened
    THOUGHT = "thought", "Thought"  # an idea, opinion or open question
    DECISION = "decision", "Decision"  # a choice made, ideally with its reason
    REMINDER = "reminder", "Reminder"  # something to act on at due_at
    DIGEST = "digest", "Digest"  # a client-written synthesis of other entries


class SupersedeReason(models.TextChoices):
    CORRECTION = "correction", "Correction: the old entry was never true"
    CHANGE = "change", "Change: the old entry was true until this happened"


class Precision(models.TextChoices):
    EXACT = "exact", "Exact time"
    DAY = "day", "Day"
    MONTH = "month", "Month"  # "last March"
    YEAR = "year", "Year"  # "back in 2019"


class Entry(models.Model):
    id = models.UUIDField(primary_key=True, default=new_id, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="entries"
    )
    kind = models.CharField(max_length=16, choices=Kind)

    # The tattoo. The user's own words, verbatim, never rewritten.
    raw_text = models.TextField()

    # Derived by the client at write time. Rebuildable from raw_text.
    claim = models.CharField(max_length=280)
    tags = ArrayField(models.CharField(max_length=64), default=list, blank=True)
    happened_at = models.DateTimeField(null=True, blank=True)
    happened_precision = models.CharField(max_length=8, choices=Precision, blank=True)
    # When this stopped (or will stop) being true. Set by the client for known
    # ends ("on leave until March"), or by the server when a change supersedes it.
    valid_until = models.DateTimeField(null=True, blank=True)

    # Reminders
    due_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)

    # Digests: the window they summarise, and the entries they cite
    covers_from = models.DateTimeField(null=True, blank=True)
    covers_to = models.DateTimeField(null=True, blank=True)
    sources = models.ManyToManyField(
        "self", through="Citation", symmetrical=False, related_name="cited_by"
    )

    # Versions form a single chain: each entry can be superseded at most once.
    # PROTECT: deleting part of a chain must go through services.forget().
    supersedes = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="superseded_by",
    )
    supersede_reason = models.CharField(max_length=16, choices=SupersedeReason, blank=True)

    # Voice capture this entry was distilled from (raw_text must be an excerpt of it)
    capture = models.ForeignKey(
        "Capture", null=True, blank=True, on_delete=models.PROTECT, related_name="entries"
    )
    # Id in a source system, e.g. "pocket:gai_001". Makes webhook retries idempotent.
    external_ref = models.CharField(max_length=128, blank=True)

    # Provenance
    client_name = models.CharField(max_length=64)  # from the auth token: trusted
    model_name = models.CharField(max_length=64, blank=True)  # self-reported
    recorded_at = models.DateTimeField(auto_now_add=True)

    # Full-text search over the claim (weighted higher) and the raw words.
    search = models.GeneratedField(
        expression=SearchVector("claim", weight="A", config="english")
        + SearchVector("raw_text", weight="B", config="english"),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        verbose_name_plural = "entries"
        indexes = [
            GinIndex(fields=["search"], name="entry_search_gin"),
            GinIndex(fields=["tags"], name="entry_tags_gin"),
            models.Index(fields=["owner", "-happened_at"], name="entry_owner_happened"),
            models.Index(fields=["owner", "-recorded_at"], name="entry_owner_recorded"),
            models.Index(
                fields=["due_at"],
                condition=Q(kind="reminder", completed_at__isnull=True),
                name="entry_open_reminders",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(kind="reminder", due_at__isnull=False)
                | (~Q(kind="reminder") & Q(due_at__isnull=True)),
                name="due_at_iff_reminder",
            ),
            models.CheckConstraint(
                condition=~Q(kind="digest")
                | Q(
                    covers_from__isnull=False,
                    covers_to__isnull=False,
                    covers_from__lte=F("covers_to"),
                ),
                name="digest_has_window",
            ),
            models.CheckConstraint(
                condition=Q(happened_at__isnull=True, happened_precision="")
                | (Q(happened_at__isnull=False) & ~Q(happened_precision="")),
                name="precision_iff_happened_at",
            ),
            models.CheckConstraint(condition=~Q(supersedes=F("id")), name="cannot_supersede_self"),
            models.CheckConstraint(
                condition=Q(supersedes__isnull=True, supersede_reason="")
                | (Q(supersedes__isnull=False) & ~Q(supersede_reason="")),
                name="reason_iff_supersedes",
            ),
            models.CheckConstraint(
                condition=~Q(supersede_reason="change") | Q(happened_at__isnull=False),
                name="change_says_when",
            ),
            models.CheckConstraint(
                condition=Q(valid_until__isnull=True)
                | Q(happened_at__isnull=True)
                | Q(valid_until__gte=F("happened_at")),
                name="valid_until_after_happened",
            ),
            models.CheckConstraint(
                condition=~Q(kind="digest", capture__isnull=False),
                name="digests_have_no_capture",
            ),
            models.UniqueConstraint(
                fields=["owner", "external_ref"],
                condition=~Q(external_ref=""),
                name="unique_external_ref",
            ),
        ]

    def __str__(self):
        return f"{self.kind}: {self.claim}"

    def save(self, *args, **kwargs):
        # Principle 1, application layer. The DB trigger in migration 0002
        # catches anything that bypasses save(), such as QuerySet.update().
        if not self._state.adding:
            stored = Entry.objects.filter(pk=self.pk).values_list("raw_text", flat=True).first()
            if stored is not None and stored != self.raw_text:
                raise ValidationError("raw_text is immutable. Record a correction instead.")
        super().save(*args, **kwargs)


class Citation(models.Model):
    """A digest citing one of the entries it was built from."""

    digest = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="citations")
    source = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["digest", "source"], name="unique_citation"),
            models.CheckConstraint(condition=~Q(digest=F("source")), name="no_self_citation"),
        ]

    def __str__(self):
        return f"{self.digest_id} cites {self.source_id}"


class CaptureStatus(models.TextChoices):
    INBOX = "inbox", "Waiting to be processed"
    PROCESSED = "processed", "Processed into entries"
    DISMISSED = "dismissed", "Dismissed, nothing worth keeping"


class Capture(models.Model):
    """
    A voice note, stored the moment it arrives. The transcript is the raw record:
    immutable (DB trigger in 0004). Edits made later in Pocket are appended to
    `revisions`, never written over the original.
    """

    id = models.UUIDField(primary_key=True, default=new_id, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="captures"
    )
    source = models.CharField(max_length=32, default="pocket")
    external_id = models.CharField(max_length=128)  # Pocket recording id

    # The tattoo
    segments = models.JSONField()  # [{speaker, text, start, end}], as received
    text = models.TextField()  # segments joined; excerpts are checked against this
    captured_at = models.DateTimeField()  # when you spoke: default happened_at

    # Appended when you edit the transcript in Pocket: [{at, text}]
    revisions = models.JSONField(default=list, blank=True)

    # Another model's reading. Offered to your LLM, never treated as raw.
    title = models.CharField(max_length=255, blank=True)
    hints = models.JSONField(default=dict, blank=True)

    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    language = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16, choices=CaptureStatus, default=CaptureStatus.INBOX)
    closed_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "source", "external_id"], name="unique_capture_per_source"
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "status", "-captured_at"], name="capture_inbox"),
        ]

    def __str__(self):
        return f"{self.source} {self.external_id} ({self.status})"

    def texts(self) -> list[str]:
        """The original transcript followed by every later revision."""
        return [self.text] + [r["text"] for r in self.revisions]


def validate_timezone(name: str) -> None:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValidationError(
            f"{name!r} is not a time zone. Use an IANA name, e.g. Europe/London."
        ) from e


class Profile(models.Model):
    """Per-user settings the server needs to be the arbiter of time (0013)."""

    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    timezone = models.CharField(max_length=64, default="UTC", validators=[validate_timezone])

    def __str__(self):
        return f"{self.owner} ({self.timezone})"


class PocketLink(models.Model):
    """Connects a Pocket account to a Memento user, and says which voice is yours."""

    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pocket"
    )
    pocket_user_id = models.CharField(max_length=128, unique=True)
    speaker_label = models.CharField(
        max_length=128, help_text="Your name as Pocket's voice print labels you."
    )

    def __str__(self):
        return f"Pocket {self.pocket_user_id} for {self.owner}"


class IngestLog(models.Model):
    """
    Why each webhook delivery was stored or skipped. Holds no transcript content,
    so skipped recordings of other people leave no trace of what they said.
    """

    class Decision(models.TextChoices):
        STORED = "stored"
        UPDATED = "updated"
        DUPLICATE = "duplicate"
        SKIPPED = "skipped"
        IGNORED = "ignored"
        REJECTED = "rejected"

    received_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=32, default="pocket")
    event = models.CharField(max_length=64)
    external_id = models.CharField(max_length=128, blank=True)
    decision = models.CharField(max_length=16, choices=Decision)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.event} {self.external_id}: {self.decision}"


class Tombstone(models.Model):
    """
    Proof that something was deliberately forgotten: ids and a date, no content.
    Lets clients tell "forgotten" from "never existed", and stops a source
    re-delivering what you forgot.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tombstones"
    )
    object_id = models.UUIDField()
    object_type = models.CharField(max_length=16)  # "entry" or "capture"
    external_ref = models.CharField(max_length=160, blank=True)  # e.g. "pocket:rec_1"
    forgotten_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "object_id"], name="one_tombstone_per_object"),
        ]
        indexes = [models.Index(fields=["owner", "external_ref"], name="tombstone_external_ref")]

    def __str__(self):
        return f"{self.object_type} {self.object_id} forgotten {self.forgotten_at:%Y-%m-%d}"


class Scope(models.TextChoices):
    READ = "memento:read", "Read entries and the inbox"
    WRITE = "memento:write", "Save entries and close captures"
    FORGET = "memento:forget", "Permanently delete"


class ClientMode(models.TextChoices):
    DIRECT = "direct", "Direct: the client structures its own entries"
    INBOX = "inbox", "Inbox: every save goes to the inbox for the distiller (M3)"


def default_scopes() -> list[str]:
    return [Scope.READ, Scope.WRITE]


class Client(models.Model):
    """
    An MCP client allowed to reach one owner's memory: Claude Code, the
    distiller, and later OAuth clients (M8). Its name is what provenance records
    as client_name. Only a hash of the bearer token is stored; the token itself
    is shown once, when the client is created.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="clients"
    )
    name = models.CharField(max_length=64)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    token_prefix = models.CharField(max_length=12, editable=False)  # to recognise a token
    scopes = ArrayField(models.CharField(max_length=32, choices=Scope), default=default_scopes)
    mode = models.CharField(max_length=8, choices=ClientMode, default=ClientMode.DIRECT)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="unique_client_name"),
        ]

    def __str__(self):
        return f"{self.name} ({self.owner})"
