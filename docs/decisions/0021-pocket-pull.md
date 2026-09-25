# 0021. Pocket by pull, while Memento lives on one Mac

Status: accepted, September 2026. Follows from 0020; revisits the transport in 0006, not its rules. **Temporary:** it lasts only as long as 0020.

## Context

0006 brings Pocket in through a signed webhook. On one Mac (0020) nothing on the internet can reach Memento, so the webhook can't be delivered. `docs/first-recordings.md` left "push or pull" open for M4. Pocket has a REST API with personal keys (`pk_...`), which a scheduled job can call without OAuth. It lists recordings by `start_date` and returns each one with its transcript, speaker labels and summaries.

## Decision

**A scheduled pull, through the webhook's rules.** `manage.py pocket_pull` (`make pocket-pull`, run by launchd every 15 minutes) lists the recordings made in the last 36 hours and fetches each one in full. `pocket.ingest` and `pocket.pull` share one store path, so every rule holds for both: solo recordings in your voice only, skips that keep no content, a tombstone that stops a forgotten recording coming back, one capture per recording id, and transcript edits appended as revisions. Action items are still ignored (0015).

**Stateless and quiet.** No watermark is stored. Each run looks back 36 hours, long enough to cover a night asleep. A recording already stored and unchanged is passed over without a log row, and a skipped one is logged once, so polling every 15 minutes doesn't grow the log. `--since` reaches further back by hand, for example to bring in the recordings made before the pull existed.

**When it was said.** `captured_at` is Pocket's `recording_at`, falling back to `created_at`. The webhook uses `createdAt`, which is all it carries.

**Dry run first.** `--dry-run` reports what would be stored, skipped or updated, and stores nothing. Pocket's REST documentation couldn't be read from where this was built; the field names come from a third-party SDK that tracks it. So the first run against the real account is a dry run, and its output is checked before anything is stored.

**The key is Pocket's, not a model's.** `POCKET_API_KEY` sits in `.env` with the rest of the server's settings. It reads the owner's recordings and generates nothing, so Principle 2 is untouched.

**Push is the mechanism once a server is reachable.** The pull exists only because nothing can reach a Mac. When Memento is deployed on a server Pocket can reach (0020 ends), Pocket delivers by signed webhook, as 0006 decided, and the pull is retired: its launchd job is removed and `pocket_pull` is kept only for backfills (`--since`). Push is better on every count that matters here. It arrives in seconds, not up to 15 minutes. It sees every event, including edits made at any age and deletions (logged, per 0008). It needs no stored API key and no polling. And it spends nothing on Pocket's rate limits.

## Consequences

- Voice notes arrive within 15 minutes while the Mac is awake, and on waking otherwise. The distiller sees them because its window is on `received_at` (0019).
- Edits made more than 36 hours after recording aren't seen unless pulled with `--since`.
- Deleting in Pocket still doesn't delete here (0008). A pull never sees deletions at all.
- The webhook stays, tested and unused, for when deployment resumes, and it becomes the only live path then. Running both during a switchover is harmless, because a recording id stores once.
- `start_date` is a day (`YYYY-MM-DD`): the first dry run got a 400 for a full time. The pull asks from the UTC day its window starts on, then drops what Pocket created (`created_at`) before the window itself, so the cutoff is still the time asked for.
- Confirmed against the owner's account (25 Sep 2026): the envelope (`success`, `data`, `pagination.has_more`), `recording_at`, a transcript of `{metadata, segments, text}` with a `speaker` on each segment, and `summarizations` keyed by id. `REAL_DETAIL` in the tests has that shape.
- Still unconfirmed: whether `updated_at` changes on transcript edits, and the rate limits (M4's "verify before relying" items).
