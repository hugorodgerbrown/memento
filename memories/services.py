"""
The server's entire behaviour. Each MCP tool is a thin wrapper around one of
these functions. Nothing here generates text: it validates, stores, searches,
counts, schedules and deletes.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    Capture,
    CaptureStatus,
    Citation,
    Client,
    ClientMode,
    Entry,
    Kind,
    Profile,
    Scope,
    SupersedeReason,
    Tombstone,
)

# "hiring", "person:alice", "project:memento", "place:lisbon"
TAG_RE = re.compile(r"^(?:(person|project|place|org):)?[a-z0-9][a-z0-9-]{0,47}$")
MAX_TAGS = 12


def normalise_tags(tags: list[str]) -> list[str]:
    cleaned = sorted({t.strip().lower().replace(" ", "-") for t in tags if t.strip()})
    bad = [t for t in cleaned if not TAG_RE.match(t)]
    if bad:
        raise ValidationError(
            f"Invalid tags {bad}. Use lowercase-hyphenated topics, or a prefix: "
            "person:, project:, place:, org:."
        )
    if len(cleaned) > MAX_TAGS:
        raise ValidationError(f"At most {MAX_TAGS} tags per entry.")
    return cleaned


def _owned(owner, ids) -> list[Entry]:
    found = list(Entry.objects.filter(owner=owner, pk__in=ids))
    if len(found) != len(set(ids)):
        raise ValidationError("One or more entry ids do not exist.")
    return found


def _capture(owner, capture) -> Capture:
    if isinstance(capture, Capture):
        if capture.owner_id != owner.pk:
            raise ValidationError("Capture does not exist.")
        return capture
    found = Capture.objects.filter(owner=owner, pk=capture).first()
    if not found:
        raise ValidationError("Capture does not exist.")
    return found


def _squash(text: str) -> str:
    return " ".join(text.split())


# --- time: the server is the arbiter (0013) ---------------------------------


def user_timezone(owner) -> ZoneInfo:
    name = Profile.objects.filter(owner=owner).values_list("timezone", flat=True).first()
    return ZoneInfo(name or "UTC")


def local_now(owner) -> datetime:
    return timezone.localtime(timezone.now(), user_timezone(owner))


def today_is(owner) -> str:
    """What every time error tells the model, e.g. Today is Friday 25 Sep 2026 (Europe/London)."""
    now = local_now(owner)
    return f"Today is {now:%A} {now.day} {now:%b %Y} ({now.tzinfo})."


RELATIVE_TIME = re.compile(
    r"\b(today|tonight|yesterday|tomorrow|recently|"
    r"this (?:morning|afternoon|evening|week|weekend|month|year)|"
    r"last (?:night|week|weekend|month|year)|next (?:week|weekend|month|year)|"
    r"(?:a|one|two|three|four|five|six|several|few|\d+) (?:days?|weeks?|months?|years?) ago)\b",
    re.IGNORECASE,
)
FUTURE_TOLERANCE = timedelta(minutes=5)


def _check_time(
    owner, kind: str, claim: str, happened_at: datetime | None, supersede_reason: str
) -> None:
    found = RELATIVE_TIME.search(claim)
    if found:
        raise ValidationError(
            f'claim says "{found.group(0)}", which will mean nothing later. Write the date '
            f"instead. {today_is(owner)}"
        )
    # A scheduled change ("moving to Porto in December") may be dated ahead: 0009 shows
    # both entries until it happens. Any other memory dated in the future is a mistake.
    future = happened_at and happened_at > timezone.now() + FUTURE_TOLERANCE
    if kind == Kind.MEMORY and future and supersede_reason != SupersedeReason.CHANGE:
        raise ValidationError(
            "A memory is something that has happened, but happened_at is in the future. "
            f"{today_is(owner)} For something planned, use kind thought or reminder."
        )


# --- tags ------------------------------------------------------------------


def _one_edit_apart(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1 :] == short for i in range(len(long_)))


def _plural_pair(a: str, b: str) -> bool:
    return any(a + end == b or b + end == a for end in ("s", "es"))


def tag_warnings(owner, tags: list[str]) -> list[str]:
    """
    New tags that look like existing ones: one edit apart, or singular and plural.
    A warning, not a rejection: "run" and "rum" can both be right.
    """
    existing = {tag for tag, _ in list_tags(owner)}
    warnings = []
    for tag in tags:
        if tag in existing:
            continue
        near = sorted(
            old
            for old in existing
            if _plural_pair(tag, old)
            or (min(len(tag), len(old)) >= 4 and _one_edit_apart(tag, old))
        )
        if near:
            warnings.append(
                f'New tag "{tag}" looks like existing {", ".join(repr(n) for n in near)}. '
                "If they mean the same, use the existing tag from now on."
            )
    return warnings


def is_excerpt(raw_text: str, capture: Capture) -> bool:
    """Exact words, whitespace-insensitive, from the transcript or any later revision."""
    needle = _squash(raw_text)
    return bool(needle) and any(needle in _squash(t) for t in capture.texts())


# --- remember --------------------------------------------------------------


@transaction.atomic
def remember(
    owner,
    *,
    client_name: str,
    kind: str,
    raw_text: str,
    claim: str,
    tags: list[str] | None = None,
    happened_at: datetime | None = None,
    happened_precision: str = "",
    due_at: datetime | None = None,
    supersedes: str | None = None,
    supersede_reason: str = "",
    valid_until: datetime | None = None,
    sources: list[str] | None = None,
    covers_from: datetime | None = None,
    covers_to: datetime | None = None,
    model_name: str = "",
    capture: str | Capture | None = None,
    external_ref: str = "",
) -> Entry:
    if not raw_text.strip():
        raise ValidationError("raw_text is required: the user's own words, verbatim.")
    if kind == Kind.DIGEST and not sources:
        raise ValidationError("A digest must cite the entries it was built from.")
    if kind != Kind.DIGEST and sources:
        raise ValidationError("Only digests cite sources.")
    _check_time(owner, kind, claim, happened_at, supersede_reason)
    if kind == Kind.REMINDER and not due_at:
        raise ValidationError("A reminder needs due_at: when to remind the user.")
    if kind != Kind.REMINDER and due_at:
        raise ValidationError("Only reminders have due_at. Leave it out, or use kind reminder.")

    source_capture = None
    if capture:
        source_capture = _capture(owner, capture)
        if kind == Kind.DIGEST:
            raise ValidationError("Digests cite entries, not captures.")
        if not is_excerpt(raw_text, source_capture):
            raise ValidationError(
                "raw_text must be copied exactly from the capture's transcript. "
                "Fix misheard words in claim, never in raw_text."
            )
        if happened_at is None:
            happened_at, happened_precision = source_capture.captured_at, "exact"

    previous = None
    if bool(supersedes) != bool(supersede_reason):
        raise ValidationError(
            "supersedes and supersede_reason go together. Use 'correction' if the old entry "
            "was never true, or 'change' if it was true until this happened."
        )
    if supersedes:
        (previous,) = _owned(owner, [supersedes])
        if hasattr(previous, "superseded_by"):
            raise ValidationError(
                f"Entry {supersedes} was already superseded by {previous.superseded_by.pk}. "
                "Supersede the latest version instead."
            )
        if supersede_reason == SupersedeReason.CHANGE:
            if happened_at is None:
                raise ValidationError("A change must say when it happened: set happened_at.")
            if previous.happened_at and happened_at < previous.happened_at:
                raise ValidationError("A change can't happen before the thing it changes.")

    # Proactive capture means the same observation may be sent twice, by a retry,
    # a second client, or a model re-logging something from earlier in the chat.
    if not (supersedes or sources or external_ref):
        duplicate = _find_duplicate(owner, kind, raw_text, happened_at, source_capture)
        if duplicate:
            duplicate.was_duplicate = True
            duplicate.warnings = []
            return duplicate

    tags = normalise_tags(tags or [])
    warnings = tag_warnings(owner, tags)
    entry = Entry(
        owner=owner,
        kind=kind,
        raw_text=raw_text,
        claim=claim.strip(),
        tags=tags,
        happened_at=happened_at,
        happened_precision=happened_precision,
        due_at=due_at,
        supersedes=previous,
        supersede_reason=supersede_reason,
        valid_until=valid_until,
        covers_from=covers_from,
        covers_to=covers_to,
        client_name=client_name,
        model_name=model_name,
        capture=source_capture,
        external_ref=external_ref,
    )
    entry.full_clean(exclude=["search"])
    entry.save()
    entry.was_duplicate = False
    entry.warnings = warnings

    if sources:
        cited = _owned(owner, sources)
        Citation.objects.bulk_create(Citation(digest=entry, source=s) for s in cited)

    _close_validity(entry, previous)
    return entry


DUPLICATE_WINDOW = timedelta(hours=1)


def _find_duplicate(owner, kind, raw_text, happened_at, capture) -> Entry | None:
    """
    Same words, same kind, same day: the same observation. "Good sleep" on two
    different mornings is two entries; "Good sleep" sent twice this morning is one.
    """
    qs = Entry.objects.filter(owner=owner, kind=kind, superseded_by__isnull=True)
    if capture:
        qs = qs.filter(capture=capture)
    if happened_at:
        qs = qs.filter(happened_at__date=happened_at.date())
    else:
        qs = qs.filter(happened_at__isnull=True, recorded_at__gte=timezone.now() - DUPLICATE_WINDOW)
    needle = _squash(raw_text).casefold()
    for candidate in qs.only("pk", "raw_text", "claim")[:50]:
        if _squash(candidate.raw_text).casefold() == needle:
            return candidate
    return None


def _close_validity(entry: Entry, previous: Entry | None) -> None:
    """
    A change ends the validity of what it replaces. If a change is later
    corrected (say, the move was in March, not February), the end date of the
    original fact follows the correction.
    """
    if not previous:
        return
    if entry.supersede_reason == SupersedeReason.CHANGE:
        target = previous
    elif previous.supersede_reason == SupersedeReason.CHANGE and entry.happened_at:
        target = previous.supersedes
    else:
        return
    if target is not None:
        target.valid_until = entry.happened_at
        target.save(update_fields=["valid_until"])


def current_version(entry: Entry) -> Entry:
    """
    The version of this entry that stands now. Anything holding an old id -- a
    source system keyed on external_ref, a client working from a stale recall --
    must act on the head, not on the row it remembers. Chains never branch:
    `supersedes` is one-to-one and set only at creation.
    """
    while hasattr(entry, "superseded_by"):
        entry = entry.superseded_by
    return entry


# --- recall ----------------------------------------------------------------


VIEWS = {"current", "history", "all"}


def visible(owner, view: str = "current", as_of: datetime | None = None):
    """
    current  What's true now, or at as_of: excludes anything corrected away,
             anything whose validity has ended, and (with as_of) anything that
             hadn't happened yet.
    history  Everything that was ever true. Only corrected-away entries are hidden.
    all      Everything, including entries that were never true.
    """
    if view not in VIEWS:
        raise ValidationError("view must be current, history or all.")
    qs = Entry.objects.filter(owner=owner)
    if view == "all":
        return qs
    qs = qs.exclude(superseded_by__supersede_reason=SupersedeReason.CORRECTION)
    if view == "history":
        return qs
    at = as_of or timezone.now()
    qs = qs.filter(Q(valid_until__isnull=True) | Q(valid_until__gt=at))
    if as_of:
        qs = qs.filter(Q(happened_at__lte=at) | Q(happened_at__isnull=True, recorded_at__lte=at))
    return qs


def recall(
    owner,
    *,
    query: str = "",
    tags: list[str] | None = None,
    kinds: list[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    ids: list[str] | None = None,
    view: str = "current",
    as_of: datetime | None = None,
    limit: int = 20,
):
    qs = visible(owner, view, as_of)
    if ids:
        qs = qs.filter(pk__in=ids)
    if tags:
        qs = qs.filter(tags__contains=normalise_tags(tags))
    if kinds:
        qs = qs.filter(kind__in=kinds)
    if since:
        qs = qs.filter(happened_at__gte=since)
    if until:
        qs = qs.filter(happened_at__lte=until)
    if query:
        q = SearchQuery(query, search_type="websearch", config="english")
        qs = qs.filter(search=q).annotate(rank=SearchRank("search", q))
        qs = qs.order_by("-rank", "-recorded_at")
    else:
        qs = qs.order_by("-recorded_at")
    return qs.prefetch_related("citations")[: min(limit, 100)]


# --- timeline and list_tags: shape before detail ---------------------------


@dataclass
class Bucket:
    period: datetime
    tag: str
    count: int


def timeline(
    owner, *, since: datetime, until: datetime, bucket: str = "week", tag_prefix: str = ""
) -> list[Bucket]:
    if bucket not in {"week", "month", "quarter"}:
        raise ValidationError("bucket must be week, month or quarter.")
    sql = """
        SELECT date_trunc(%s, COALESCE(e.happened_at, e.recorded_at)) AS period,
               t.tag, count(*)
        FROM memories_entry e
        CROSS JOIN LATERAL unnest(e.tags) AS t(tag)
        WHERE e.owner_id = %s
          AND e.kind <> 'digest'
          AND NOT EXISTS (SELECT 1 FROM memories_entry s
                          WHERE s.supersedes_id = e.id AND s.supersede_reason = 'correction')
          AND COALESCE(e.happened_at, e.recorded_at) BETWEEN %s AND %s
          AND t.tag LIKE %s
        GROUP BY 1, 2
        ORDER BY 1, 3 DESC, 2
    """
    with connection.cursor() as cur:
        cur.execute(sql, [bucket, owner.pk, since, until, f"{tag_prefix}%"])
        return [Bucket(*row) for row in cur.fetchall()]


def list_tags(owner, *, prefix: str = "") -> list[tuple[str, int]]:
    sql = """
        SELECT t.tag, count(*) FROM memories_entry e
        CROSS JOIN LATERAL unnest(e.tags) AS t(tag)
        WHERE e.owner_id = %s AND t.tag LIKE %s
        GROUP BY 1 ORDER BY 2 DESC, 1
    """
    with connection.cursor() as cur:
        cur.execute(sql, [owner.pk, f"{prefix}%"])
        return cur.fetchall()


# --- reminders ---------------------------------------------------------------


def complete_reminder(owner, entry_id: str) -> Entry:
    (entry,) = _owned(owner, [entry_id])
    if entry.kind != Kind.REMINDER:
        raise ValidationError("Only reminders can be completed.")
    entry.completed_at = timezone.now()
    entry.save(update_fields=["completed_at"])
    return entry


def due_reminders(now: datetime | None = None):
    """For the daily email cron. No LLM involved: it sends the raw words."""
    now = now or timezone.now()
    return (
        Entry.objects.filter(kind=Kind.REMINDER, completed_at__isnull=True, due_at__lte=now)
        .filter(superseded_by__isnull=True)
        .select_related("owner")
        .order_by("owner_id", "due_at")
    )


# --- inbox ---------------------------------------------------------------


def inbox(owner, *, limit: int = 10, received_since: datetime | None = None):
    """
    Voice notes waiting to be distilled, oldest first, with what already came of them.
    `received_since` narrows it to recent arrivals, so notes the distiller leaves for
    the user never crowd out new ones (0019).
    """
    qs = Capture.objects.filter(owner=owner, status=CaptureStatus.INBOX)
    if received_since:
        qs = qs.filter(received_at__gte=received_since)
    return qs.prefetch_related("entries").order_by("captured_at")[: min(limit, 50)]


def capture_text(
    owner, *, raw_text: str, client_name: str, model_name: str = "", fields: dict | None = None
) -> Capture:
    """
    The inbox fallback (0013): the user's words kept whole, for a client or the
    distiller to structure later. Used when a client sends raw_text alone, and
    always for clients in inbox mode, whose derived fields become hints.

    The same words on the same day (in the user's time zone) are one capture:
    the external id is derived from them, so a retry hits the unique constraint.
    """
    if not raw_text.strip():
        raise ValidationError("raw_text is required: the user's own words, verbatim.")
    day = local_now(owner).date().isoformat()
    digest = hashlib.sha256(_squash(raw_text).casefold().encode()).hexdigest()[:32]
    external_id = f"{day}:{digest}"
    hints = {"client": client_name, "model": model_name}
    if fields:
        hints["fields"] = fields
    try:
        with transaction.atomic():
            capture = Capture.objects.create(
                owner=owner,
                source="chat",
                external_id=external_id,
                segments=[{"text": raw_text}],
                text=raw_text,
                captured_at=timezone.now(),
                hints=hints,
            )
        capture.was_duplicate = False
    except IntegrityError:
        capture = Capture.objects.get(owner=owner, source="chat", external_id=external_id)
        capture.was_duplicate = True
    return capture


INBOX_CONTEXT_LIMIT = 25


def inbox_context(owner, *, limit: int = INBOX_CONTEXT_LIMIT) -> tuple[list[Entry], int]:
    """
    The owner's most recent current entries, handed over with the inbox so a
    client can supersede an existing entry instead of duplicating it (0014).
    Bounded, and it says how many it held back.
    """
    qs = visible(owner).exclude(kind=Kind.DIGEST).order_by("-recorded_at")
    shown = list(qs.prefetch_related("citations")[:limit])
    return shown, max(qs.count() - len(shown), 0)


def inbox_count(owner) -> int:
    return Capture.objects.filter(owner=owner, status=CaptureStatus.INBOX).count()


def close_capture(owner, capture_id: str, status: str) -> Capture:
    if status not in {CaptureStatus.PROCESSED, CaptureStatus.DISMISSED}:
        raise ValidationError("status must be processed or dismissed.")
    capture = _capture(owner, capture_id)
    capture.status = status
    capture.closed_at = timezone.now()
    capture.save(update_fields=["status", "closed_at"])
    return capture


# --- forget ----------------------------------------------------------------


@dataclass
class ForgetPlan:
    entries: list[Entry]
    captures: list[Capture]
    note: str = ""


def forget_plan(
    owner, entry_id: str | None = None, *, capture_id: str | None = None, scope: str = "entry"
) -> ForgetPlan:
    """
    Everything that must go. scope="entry": every version in the entry's
    correction chain plus every digest citing any of them, transitively.
    scope="source": the voice note it came from, every entry from that note,
    and their chains and digests. Pass capture_id to forget a voice note directly.
    """
    if scope not in {"entry", "source"}:
        raise ValidationError("scope must be entry or source.")
    captures: set = set()
    seeds: set = set()

    if capture_id:
        captures.add(_capture(owner, capture_id).pk)
    else:
        (entry,) = _owned(owner, [entry_id])
        seeds.add(entry.pk)
        if scope == "source" and entry.capture_id:
            captures.add(entry.capture_id)

    seeds |= set(Entry.objects.filter(capture_id__in=captures).values_list("pk", flat=True))

    doomed: set = set()
    for pk in seeds:
        start = Entry.objects.get(pk=pk)
        node = start
        while node:
            doomed.add(node.pk)
            node = node.supersedes
        node = start
        while hasattr(node, "superseded_by"):
            node = node.superseded_by
            doomed.add(node.pk)

    frontier = set(doomed)
    while frontier:
        citing = (
            set(Citation.objects.filter(source_id__in=frontier).values_list("digest_id", flat=True))
            - doomed
        )
        doomed |= citing
        frontier = citing

    entries = list(Entry.objects.filter(owner=owner, pk__in=doomed).order_by("recorded_at"))
    note = ""
    if not captures and any(e.capture_id for e in entries):
        note = (
            "The voice note transcript still contains these words. "
            "Use scope=source to forget the recording as well."
        )
    return ForgetPlan(entries, list(Capture.objects.filter(pk__in=captures)), note)


UNDO_WINDOW = timedelta(minutes=15)


def is_simple_undo(plan: ForgetPlan) -> bool:
    """
    "Don't log that", straight after a save, may skip the preview: one entry,
    recorded moments ago, from no voice note, that nothing cites or supersedes;
    or one note saved to the inbox moments ago with raw_text alone, and nothing
    made from it yet.
    """
    if not plan.entries and len(plan.captures) == 1:
        (capture,) = plan.captures
        return capture.source == "chat" and capture.received_at >= timezone.now() - UNDO_WINDOW
    if len(plan.entries) != 1 or plan.captures:
        return False
    (entry,) = plan.entries
    return (
        entry.recorded_at >= timezone.now() - UNDO_WINDOW
        and entry.supersedes_id is None
        and entry.capture_id is None
    )


@transaction.atomic
def forget(
    owner, entry_id: str | None = None, *, capture_id: str | None = None, scope: str = "entry"
) -> ForgetPlan:
    plan = forget_plan(owner, entry_id, capture_id=capture_id, scope=scope)
    ids = [e.pk for e in plan.entries]
    # Break the chain first so SET_NULL doesn't try to rewrite rows we're deleting.
    Entry.objects.filter(pk__in=ids).update(supersedes=None, supersede_reason="")
    Entry.objects.filter(pk__in=ids).delete()
    Capture.objects.filter(pk__in=[c.pk for c in plan.captures]).delete()
    Tombstone.objects.bulk_create(
        [
            Tombstone(owner=owner, object_id=e.pk, object_type="entry", external_ref=e.external_ref)
            for e in plan.entries
        ]
        + [
            Tombstone(
                owner=owner,
                object_id=c.pk,
                object_type="capture",
                external_ref=f"{c.source}:{c.external_id}",
            )
            for c in plan.captures
        ],
        ignore_conflicts=True,
    )
    return plan


FORGET_TOKEN_SALT = "memento.forget"
FORGET_TOKEN_MAX_AGE = timedelta(hours=1)


def _plan_ids(owner, plan: ForgetPlan) -> dict:
    return {
        "owner": owner.pk,
        "entries": sorted(str(e.pk) for e in plan.entries),
        "captures": sorted(str(c.pk) for c in plan.captures),
    }


def forget_token(owner, plan: ForgetPlan) -> str:
    """
    Proof that this exact plan was previewed. A confirm must carry it, so what is
    deleted is what the user was shown: if anything joins the plan in between,
    such as a new digest citing the entry, the token no longer matches.
    """
    return signing.dumps(_plan_ids(owner, plan), salt=FORGET_TOKEN_SALT)


def forget_token_matches(owner, plan: ForgetPlan, token: str) -> bool:
    try:
        signed = signing.loads(token, salt=FORGET_TOKEN_SALT, max_age=FORGET_TOKEN_MAX_AGE)
    except signing.BadSignature:
        return False
    return signed == _plan_ids(owner, plan)


def forgotten(owner, ids: list[str]) -> dict:
    """Which of these ids were deliberately forgotten, and when."""
    return dict(
        Tombstone.objects.filter(owner=owner, object_id__in=ids).values_list(
            "object_id", "forgotten_at"
        )
    )


def is_forgotten_ref(owner, external_ref: str) -> bool:
    return Tombstone.objects.filter(owner=owner, external_ref=external_ref).exists()


# --- clients ---------------------------------------------------------------

TOKEN_PREFIX = "mem_"
LAST_USED_RESOLUTION = timedelta(minutes=5)


def _token_hash(token: str) -> str:
    # The token is 256 random bits, so a fast hash is enough: there is nothing to brute-force.
    return hashlib.sha256(token.encode()).hexdigest()


def create_client(
    owner, name: str, *, scopes: list[str] | None = None, mode: str = ClientMode.DIRECT
) -> tuple[Client, str]:
    """A new client and its bearer token. The token is returned once and never stored."""
    scopes = sorted(set(scopes or [Scope.READ, Scope.WRITE]))
    unknown = set(scopes) - set(Scope.values)
    if unknown:
        raise ValidationError(f"Unknown scopes {sorted(unknown)}. Use {', '.join(Scope.values)}.")
    if mode not in ClientMode.values:
        raise ValidationError("mode must be direct or inbox.")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    client = Client(
        owner=owner,
        name=name.strip(),
        token_hash=_token_hash(token),
        token_prefix=token[:12],
        scopes=scopes,
        mode=mode,
    )
    client.full_clean()
    client.save()
    return client, token


def authenticate(token: str) -> Client | None:
    """The live client this bearer token belongs to, or None."""
    if not token.startswith(TOKEN_PREFIX):
        return None
    client = (
        Client.objects.select_related("owner")
        .filter(token_hash=_token_hash(token), revoked_at__isnull=True, owner__is_active=True)
        .first()
    )
    if client is None:
        return None
    now = timezone.now()
    if client.last_used_at is None or now - client.last_used_at > LAST_USED_RESOLUTION:
        client.last_used_at = now
        client.save(update_fields=["last_used_at"])
    return client


def seal_client(client: Client) -> Client:
    """
    Replace a client's token with one nobody is ever shown, so no bearer token can
    act as it. For clients that never use HTTP, such as Claude Desktop over stdio (0023).
    """
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    client.token_hash, client.token_prefix = _token_hash(token), "sealed"
    client.save(update_fields=["token_hash", "token_prefix"])
    return client


def revoke_client(client: Client) -> Client:
    client.revoked_at = client.revoked_at or timezone.now()
    client.save(update_fields=["revoked_at"])
    return client
