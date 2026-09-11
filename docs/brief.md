# Memento: Phase 1–2 brief

Status: revised 11 September 2026. Capture is now proactive.

## Thesis

Memento is durable, deliberately simple storage for a person's memories, thoughts and reminders, reached through MCP. All interpretation happens in the calling LLM, before storage and after retrieval. The store outlives every model: an entry written by today's model will be read by a better one in five years.

## Locked decisions

| Decision | Choice | What it forces |
|---|---|---|
| First user | Me, dogfooding | Optimise for one person's daily capture habit. Skip onboarding and billing, but keep a `user` foreign key everywhere so multi-tenancy costs nothing later. |
| Where the LLM runs | The calling client | The server never generates text. The MCP tool descriptions and required fields become the write-side prompt, so they are product surface, not plumbing. |
| Privacy | Encrypted at rest, server can search | Managed Postgres disk encryption, TLS, and per-token scopes. Field-level encryption is out, because it breaks full-text search. I, as operator, can read the data. That's acceptable for dogfooding and must be stated plainly before anyone else uses it. |

## Principles

1. **Raw is sacred.** The user's words are stored verbatim and never rewritten. Everything the model adds sits beside them.
2. **The server never generates.** It validates, stores, searches, counts and schedules. No summaries, no inference.
3. **Every answer cites.** Anything derived, including digests, carries the ids of the raw entries it came from.
4. **Corrections append.** A correction is a new entry that supersedes an old one, so the history stays auditable.
5. **Forgetting is a right.** The one exception to append-only: hard delete, including derived entries that cite the deleted one.
6. **Time has two axes.** `happened_at` and `recorded_at` are different, and longitudinal analysis depends on the first.
7. **Tool descriptions are the product.** The quality of capture depends on how well the tool schema teaches the client what to extract. The first 500 characters of `remember` are the capture policy for every client.
8. **Your words, not other people's.** Memento stores what you say about your life. Other people's speech and private details stay out.
9. **Nothing is saved silently.** Capture is proactive, so every save gets a one-line receipt and a one-step undo.

## Jobs to be done

| Job | Example prompt | Success looks like |
|---|---|---|
| Log | "Woke up and tendonitis is bad. Played padel yesterday for 90 minutes with Fred. Good sleep." | Saved without being asked, from any MCP client, split into one entry per observation, with a one-line receipt |
| Capture | "Remember I decided to stop taking unscoped consulting work." | One sentence, from any client, stored with sensible tags and date |
| Recall | "What did I decide about consulting, and when?" | Correct answer with the source entry quoted |
| Reflect | "How has my thinking on hiring changed since spring?" | A narrative grounded in cited entries, saved as a digest |
| Be reminded | "Remind me to renew the domain in March." | An email arrives in March without my asking |

## Out of scope for v1

Teams and sharing, billing, end-to-end encryption, any server-side model, native mobile apps, bulk import from other tools.

## Risks

| Risk | Mitigation |
|---|---|
| Client distils badly or inconsistently across models | Strict required fields, enum types, examples in tool descriptions, raw text always kept |
| Over-capture turns the store into noise | Exclusions in the capture policy, a receipt for every save, one-step undo, a same-observation duplicate guard, and an eval set run against each client |
| Different models parse the same message differently | A strict server contract with teaching errors, the Memento skill, per-client evals and modes, and an inbox fallback to one distiller (0013) |
| Under-capture: a client ignores the policy | Policy in the first 500 characters of the tool description, the one channel every client shows the model |
| Keyword search misses paraphrased recalls | Rich client-written tags at write time; revisit semantic search after 30 days |
| Client context can't hold a year of entries | Server-side aggregation tools and cited monthly digests |
| Reminders ignored because nothing pushes them | Daily email from a cron job, no LLM involved |

## Kill criteria at day 30

Stop or rethink if any of these hold:

- Fewer than five captures a week in weeks three and four.
- More than one in five recall spot-checks is wrong or untrusted.
- I reach for native Claude memory or a notes app instead.
- Reminders arrive but I don't act on them.
- More than one in ten automatic saves is something I didn't want kept.
- Any client misses more than one in five of the logs in the eval set.

## Open for Phase 3 (model)

Resolved in Phase 3: keyword search in v1, OAuth staging, the entry taxonomy, supersession and deletion.

Revisit at day 30: semantic search; a `measures` field for numbers like durations and sleep quality, currently kept in text.
