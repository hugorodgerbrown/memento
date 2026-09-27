"""
Pocket webhook ingest. Deterministic: it maps fields, it never generates text.

Decisions (Phase 3 addendum):
- Only solo recordings in your own voice are stored. Anything with another
  speaker is skipped, and the log records why without keeping any content.
- Pocket's action items are ignored, dated or not. They are Pocket's to-do
  list, written by its model; Memento keeps your words for recall across time
  (0015). A reminder you speak is in the transcript, and your LLM distils it
  from there.
- Deleting a recording in Pocket does not delete it here. Memento is the
  permanent record; forgetting happens in Memento, deliberately, and leaves a
  tombstone so Pocket can't re-deliver what you forgot.

Two ways in, one set of rules: Pocket's signed webhook, and, while Memento runs
on one Mac that Pocket can't reach, a scheduled pull from Pocket's REST API
(0020, 0021). Both store through `_store`, so the rules above hold for both.
"""

import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import nullcontext
from datetime import UTC, datetime

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import services
from .models import Capture, IngestLog, PocketLink

logger = logging.getLogger(__name__)
# Pocket's field names, as the pull and the webhook have shown them (0021, M4).
KNOWN_FIELDS = frozenset(
    [
        "id",
        "title",
        "text",
        "speaker",
        "start",
        "end",
        "segments",
        "metadata",
        "duration",
        "language",
        "createdAt",
        "recordingAt",
        "updatedAt",
        "created_at",
        "updated_at",
        "recording_at",
        "processingStatus",
        "settings",
        "modelId",
        "v2",
        "summary",
        "markdown",
        "bulletPoints",
        "actionItems",
        "summarizations",
        "transcript",
    ]
)

REPLAY_WINDOW_SECONDS = 300
TRANSCRIPT_EVENTS = {
    "transcription.completed",
    "summary.completed",
    "summary.regenerated",
    "summary.updated",  # seen in the first real deliveries (27 Sep 2026)
    "speakers.labeled",
}

Decision = IngestLog.Decision


# --- signature ---------------------------------------------------------------


def verify_signature(
    secret: str, timestamp_ms: str, raw_body: bytes, signature: str, now: float | None = None
) -> bool:
    """HMAC-SHA256 over "{timestamp}.{raw body}", rejecting stale deliveries."""
    if not (secret and timestamp_ms and signature):
        return False
    try:
        age = (now or time.time()) - int(timestamp_ms) / 1000
    except ValueError:
        return False
    if abs(age) > REPLAY_WINDOW_SECONDS:
        return False
    expected = hmac.new(
        secret.encode(), f"{timestamp_ms}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))


# --- payload helpers ---------------------------------------------------------


def _summary_body(summary: dict) -> dict:
    # The REST pull nests it under `v2`; the webhook puts it at the top (M4).
    return (summary.get("v2") or summary).get("summary") or {}


def _latest_summary(payload: dict) -> dict:
    """
    Pocket's newest finished summary. The REST pull keys summaries by id and marks
    them with processingStatus; the webhook sends a list, without a status, and a
    summary that isn't written yet has no body. Either way, one still processing
    isn't used.
    """
    rows = payload.get("summarizations") or []
    rows = rows.values() if isinstance(rows, dict) else rows
    rows = [
        r
        for r in rows
        if isinstance(r, dict)
        and r.get("processingStatus", "completed") == "completed"
        and _summary_body(r).get("markdown")
    ]
    rows.sort(key=lambda r: r.get("updatedAt") or r.get("createdAt") or "")
    return rows[-1] if rows else {}


def _hints(payload: dict, summary: dict) -> dict:
    body = _summary_body(summary)
    return {
        "summary_markdown": body.get("markdown", ""),
        "bullet_points": body.get("bulletPoints", []),
        "model": (summary.get("settings") or {}).get("modelId", ""),
    }


def _solo_verdict(segments: list[dict], link: PocketLink) -> tuple[bool, str]:
    speakers = {(seg.get("speaker") or "").strip() for seg in segments}
    speakers.discard("")
    if not segments or not "".join(seg.get("text", "") for seg in segments).strip():
        return False, "empty transcript"
    if not speakers:
        return False, "no speaker labels"
    if len(speakers) != 1:
        return False, f"{len(speakers)} speakers"
    (only,) = speakers
    if only.casefold() == link.speaker_label.casefold():
        return True, "solo, your voice"
    if link.one_speaker_is_me:
        return True, "solo, one speaker (0022)"
    return False, "one speaker, not labelled as you; to keep these, tick 'one speaker is me'"


def _shape(value, depth=0, schema=True):
    """
    What a delivery looks like, with none of its content: keys and types only. Pocket's
    webhook shape is unconfirmed (M4), so each delivery logs this, and the logs can
    show what really arrives without keeping a word anyone said.

    The envelope's keys and the recording's are Pocket's schema, so they are shown.
    Deeper down, a key is shown only if it is a known Pocket field: anything else
    could be an id or a name keying a map (a speaker, say), so it is masked.
    """
    if isinstance(value, dict):
        if depth >= 4:
            return "{...}"
        shown, masked = {}, []
        for k, v in value.items():
            if schema or k in KNOWN_FIELDS:
                shown[k] = _shape(v, depth + 1, schema=depth == 0 and k == "recording")
            else:
                masked.append(v)
        if masked:
            shown[f"<{len(masked)} other keys>"] = _shape(masked[0], depth + 1, schema=False)
        return shown
    if isinstance(value, list):
        return [_shape(value[0], depth + 1, schema=False), len(value)] if value else []
    return type(value).__name__


def _log(event, external_id, decision, reason=""):
    IngestLog.objects.create(
        event=event, external_id=external_id or "", decision=decision, reason=reason[:255]
    )
    return decision


# --- ingest ------------------------------------------------------------------


def _link_for(pocket_user_id: str) -> PocketLink | None:
    """
    Memento has one user (0026), so the only Pocket link takes every signed
    delivery, whatever user id Pocket sends. The id is kept on the link when it was
    left blank, in case it is needed later. With two or more links, only an exact
    id match will do, so no one's recordings reach someone else.
    """
    links = PocketLink.objects.select_related("owner")
    if pocket_user_id and (link := links.filter(pocket_user_id=pocket_user_id).first()):
        return link
    if links.count() != 1:
        return None
    link = links.get()
    if pocket_user_id and not link.pocket_user_id:
        link.pocket_user_id = pocket_user_id[:128]
        link.save(update_fields=["pocket_user_id"])
    return link


@transaction.atomic
def ingest(payload: dict) -> str:
    event = payload.get("event", "")
    recording = payload.get("recording") or {}
    rec_id = recording.get("id", "")
    logger.info("Pocket delivery %s %s: %s", event, rec_id, json.dumps(_shape(payload)))

    link = _link_for((payload.get("user") or {}).get("id") or "")
    if not link:
        return _log(
            event, rec_id, Decision.REJECTED, "no Pocket link matches; add one in the admin"
        )

    if event == "recording.deleted":
        return _log(event, rec_id, Decision.IGNORED, "kept: Memento is the permanent record")

    capture = Capture.objects.filter(owner=link.owner, source="pocket", external_id=rec_id).first()

    if event == "transcript.edited":
        return _record_edit(event, link, capture, payload, rec_id)

    if event not in TRANSCRIPT_EVENTS:
        return _log(event, rec_id, Decision.IGNORED, "event not used")

    segments = _segments(payload) or _segments(recording)
    summary = _latest_summary(payload)

    if capture:
        # Hints are derived and may be refreshed. The transcript never changes.
        capture.hints = _hints(payload, summary) if summary else capture.hints
        capture.title = (recording.get("title") or capture.title)[:255]
        capture.save(update_fields=["hints", "title"])
        return _log(event, rec_id, Decision.DUPLICATE, "already stored")

    if services.is_forgotten_ref(link.owner, f"pocket:{rec_id}"):
        return _log(event, rec_id, Decision.IGNORED, "forgotten in Memento; not re-stored")

    solo, reason = _solo_verdict(segments, link)
    if not solo:
        return _log(event, rec_id, Decision.SKIPPED, reason)

    # When it was said (0021): recordingAt, not createdAt, which is when Pocket filed it.
    spoken = recording.get("recordingAt") or recording.get("createdAt") or ""
    return _store(
        link, event, rec_id, segments,
        captured_at=parse_datetime(spoken),
        title=recording.get("title"), hints=_hints(payload, summary),
        duration=recording.get("duration"), language=recording.get("language"),
        reason=reason,
    )  # fmt: skip


def _store(link, event, rec_id, segments, *, captured_at, title, hints, duration, language, reason):
    try:
        with transaction.atomic():
            Capture.objects.create(
                owner=link.owner,
                external_id=rec_id,
                segments=segments,
                text=_text(segments),
                captured_at=captured_at or timezone.now(),
                title=(title or "")[:255],
                hints=hints,
                duration_seconds=duration,
                language=language or "",
            )
    except IntegrityError:  # a concurrent retry won the race
        return _log(event, rec_id, Decision.DUPLICATE, "concurrent delivery")
    return _log(event, rec_id, Decision.STORED, reason)


def _text(segments: list[dict]) -> str:
    return " ".join(seg["text"].strip() for seg in segments if seg.get("text"))


def _record_edit(event, link, capture, payload, rec_id) -> str:
    if not capture:
        return _log(event, rec_id, Decision.IGNORED, "no stored capture")
    segments = _segments(payload) or _segments(payload.get("recording") or {})
    text = _text(segments)
    if not text or text == capture.texts()[-1]:
        return _log(event, rec_id, Decision.IGNORED, "no text change")
    return _append_revision(event, link, capture, segments, rec_id) or Decision.SKIPPED


def _append_revision(event, link, capture, segments, rec_id) -> str | None:
    """
    A later version of a stored recording is kept only if it is still solo in your
    voice (Principle 8): an edit or relabel can't bring someone else's words in.
    The refusal is logged once, without content.
    """
    solo, reason = _solo_verdict(segments, link)
    if not solo:
        reason = f"revision not kept: {reason}"
        seen = IngestLog.objects.filter(
            external_id=rec_id, decision=Decision.SKIPPED, reason=reason
        )
        return None if seen.exists() else _log(event, rec_id, Decision.SKIPPED, reason)
    text = _text(segments)
    capture.revisions = [*capture.revisions, {"at": timezone.now().isoformat(), "text": text}]
    capture.save(update_fields=["revisions"])
    return _log(event, rec_id, Decision.UPDATED, f"revision {len(capture.revisions)} appended")


# --- the pull (0021) -------------------------------------------------------------

PULL = "pull"


class PocketAPIError(Exception):
    HELP = {
        401: "Pocket rejected POCKET_API_KEY. Create a key in Pocket > Settings > Developer > "
        "API Keys and put it in .env.",
        403: "Pocket refused access. Check the key belongs to your account and your plan "
        "includes the API.",
        429: "Pocket is rate-limiting the pull. It will try again on the next run.",
    }

    def __init__(self, status: int, detail: str = ""):
        self.status = status
        super().__init__(self.HELP.get(status, f"Pocket's API answered {status}. {detail}".strip()))


class PocketAPI:
    """Pocket's REST API, read-only, with a personal key (pk_...)."""

    def __init__(self, key: str, base_url: str, timeout: int = 30):
        self.key, self.base_url, self.timeout = key, base_url.rstrip("/"), timeout

    def get(self, path: str, params: dict | None = None) -> dict:
        url = self.base_url + path + ("?" + urllib.parse.urlencode(params) if params else "")
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.key}"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            # Pocket's own words say which parameter it refused; keep them.
            body = e.read().decode("utf-8", "replace").strip()[:300]
            raise PocketAPIError(e.code, f"{e.reason}: {body}" if body else e.reason) from e


def _unwrap(body: dict):
    """Pocket wraps results in `data`; tolerate a bare object or list too."""
    return body.get("data", body) if isinstance(body, dict) else body


def _recording_ids(api, since: datetime):
    # Pocket filters by the day, as YYYY-MM-DD, and answers 400 to a full time.
    # The day is taken in UTC, which starts no later than `since`; `pull` drops the
    # part of the day before it.
    start_date = since.astimezone(UTC).date().isoformat()
    page = 1
    while True:
        body = api.get("/recordings", {"start_date": start_date, "page": page, "limit": 100})
        rows = _unwrap(body)
        if isinstance(rows, dict):
            rows = rows.get("recordings", [])
        yield from (row["id"] for row in rows)
        pagination = (body.get("pagination") or body) if isinstance(body, dict) else {}
        if not rows or not pagination.get("has_more"):
            return
        page += 1


def pull(link: PocketLink, api, *, since: datetime, dry_run: bool = False) -> list[tuple[str, str]]:
    """
    Ingest Pocket recordings made since `since`, by the webhook's rules. Returns
    (id, decision, reason) for each recording that changed anything; repeats return
    nothing and log nothing, so pulling every 15 minutes over a long window stays quiet.
    A dry run rolls the log back, so the reason is read before it goes.
    """
    results = []
    with transaction.atomic() if dry_run else nullcontext():
        for rec_id in _recording_ids(api, since):
            params = {"include_transcript": "true", "include_summarizations": "true"}
            recording = _unwrap(api.get(f"/recordings/{rec_id}", params))
            if _before(recording, since):
                continue
            with transaction.atomic():
                if decision := _pull_one(link, recording):
                    results.append((rec_id, decision, _last_reason(rec_id)))
        if dry_run:
            transaction.set_rollback(True)
    return results


def _last_reason(rec_id: str) -> str:
    log = IngestLog.objects.filter(event=PULL, external_id=rec_id).order_by("-received_at", "-pk")
    return log.values_list("reason", flat=True).first() or ""


def _before(recording: dict, since: datetime) -> bool:
    """
    Pocket was asked for the whole day, so drop what it created before `since`: on
    the axis Pocket filters by, `created_at`. Without a readable time, keep it.
    """
    stamp = recording.get("created_at") or recording.get("recording_at") or ""
    created = parse_datetime(stamp)
    return created is not None and created < since


def _refresh(capture: Capture, recording: dict) -> None:
    """Hints and title are derived, and Pocket may finish them later. The transcript doesn't."""
    summary = _latest_summary(recording)
    hints = _hints(recording, summary) if summary else capture.hints
    title = (recording.get("title") or capture.title)[:255]
    if (hints, title) != (capture.hints, capture.title):
        capture.hints, capture.title = hints, title
        capture.save(update_fields=["hints", "title"])


def _segments(recording: dict) -> list[dict]:
    """
    A transcript comes as {text, segments}, a bare list of segments, or plain text (as
    pocket-laravel reads it), by pull or by webhook. Plain text has no speaker labels, so the
    solo-voice rule skips it: without labels there's no telling whose words they are.
    """
    transcript = recording.get("transcript") or recording.get("raw_transcript") or []
    if isinstance(transcript, dict):
        text = transcript.get("text") or ""
        transcript = transcript.get("segments") or ([{"text": text}] if text.strip() else [])
    elif isinstance(transcript, str):
        transcript = [{"text": transcript}] if transcript.strip() else []
    return [
        {k: seg.get(k) for k in ("speaker", "text", "start", "end")}
        for seg in transcript
        if isinstance(seg, dict)
    ]


def _pull_one(link: PocketLink, recording: dict) -> str | None:
    rec_id = recording["id"]
    segments = _segments(recording)
    capture = Capture.objects.filter(owner=link.owner, source="pocket", external_id=rec_id).first()
    if capture:
        _refresh(capture, recording)
        text = _text(segments)
        if not text or text == capture.texts()[-1]:
            return None
        return _append_revision(PULL, link, capture, segments, rec_id)
    if services.is_forgotten_ref(link.owner, f"pocket:{rec_id}"):
        return None
    solo, reason = _solo_verdict(segments, link)
    if not solo:
        seen = IngestLog.objects.filter(
            event=PULL, external_id=rec_id, decision=Decision.SKIPPED, reason=reason
        )
        return None if seen.exists() else _log(PULL, rec_id, Decision.SKIPPED, reason)
    spoken = recording.get("recording_at") or recording.get("created_at") or ""
    return _store(
        link, PULL, rec_id, segments,
        captured_at=parse_datetime(spoken),
        title=recording.get("title"), hints=_hints(recording, _latest_summary(recording)),
        duration=recording.get("duration"), language=recording.get("language"),
        reason=reason,
    )  # fmt: skip
