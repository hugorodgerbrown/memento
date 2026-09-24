"""
The MCP surface: nine tools, each a thin wrapper around one function in
services.py. Nothing here decides anything the services don't: it checks the
client's scope, parses arguments, calls the service, and shapes the result.

The descriptions are the product (Principle 7). They must match
docs/mcp-tools.md exactly; a test compares them.
"""

import contextvars
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from . import services
from .models import Capture, Client, ClientMode, Entry, Kind, Precision, Scope

# --- descriptions: exactly as in docs/mcp-tools.md ---------------------------

INSTRUCTIONS = (
    "Memento is the user's long-term memory. Save what they tell you about their own life "
    "with remember, and answer questions about their past with recall, citing the entries "
    'you used. Every save returns a receipt to relay; "don\'t log that" is undone with forget.'
)

REMEMBER = (
    "Save what the user tells you about their own life, whether or not they ask: health, "
    "symptoms, sleep, exercise, food, mood, work, people they saw, decisions, plans and "
    "reminders. Split each message into one entry per observation. Don't save questions, tasks "
    "you're helping with, hypotheticals, fiction, or other people's private details. If unsure, "
    'ask "Log this?" once. Call list_tags first. After saving, tell the user in one line what '
    "you saved."
)

SAVE_DIGEST = (
    "Save a synthesis you wrote from the user's entries, such as a monthly summary or an "
    "analysis of how their thinking on a topic changed. Only call this when the user asks you "
    "to save or keep the synthesis.\n"
    "\n"
    "`sources` must list the id of every entry you drew on, and nothing you didn't. "
    "`covers_from` and `covers_to` are the period the digest describes. `raw_text` is your full "
    "synthesis; `claim` is its one-sentence conclusion.\n"
    "\n"
    "Entries record what the user mentioned, not everything that happened. Don't state how "
    "often something happened unless the cited entries say so.\n"
    "\n"
    "A digest is deleted automatically if the user later forgets any entry it cites, so cite "
    "precisely."
)

RECALL = (
    "Search the user's saved entries. Use this before answering any question about the user's "
    "past: what they decided, planned, thought, did, or said.\n"
    "\n"
    "Search is keyword-based with stemming, not semantic. If a search returns nothing, try "
    'synonyms or related tags before concluding nothing exists. Supports "quoted phrases" and '
    "-exclusions.\n"
    "\n"
    "`view` controls which versions you see:\n"
    "- `current` (default): what's true now. Pass `as_of` for what was true at a past date "
    '("where was I living in 2025?").\n'
    "- `history`: everything that was ever true, including things that later changed. Use for "
    "questions about how something evolved.\n"
    "- `all`: also includes entries later corrected as never true. Use only when the user asks "
    "what they originally said.\n"
    "\n"
    'Each entry shows `valid_until` and its `supersede_reason`, so you can say "you lived in '
    'Lisbon until February 2026".\n'
    "\n"
    "If an id you ask for comes back as forgotten, the user deliberately deleted it. Say so; "
    "don't search for it elsewhere.\n"
    "\n"
    "When you answer, cite the entries you relied on with their dates, and quote `raw_text` "
    "when exact wording matters. If nothing matches, say so plainly. Never fill gaps from "
    "general knowledge or from this conversation and present it as their memory. Entries are "
    "what the user mentioned: a missing entry means it wasn't mentioned, not that it didn't "
    "happen."
)

TIMELINE = (
    "Count the user's entries per tag over time, without returning their contents. Call this "
    'first for any question about change, patterns or a long period ("how has my thinking on '
    'hiring evolved", "what was I focused on this year"). Then use `recall` on the specific '
    "periods and tags that matter. Counts measure how often something was mentioned, not how "
    "often it happened."
)

LIST_TAGS = (
    "List the user's existing tags with how often each is used. Call this before `remember` so "
    "you reuse tags instead of creating near-duplicates, and before `recall` to discover what "
    "to search for. Filter with `prefix`, e.g. `person:` to list people. Counts are mentions, "
    "not occurrences."
)

COMPLETE_REMINDER = (
    "Mark a reminder as done when the user says they've done it. Safe to call twice."
)

INBOX = (
    "List notes waiting to be turned into entries, oldest first: voice notes from Pocket, and "
    "words saved with `raw_text` alone. Each has the transcript, when it was recorded, any hints "
    "(Pocket's summary, or the fields the saving client suggested), and any entries already "
    "created from it. The result also lists the user's most recent current entries, so you can "
    "update one instead of duplicating it.\n"
    "\n"
    "For each note: split it into separate entries (one per memory, thought, decision or "
    "reminder), each with an exact excerpt as `raw_text` and `capture` set. Don't recreate "
    "entries that already exist; if one's claim is wrong, supersede it. Treat hints as another "
    "model's reading: useful for orientation, never a source of facts. Then call "
    "`close_capture`.\n"
    "\n"
    "Transcription errors are common with names. If you're unsure what a word was, ask the user "
    "rather than guessing."
)

CLOSE_CAPTURE = (
    "Mark a voice note as `processed` once you've saved its entries, or `dismissed` if nothing "
    "in it is worth keeping. Its transcript stays stored either way."
)

FORGET = (
    "Permanently delete an entry, every corrected version of it, and every digest that cites "
    "any of them. Only call this when the user explicitly asks you to forget or delete "
    "something.\n"
    "\n"
    'For entries from a voice note, `scope: "entry"` leaves the transcript in place, and the '
    'preview will say so. `scope: "source"` also deletes the voice note and every entry made '
    "from it. Ask the user which they mean.\n"
    "\n"
    "A tombstone holding only the ids and the date is kept, so you can later tell the user "
    "something was forgotten rather than never saved. Nothing else survives.\n"
    "\n"
    "Always call it first with `confirm: false`. That returns everything that would be deleted, "
    "without deleting, and a `confirm_token`. Show the user that list in plain words, and call "
    "again with `confirm: true` and the `confirm_token` only after they agree. This cannot be "
    "undone.\n"
    "\n"
    'The one exception: when the user says "don\'t log that" right after a save, you may pass '
    "`confirm: true` directly. The server allows this only if the plan is a single entry, "
    "recorded in the last 15 minutes, that nothing cites."
)

# --- the calling client --------------------------------------------------------

current_client: contextvars.ContextVar[Client | None] = contextvars.ContextVar(
    "memento_client", default=None
)
_call_tz: contextvars.ContextVar[ZoneInfo | None] = contextvars.ContextVar(
    "memento_tz", default=None
)


def _tz() -> ZoneInfo:
    """The calling user's time zone, looked up once per tool call."""
    tz = _call_tz.get()
    if tz is None:
        tz = services.user_timezone(current_client.get().owner)
        _call_tz.set(tz)
    return tz


SCOPE_VERBS = {
    Scope.READ: "read the user's memory",
    Scope.WRITE: "save to the user's memory",
    Scope.FORGET: "delete from the user's memory",
}


def _client(scope: Scope) -> Client:
    client = current_client.get()
    if client is None:
        raise ToolError("Not authenticated. Send a Memento bearer token.")
    if scope not in client.scopes:
        raise ToolError(
            f"This client isn't allowed to {SCOPE_VERBS[scope]} ({scope}). "
            "Tell the user; they can grant it in Memento."
        )
    return client


def _fresh_connection():
    """
    What Django does at the start of each request, which /mcp bypasses: drop a
    connection that has died or outlived CONN_MAX_AGE. Never mid-transaction.
    """
    if not connection.in_atomic_block:
        close_old_connections()


INBOX_WAY_OUT = (
    " If you can't fix this, call remember again with raw_text alone: the user's words go to "
    "the inbox and are structured later, so nothing is lost."
)


async def _run(fn: Callable[[], dict[str, Any]], *, way_out: str = "") -> dict[str, Any]:
    """
    Run a sync service call off the event loop, turning validation into teaching
    errors, and stamp every result with `now` in the user's time zone (0013).
    """

    def call():
        _fresh_connection()
        _call_tz.set(None)
        try:
            result = fn()
        except ValidationError as e:
            raise ToolError(" ".join(e.messages) + way_out) from e
        return {"now": timezone.localtime(timezone.now(), _tz()).isoformat(), **result}

    return await sync_to_async(call)()


# --- arguments ---------------------------------------------------------------

ISO_HINT = "Use ISO 8601, such as 2026-09-10 or 2026-09-10T08:30:00+01:00."


def _when(value: str | None, field: str) -> tuple[datetime | None, bool]:
    """
    An aware datetime, and whether only a date was given. A date or time without
    a zone is read in the user's time zone, not the server's.
    """
    if not value:
        return None, False
    tz = _tz()
    try:
        if len(value.strip()) == 10 and (day := parse_date(value.strip())):
            return timezone.make_aware(datetime.combine(day, datetime.min.time()), tz), True
        parsed = parse_datetime(value.strip())
    except ValueError:
        parsed = None
    if parsed is None:
        raise ValidationError(f"{field} isn't a date I can read: {value!r}. {ISO_HINT}")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, tz)
    return parsed, False


def _at(value: str | None, field: str) -> datetime | None:
    return _when(value, field)[0]


# --- results -------------------------------------------------------------------


def _happened(entry: Entry) -> str | None:
    if entry.happened_at is None:
        return None
    at = timezone.localtime(entry.happened_at, _tz())
    return {
        Precision.DAY: at.date().isoformat(),
        Precision.MONTH: at.strftime("%Y-%m"),
        Precision.YEAR: at.strftime("%Y"),
    }.get(entry.happened_precision, at.isoformat())


def _iso(value: datetime | None) -> str | None:
    if not value:
        return None
    return timezone.localtime(value, _tz()).isoformat()


def entry_dict(entry: Entry) -> dict:
    """The one compact shape every tool returns an entry in (docs/mcp-tools.md)."""
    superseded_by = getattr(entry, "superseded_by", None)
    result = {
        "id": str(entry.pk),
        "kind": entry.kind,
        "claim": entry.claim,
        "raw_text": entry.raw_text,
        "tags": entry.tags,
        "happened_at": _happened(entry),
        "happened_precision": entry.happened_precision,
        "recorded_at": _iso(entry.recorded_at),
        "valid_until": _iso(entry.valid_until),
        "supersedes": str(entry.supersedes_id) if entry.supersedes_id else None,
        "supersede_reason": entry.supersede_reason,
        "superseded_by": str(superseded_by.pk) if superseded_by else None,
        "cites": [str(c.source_id) for c in entry.citations.all()],
    }
    if entry.kind == Kind.REMINDER:
        result["due_at"] = _iso(entry.due_at)
        result["completed_at"] = _iso(entry.completed_at)
    if entry.kind == Kind.DIGEST:
        result["covers_from"] = _iso(entry.covers_from)
        result["covers_to"] = _iso(entry.covers_to)
    if entry.capture_id:
        result["capture"] = str(entry.capture_id)
    return result


def _receipt(entry: Entry) -> dict:
    result = {
        "saved": [{"id": str(entry.pk), "claim": entry.claim}],
        "duplicate": entry.was_duplicate,
        "say": (
            'Tell the user in one line what you saved. If they say "don\'t log that", call '
            "forget with this id and confirm: true."
        ),
    }
    if entry.warnings:
        result["warnings"] = entry.warnings
    return result


def _inbox_receipt(capture: Capture) -> dict:
    return {
        "saved": [],
        "inbox": {"id": str(capture.pk), "text": capture.text},
        "duplicate": capture.was_duplicate,
        "say": (
            "Tell the user in one line that their words are kept in Memento's inbox and will be "
            'structured later. If they say "don\'t log that", call forget with this id as '
            "capture_id and confirm: true."
        ),
    }


def _capture_dict(capture: Capture) -> dict:
    return {
        "id": str(capture.pk),
        "source": capture.source,
        "captured_at": _iso(capture.captured_at),
        "title": capture.title,
        "transcript": capture.text,
        "revisions": [r["text"] for r in capture.revisions],
        "hints": capture.hints,
        "entries": [entry_dict(e) for e in capture.entries.all()],
    }


def _plan_dict(plan: services.ForgetPlan) -> dict:
    return {
        "entries": [entry_dict(e) for e in plan.entries],
        "voice_notes": [
            {"id": str(c.pk), "captured_at": _iso(c.captured_at), "title": c.title}
            for c in plan.captures
        ],
    }


# --- the server ----------------------------------------------------------------

mcp = MCPServer("memento", instructions=INSTRUCTIONS)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
IDEMPOTENT = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

EntryId = Annotated[str, Field(description="An entry id from recall or a previous save.")]


@mcp.tool(description=REMEMBER, annotations=WRITE)
async def remember(
    raw_text: Annotated[
        str,
        Field(
            description="The user's own words for this one observation, copied exactly, typos "
            "included. Never paraphrase."
        ),
    ],
    claim: Annotated[
        str | None,
        Field(
            description="One standalone sentence, at most 280 characters, with names and real "
            'dates ("yesterday" becomes "10 Sep 2026"). Fix typos here, not in raw_text. Omit '
            "claim and kind to send raw_text alone to the inbox, to be structured later."
        ),
    ] = None,
    kind: Annotated[
        Literal["memory", "thought", "decision", "reminder"] | None,
        Field(
            description="memory: something that happened or a state, like a symptom, sleep or "
            "activity. thought: an idea or opinion. decision: a choice made. reminder: something "
            "to act on, needs due_at."
        ),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Field(
            description="2 to 6, reusing tags from list_tags. Prefix people person:, projects "
            "project:, places place:, organisations org:."
        ),
    ] = None,
    happened_at: Annotated[
        str | None,
        Field(
            description='When it happened, not now. "This morning" is today. Omit if unknown; '
            "never guess."
        ),
    ] = None,
    happened_precision: Annotated[
        Literal["exact", "day", "month", "year"] | None,
        Field(description="How precisely the user said it."),
    ] = None,
    due_at: Annotated[str | None, Field(description="When to remind.")] = None,
    supersedes: Annotated[
        str | None,
        Field(description="When a saved entry is wrong or out of date. recall it first."),
    ] = None,
    supersede_reason: Annotated[
        Literal["correction", "change"] | None,
        Field(
            description="correction: it was never true. change: it was true until now; set "
            "happened_at to when it changed."
        ),
    ] = None,
    valid_until: Annotated[
        str | None,
        Field(description="Only when the user says something will stop being true."),
    ] = None,
    capture: Annotated[
        str | None,
        Field(description="From inbox. raw_text must then be an exact excerpt of the transcript."),
    ] = None,
    model_name: Annotated[str | None, Field(description="Your model name, for provenance.")] = None,
) -> dict[str, Any]:
    def call():
        client = _client(Scope.WRITE)
        derived = {
            "claim": claim, "kind": kind, "tags": tags, "happened_at": happened_at,
            "happened_precision": happened_precision, "due_at": due_at, "supersedes": supersedes,
            "supersede_reason": supersede_reason, "valid_until": valid_until, "capture": capture,
        }  # fmt: skip
        derived = {k: v for k, v in derived.items() if v}
        if client.mode == ClientMode.INBOX or not derived:
            # 0013: inbox-mode clients always, and anyone sending raw_text alone.
            return _inbox_receipt(
                services.capture_text(
                    client.owner,
                    raw_text=raw_text,
                    client_name=client.name,
                    model_name=model_name or "",
                    fields=derived,
                )
            )
        if not (claim and kind):
            raise ValidationError(
                "Send claim and kind together, or raw_text alone to keep the words in the inbox."
            )
        if len(claim) > 280:
            raise ValidationError(f"claim is {len(claim)} characters; keep it to 280 or fewer.")
        when, date_only = _when(happened_at, "happened_at")
        precision = happened_precision or ""
        if when and not precision:
            precision = Precision.DAY if date_only else Precision.EXACT
        if precision and not when:
            raise ValidationError("happened_precision needs happened_at. Omit both if unknown.")
        entry = services.remember(
            client.owner,
            client_name=client.name,
            kind=kind,
            raw_text=raw_text,
            claim=claim,
            tags=tags,
            happened_at=when,
            happened_precision=precision,
            due_at=_at(due_at, "due_at"),
            supersedes=supersedes,
            supersede_reason=supersede_reason or "",
            valid_until=_at(valid_until, "valid_until"),
            model_name=model_name or "",
            capture=capture,
        )
        return _receipt(entry)

    return await _run(call, way_out=INBOX_WAY_OUT)


@mcp.tool(description=SAVE_DIGEST, annotations=WRITE)
async def save_digest(
    raw_text: Annotated[str, Field(description="Your full synthesis.")],
    claim: Annotated[
        str, Field(max_length=280, description="The synthesis's one-sentence conclusion.")
    ],
    sources: Annotated[
        list[str],
        Field(min_length=1, description="The id of every entry you drew on, and no others."),
    ],
    covers_from: Annotated[str, Field(description="Start of the period described. ISO 8601.")],
    covers_to: Annotated[str, Field(description="End of the period described. ISO 8601.")],
    tags: Annotated[list[str] | None, Field(description="Tags, reusing list_tags.")] = None,
    model_name: Annotated[str | None, Field(description="Your model name, for provenance.")] = None,
) -> dict[str, Any]:
    def call():
        client = _client(Scope.WRITE)
        entry = services.remember(
            client.owner,
            client_name=client.name,
            kind=Kind.DIGEST,
            raw_text=raw_text,
            claim=claim,
            tags=tags,
            sources=sources,
            covers_from=_at(covers_from, "covers_from"),
            covers_to=_at(covers_to, "covers_to"),
            model_name=model_name or "",
        )
        return _receipt(entry)

    return await _run(call)


@mcp.tool(description=RECALL, annotations=READ_ONLY)
async def recall(
    query: Annotated[
        str | None, Field(description='Keywords. Supports "quoted phrases" and -exclusions.')
    ] = None,
    tags: Annotated[list[str] | None, Field(description="Entries must carry all of these.")] = None,
    kinds: Annotated[
        list[Literal["memory", "thought", "decision", "reminder", "digest"]] | None,
        Field(description="Only these kinds."),
    ] = None,
    since: Annotated[str | None, Field(description="happened_at on or after. ISO 8601.")] = None,
    until: Annotated[str | None, Field(description="happened_at on or before. ISO 8601.")] = None,
    ids: Annotated[list[str] | None, Field(description="Fetch these entries by id.")] = None,
    view: Annotated[
        Literal["current", "history", "all"], Field(description="Which versions to see.")
    ] = "current",
    as_of: Annotated[
        str | None, Field(description="With view current: what was true at this date.")
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="At most this many.")] = 20,
) -> dict[str, Any]:
    def call():
        client = _client(Scope.READ)
        entries = services.recall(
            client.owner,
            query=query or "",
            tags=tags,
            kinds=kinds,
            since=_at(since, "since"),
            until=_at(until, "until"),
            ids=ids,
            view=view,
            as_of=_at(as_of, "as_of"),
            limit=limit,
        )
        result = {"entries": [entry_dict(e) for e in entries]}
        if ids:
            gone = services.forgotten(client.owner, ids)
            result["forgotten"] = {str(k): _iso(v) for k, v in gone.items()}
        return result

    return await _run(call)


@mcp.tool(description=TIMELINE, annotations=READ_ONLY)
async def timeline(
    since: Annotated[
        str | None, Field(description="Start of the period. Defaults to a year ago.")
    ] = None,
    until: Annotated[str | None, Field(description="End of the period. Defaults to now.")] = None,
    bucket: Annotated[
        Literal["week", "month", "quarter"], Field(description="Size of each period.")
    ] = "month",
    tag_prefix: Annotated[
        str | None, Field(description="Only tags starting with this, e.g. person:.")
    ] = None,
) -> dict[str, Any]:
    def call():
        client = _client(Scope.READ)
        end = _at(until, "until") or timezone.now()
        start = _at(since, "since") or end - timedelta(days=365)
        buckets = services.timeline(
            client.owner, since=start, until=end, bucket=bucket, tag_prefix=tag_prefix or ""
        )
        return {
            "buckets": [
                {"period": b.period.date().isoformat(), "tag": b.tag, "mentions": b.count}
                for b in buckets
            ]
        }

    return await _run(call)


@mcp.tool(description=LIST_TAGS, annotations=READ_ONLY)
async def list_tags(
    prefix: Annotated[str | None, Field(description="Only tags starting with this.")] = None,
) -> dict[str, Any]:
    def call():
        client = _client(Scope.READ)
        rows = services.list_tags(client.owner, prefix=prefix or "")
        return {"tags": [{"tag": tag, "mentions": count} for tag, count in rows]}

    return await _run(call)


@mcp.tool(description=COMPLETE_REMINDER, annotations=IDEMPOTENT)
async def complete_reminder(entry_id: EntryId) -> dict[str, Any]:
    def call():
        client = _client(Scope.WRITE)
        return {"completed": entry_dict(services.complete_reminder(client.owner, entry_id))}

    return await _run(call)


@mcp.tool(description=INBOX, annotations=READ_ONLY)
async def inbox(
    limit: Annotated[int, Field(ge=1, le=50, description="At most this many notes.")] = 10,
) -> dict[str, Any]:
    def call():
        client = _client(Scope.READ)
        captures = services.inbox(client.owner, limit=limit)
        context, held_back = services.inbox_context(client.owner)
        return {
            "waiting": services.inbox_count(client.owner),
            "captures": [_capture_dict(c) for c in captures],
            "current_entries": [entry_dict(e) for e in context],
            "current_entries_held_back": held_back,
        }

    return await _run(call)


@mcp.tool(description=CLOSE_CAPTURE, annotations=IDEMPOTENT)
async def close_capture(
    capture_id: Annotated[str, Field(description="The voice note's id, from inbox.")],
    status: Annotated[
        Literal["processed", "dismissed"],
        Field(description="processed: entries saved. dismissed: nothing worth keeping."),
    ],
) -> dict[str, Any]:
    def call():
        client = _client(Scope.WRITE)
        capture = services.close_capture(client.owner, capture_id, status)
        return {"id": str(capture.pk), "status": capture.status}

    return await _run(call)


@mcp.tool(description=FORGET, annotations=DESTRUCTIVE)
async def forget(
    entry_id: Annotated[str | None, Field(description="The entry to forget.")] = None,
    capture_id: Annotated[
        str | None, Field(description="A voice note to forget, with every entry made from it.")
    ] = None,
    scope: Annotated[
        Literal["entry", "source"],
        Field(description="source also forgets the voice note the entry came from."),
    ] = "entry",
    confirm: Annotated[
        bool, Field(description="false: preview only. true: delete, after the user agrees.")
    ] = False,
    confirm_token: Annotated[
        str | None, Field(description="From the preview. Required with confirm: true.")
    ] = None,
) -> dict[str, Any]:
    def call():
        client = _client(Scope.FORGET)
        if bool(entry_id) == bool(capture_id):
            raise ValidationError("Pass exactly one of entry_id or capture_id.")
        owner = client.owner
        plan = services.forget_plan(owner, entry_id, capture_id=capture_id, scope=scope)
        if not confirm:
            return {
                "would_delete": _plan_dict(plan),
                "note": plan.note,
                "confirm_token": services.forget_token(owner, plan),
                "say": "Show the user this list in plain words. Delete only if they agree.",
            }
        allowed = (confirm_token and services.forget_token_matches(owner, plan, confirm_token)) or (
            not confirm_token and services.is_simple_undo(plan)
        )
        if not allowed:
            raise ValidationError(
                "Not deleted. Call forget with confirm: false first, show the user what will be "
                "deleted, then call again with confirm: true and the confirm_token it returned. "
                "If the token is older than an hour or the plan has changed, preview again."
            )
        done = services.forget(owner, entry_id, capture_id=capture_id, scope=scope)
        return {"deleted": _plan_dict(done)}

    return await _run(call)


# --- HTTP ------------------------------------------------------------------------


def _transport_security() -> TransportSecuritySettings:
    """DNS-rebinding protection keyed to the hosts Django already trusts."""
    hosts = [h for h in settings.ALLOWED_HOSTS if h and h != "*"]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection="*" not in settings.ALLOWED_HOSTS,
        allowed_hosts=[*hosts, *(f"{h}:*" for h in hosts)],
        allowed_origins=[
            *settings.CSRF_TRUSTED_ORIGINS,
            *(f"{scheme}://{h}" for h in hosts for scheme in ("http", "https")),
            *(f"{scheme}://{h}:*" for h in hosts for scheme in ("http", "https")),
        ],
    )


class BearerAuth:
    """
    ASGI wrapper: every request to /mcp carries `Authorization: Bearer <token>`
    for a live Client, or gets a 401. The client is available to tools through
    `current_client`. OAuth (M8) replaces how the token is issued, not this check.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        scheme, _, token = headers.get(b"authorization", b"").decode("latin-1").partition(" ")
        client = None
        if scheme.lower() == "bearer" and token.strip():
            client = await sync_to_async(_authenticate)(token.strip())
        if client is None:
            return await _unauthorised(send)
        reset = current_client.set(client)
        try:
            await self.app(scope, receive, send)
        finally:
            current_client.reset(reset)


def _authenticate(token: str) -> Client | None:
    _fresh_connection()
    return services.authenticate(token)


async def _unauthorised(send):
    body = json.dumps(
        {"error": "invalid_token", "error_description": "Send a valid Memento bearer token."}
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b'Bearer realm="memento", error="invalid_token"'),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def http_app():
    """
    Streamable HTTP at /mcp, stateless and JSON: Render runs several workers, and
    a session held in one worker's memory would be lost on the next request.
    """
    return BearerAuth(
        mcp.streamable_http_app(
            streamable_http_path="/mcp",
            stateless_http=True,
            json_response=True,
            transport_security=_transport_security(),
        )
    )
