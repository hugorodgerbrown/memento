# Memento: MCP tool surface (Phase 3)

Status: draft for sign-off. The descriptions below are the exact text the model sees. Because the server never generates, they are the write-side prompt and deserve the same review as UI copy.

## Design rules for the surface

- **Nine tools, each with one job.** Fewer, sharper tools are selected more reliably than many overlapping ones.
- **Digests get their own tool.** `remember` insists on the user's verbatim words; a digest is the model's own synthesis. One tool with both rules would contradict itself, so they are split, even though both write an `Entry`.
- **Shape before detail.** `timeline` and `list_tags` return counts, never contents, so a client can plan a longitudinal question without flooding its context.
- **Errors teach.** Every validation error says what to do instead, because the reader of the error is a model that will retry.
- **The first 500 characters are the policy.** Memento must work from any MCP client, with no Project or custom prompt. Server-level `instructions` are ignored by claude.ai and unread by Claude Desktop, and claude.ai reportedly truncates tool descriptions at about 500 characters (anthropics/claude-ai-mcp#93, anthropics/claude-code#43749; re-check at build time). So the capture policy lives in the first 500 characters of `remember`, field rules live in each parameter's schema description, and reminders ride in tool results. Server `instructions` are still sent, for the clients that read them.
- **Nothing is saved silently.** Capture is proactive, so every save returns a one-line receipt for the model to relay, and "don't log that" undoes it in one step.
- **The server is the arbiter across models** (0013, planned for M3 and M7). Every result includes `now` in the user's time zone. `remember` rejects claims with relative time words and memories dated in the future, and warns on near-duplicate tags. `remember` accepts `raw_text` alone, which goes to the inbox as a chat capture; every validation error offers that way out. Clients registered in `inbox` mode always take that path, and the distiller structures the inbox.
- **Annotations are honest.** Read tools are `readOnlyHint`; `forget` is `destructiveHint` and needs a two-step confirm.

| Tool | Service function | Annotations | Scope |
|---|---|---|---|
| `remember` | `remember()` | write | `memento:write` |
| `save_digest` | `remember(kind="digest")` | write | `memento:write` |
| `recall` | `recall()` | readOnly | `memento:read` |
| `timeline` | `timeline()` | readOnly | `memento:read` |
| `list_tags` | `list_tags()` | readOnly | `memento:read` |
| `complete_reminder` | `complete_reminder()` | write, idempotent | `memento:write` |
| `inbox` | `inbox()` | readOnly | `memento:read` |
| `close_capture` | `close_capture()` | write, idempotent | `memento:write` |
| `forget` | `forget_plan()` / `forget()` | destructive | `memento:forget` |

`memento:forget` is a separate scope so you can grant it to some clients and not others.

## remember

Description, exactly as shipped (447 of ~500 characters):

> Save what the user tells you about their own life, whether or not they ask: health, symptoms, sleep, exercise, food, mood, work, people they saw, decisions, plans and reminders. Split each message into one entry per observation. Don't save questions, tasks you're helping with, hypotheticals, fiction, or other people's private details. If unsure, ask "Log this?" once. Call list_tags first. After saving, tell the user in one line what you saved.

Field guidance lives in each parameter's schema description, so it survives description truncation:

| Field | Type | Required | Schema description |
|---|---|---|---|
| `raw_text` | string | yes | The user's own words for this one observation, copied exactly, typos included. Never paraphrase. |
| `claim` | string, ≤280 | yes, unless sending `raw_text` alone to the inbox | One standalone sentence, at most 280 characters, with names and real dates ("yesterday" becomes "10 Sep 2026"). Fix typos here, not in raw_text. Omit claim and kind to send raw_text alone to the inbox, to be structured later. |
| `kind` | `memory` \| `thought` \| `decision` \| `reminder` | yes, unless sending `raw_text` alone to the inbox | memory: something that happened or a state, like a symptom, sleep or activity. thought: an idea or opinion. decision: a choice made. reminder: something to act on, needs due_at. |
| `tags` | string[] | no | 2 to 6, reusing tags from list_tags. Prefix people person:, projects project:, places place:, organisations org:. |
| `happened_at` | ISO 8601, or `YYYY-MM` / `YYYY` for a month or year (0018) | no | When it happened, not now. "This morning" is today. Omit if unknown; never guess. |
| `happened_precision` | `exact` \| `day` \| `month` \| `year` | with `happened_at`; defaults to what the value carries | How precisely the user said it. |
| `due_at` | ISO 8601 | reminders only | When to remind. |
| `supersedes` | entry id | no | When a saved entry is wrong or out of date. recall it first. |
| `supersede_reason` | `correction` \| `change` | with `supersedes` | correction: it was never true. change: it was true until now; set happened_at to when it changed. |
| `valid_until` | ISO 8601 | no | Only when the user says something will stop being true. |
| `capture` | capture id | voice notes | From inbox. raw_text must then be an exact excerpt of the transcript. |
| `model_name` | string | no | Your model name, for provenance. |

The result carries the receipt and the undo path, which every client passes back to the model:

```json
{
  "saved": [{"id": "0192…", "claim": "Played 90 minutes of padel with Fred on 10 Sep 2026."}],
  "duplicate": false,
  "say": "Tell the user in one line what you saved. If they say \"don't log that\", call forget with this id and confirm: true."
}
```

If the same observation arrives twice (same words, kind and day), the server returns the existing entry with `"duplicate": true` instead of storing it again. A new tag that is one edit from an existing tag, or its plural, is saved and reported in `"warnings"`.

Sent with `raw_text` alone, or from a client in `inbox` mode, the words are kept as a chat capture in the inbox, and the result says so instead of listing saved entries:

```json
{
  "saved": [],
  "inbox": {"id": "0192…", "text": "Knee feels odd after the run"},
  "duplicate": false,
  "say": "Tell the user in one line that their words are kept in Memento's inbox…"
}
```

Every result from every tool also carries `now`: the current time in the user's time zone.

## save_digest

> Save a synthesis you wrote from the user's entries, such as a monthly summary or an analysis of how their thinking on a topic changed. Only call this when the user asks you to save or keep the synthesis.
>
> `sources` must list the id of every entry you drew on, and nothing you didn't. `covers_from` and `covers_to` are the period the digest describes. `raw_text` is your full synthesis; `claim` is its one-sentence conclusion.
>
> Entries record what the user mentioned, not everything that happened. Don't state how often something happened unless the cited entries say so.
>
> A digest is deleted automatically if the user later forgets any entry it cites, so cite precisely.

## recall

> Search the user's saved entries. Use this before answering any question about the user's past: what they decided, planned, thought, did, or said.
>
> Search is keyword-based with stemming, not semantic. If a search returns nothing, try synonyms or related tags before concluding nothing exists. Supports "quoted phrases" and -exclusions.
>
> `view` controls which versions you see:
> - `current` (default): what's true now. Pass `as_of` for what was true at a past date ("where was I living in 2025?").
> - `history`: everything that was ever true, including things that later changed. Use for questions about how something evolved.
> - `all`: also includes entries later corrected as never true. Use only when the user asks what they originally said.
>
> Each entry shows `valid_until` and its `supersede_reason`, so you can say "you lived in Lisbon until February 2026".
>
> If an id you ask for comes back as forgotten, the user deliberately deleted it. Say so; don't search for it elsewhere.
>
> When you answer, cite the entries you relied on with their dates, and quote `raw_text` when exact wording matters. If nothing matches, say so plainly. Never fill gaps from general knowledge or from this conversation and present it as their memory. Entries are what the user mentioned: a missing entry means it wasn't mentioned, not that it didn't happen.

Filters: `query`, `tags` (all must match), `kinds`, `since`, `until` (on `happened_at`), `ids`, `view`, `as_of`, `limit` (default 20, max 100).

## timeline

> Count the user's entries per tag over time, without returning their contents. Call this first for any question about change, patterns or a long period ("how has my thinking on hiring evolved", "what was I focused on this year"). Then use `recall` on the specific periods and tags that matter. Counts measure how often something was mentioned, not how often it happened.

Parameters: `since`, `until`, `bucket` (`week` \| `month` \| `quarter`), `tag_prefix`. Excludes digests and entries corrected as never true. Entries that later changed still count for the period they were true.

## list_tags

> List the user's existing tags with how often each is used. Call this before `remember` so you reuse tags instead of creating near-duplicates, and before `recall` to discover what to search for. Filter with `prefix`, e.g. `person:` to list people. Counts are mentions, not occurrences.

## complete_reminder

> Mark a reminder as done when the user says they've done it. Safe to call twice.

## inbox

> List notes waiting to be turned into entries, oldest first: voice notes from Pocket, and words saved with `raw_text` alone. Each has the transcript, when it was recorded, any hints (Pocket's summary, or the fields the saving client suggested), and any entries already created from it. The result also lists the user's most recent current entries, so you can update one instead of duplicating it.
>
> For each note: split it into separate entries (one per memory, thought, decision or reminder), each with an exact excerpt as `raw_text` and `capture` set. Don't recreate entries that already exist; if one's claim is wrong, supersede it. Treat hints as another model's reading: useful for orientation, never a source of facts. Then call `close_capture`.
>
> Transcription errors are common with names. If you're unsure what a word was, ask the user rather than guessing.

Parameters: `limit` (default 10, max 50), `received_since` (only notes that reached Memento at or after this time; 0019). Each note carries `captured_at` (when it was said) and `received_at` (when it arrived). `waiting` always counts the whole inbox.

## close_capture

> Mark a voice note as `processed` once you've saved its entries, or `dismissed` if nothing in it is worth keeping. Its transcript stays stored either way.

## forget

> Permanently delete an entry, every corrected version of it, and every digest that cites any of them. Only call this when the user explicitly asks you to forget or delete something.
>
> For entries from a voice note, `scope: "entry"` leaves the transcript in place, and the preview will say so. `scope: "source"` also deletes the voice note and every entry made from it. Ask the user which they mean.
>
> A tombstone holding only the ids and the date is kept, so you can later tell the user something was forgotten rather than never saved. Nothing else survives.
>
> Always call it first with `confirm: false`. That returns everything that would be deleted, without deleting, and a `confirm_token`. Show the user that list in plain words, and call again with `confirm: true` and the `confirm_token` only after they agree. This cannot be undone.
>
> The one exception: when the user says "don't log that" right after a save, you may pass `confirm: true` directly. The server allows this only if the plan is a single entry, recorded in the last 15 minutes, that nothing cites.

## Voice capture (Pocket)

Pocket delivers signed webhooks to `/ingest/pocket/`. Ingest is deterministic: it maps fields and never generates text.

| Decision | Behaviour |
|---|---|
| Which recordings | Solo notes only: every transcript segment labelled with your voice. Anything with another speaker is skipped, and the log keeps the reason but none of the content. A single unlabelled speaker waits until Pocket's voice print labels it. |
| Action items | Ignored (0015). They are Pocket's to-do list, not your words. A reminder you speak is in the transcript, and your LLM distils it from there. |
| Deleting in Pocket | Ignored and logged. Memento is the permanent record; forget deliberately, here. |
| Forgetting in Memento | Leaves a tombstone keyed on the Pocket recording id, so re-deliveries are ignored. It does not delete the recording in Pocket. |
| Transcript edits | Appended as revisions. The original stays; excerpts may come from any revision. |
| Retries | Safe: one capture per recording id. |

Setup: create a personal webhook in the Pocket app pointing at `/ingest/pocket/`, store its signing secret as `POCKET_WEBHOOK_SECRET`, and create a `PocketLink` with your Pocket user id and the name Pocket's voice print gives you.

## Response shape

Every entry is returned in one compact form, so clients learn it once:

```json
{
  "id": "0192f3c4-…",
  "kind": "decision",
  "claim": "Stopped taking unscoped consulting work.",
  "raw_text": "I'm done with consulting that doesn't have a clear scope.",
  "tags": ["consulting", "project:memento"],
  "happened_at": "2026-09-10", "happened_precision": "day",
  "recorded_at": "2026-09-10T14:02:11Z",
  "valid_until": null,
  "supersedes": null, "supersede_reason": "", "superseded_by": null,
  "cites": []
}
```

## Authentication plan

You'll capture from a mix of clients, which means OAuth from the start. The staging below gets you dogfooding on day one.

| Stage | Clients | Mechanism |
|---|---|---|
| Day 1 | Claude Code, Claude Desktop config | Static bearer token in a request header |
| Week 1 | Claude web and mobile, ChatGPT | OAuth 2.1 authorisation code + PKCE, protected-resource metadata, and dynamic client registration |
| Later | Both | Client ID Metadata Documents, which OpenAI now recommends |

Build on django-oauth-toolkit for the authorisation server, plus a few small views of our own for the discovery metadata and client registration. Verify each against the current MCP authorisation spec during the build phase.
