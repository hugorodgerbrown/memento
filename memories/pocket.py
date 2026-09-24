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
"""

import hashlib
import hmac
import time

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import services
from .models import Capture, IngestLog, PocketLink

REPLAY_WINDOW_SECONDS = 300
TRANSCRIPT_EVENTS = {
    "transcription.completed",
    "summary.completed",
    "summary.regenerated",
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


def _latest_summary(payload: dict) -> dict:
    rows = [
        r
        for r in (payload.get("summarizations") or {}).values()
        if r.get("processingStatus") == "completed"
    ]
    rows.sort(key=lambda r: r.get("updatedAt") or "")
    return rows[-1] if rows else {}


def _hints(payload: dict, summary: dict) -> dict:
    v2 = summary.get("v2") or {}
    return {
        "summary_markdown": (v2.get("summary") or {}).get("markdown", ""),
        "bullet_points": (v2.get("summary") or {}).get("bulletPoints", []),
        "model": (summary.get("settings") or {}).get("modelId", ""),
    }


def _solo_verdict(segments: list[dict], my_label: str) -> tuple[bool, str]:
    speakers = {(seg.get("speaker") or "").strip() for seg in segments}
    speakers.discard("")
    if not segments or not "".join(seg.get("text", "") for seg in segments).strip():
        return False, "empty transcript"
    if len(speakers) != 1:
        return False, f"{len(speakers)} speakers"
    (only,) = speakers
    if only.casefold() != my_label.casefold():
        return False, "single speaker not labelled as you yet"
    return True, "solo, your voice"


def _log(event, external_id, decision, reason=""):
    IngestLog.objects.create(
        event=event, external_id=external_id or "", decision=decision, reason=reason[:255]
    )
    return decision


# --- ingest ------------------------------------------------------------------


@transaction.atomic
def ingest(payload: dict) -> str:
    event = payload.get("event", "")
    recording = payload.get("recording") or {}
    rec_id = recording.get("id", "")

    link = (
        PocketLink.objects.filter(pocket_user_id=(payload.get("user") or {}).get("id", ""))
        .select_related("owner")
        .first()
    )
    if not link:
        return _log(event, rec_id, Decision.REJECTED, "unknown Pocket user")

    if event == "recording.deleted":
        return _log(event, rec_id, Decision.IGNORED, "kept: Memento is the permanent record")

    capture = Capture.objects.filter(owner=link.owner, source="pocket", external_id=rec_id).first()

    if event == "transcript.edited":
        return _record_edit(event, capture, payload, rec_id)

    if event not in TRANSCRIPT_EVENTS:
        return _log(event, rec_id, Decision.IGNORED, "event not used")

    segments = payload.get("transcript") or []
    summary = _latest_summary(payload)

    if capture:
        # Hints are derived and may be refreshed. The transcript never changes.
        capture.hints = _hints(payload, summary) if summary else capture.hints
        capture.title = (recording.get("title") or capture.title)[:255]
        capture.save(update_fields=["hints", "title"])
        return _log(event, rec_id, Decision.DUPLICATE, "already stored")

    if services.is_forgotten_ref(link.owner, f"pocket:{rec_id}"):
        return _log(event, rec_id, Decision.IGNORED, "forgotten in Memento; not re-stored")

    solo, reason = _solo_verdict(segments, link.speaker_label)
    if not solo:
        return _log(event, rec_id, Decision.SKIPPED, reason)

    try:
        with transaction.atomic():
            capture = Capture.objects.create(
                owner=link.owner,
                external_id=rec_id,
                segments=segments,
                text=" ".join(seg["text"].strip() for seg in segments if seg.get("text")),
                captured_at=parse_datetime(recording.get("createdAt") or "") or timezone.now(),
                title=(recording.get("title") or "")[:255],
                hints=_hints(payload, summary),
                duration_seconds=recording.get("duration"),
                language=recording.get("language") or "",
            )
    except IntegrityError:  # a concurrent retry won the race
        return _log(event, rec_id, Decision.DUPLICATE, "concurrent delivery")

    return _log(event, rec_id, Decision.STORED, reason)


def _record_edit(event, capture, payload, rec_id) -> str:
    if not capture:
        return _log(event, rec_id, Decision.IGNORED, "no stored capture")
    text = " ".join(
        seg["text"].strip() for seg in payload.get("transcript") or [] if seg.get("text")
    )
    if not text or text == capture.texts()[-1]:
        return _log(event, rec_id, Decision.IGNORED, "no text change")
    capture.revisions = [*capture.revisions, {"at": timezone.now().isoformat(), "text": text}]
    capture.save(update_fields=["revisions"])
    return _log(event, rec_id, Decision.UPDATED, f"revision {len(capture.revisions)} appended")
