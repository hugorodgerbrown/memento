# 0013. Cross-model consistency: converge every client, with an inbox fallback

Status: accepted, September 2026. To be built in milestones M3, M6 and M7 of the build plan.

## Context

Memento accepts writes from any MCP client, and each runs a different model. They diverge in predictable places: resolving relative dates (some clients don't know today's date or the user's time zone), how finely to split a message, where kinds begin and end, tag vocabulary, and how faithfully they copy words verbatim. Tool descriptions (0011) are guidance, not a guarantee, and some clients truncate them.

Two strategies were considered. Converging every client makes each one parse well enough through a strict server contract and a shared skill. A central distiller captures raw text everywhere and lets one chosen model do all the structuring, as the Pocket inbox already does. Convergence gives immediate structure; central distillation gives uniformity. We take both: converge by default, and fall back to the inbox where a client can't keep up.

## Decision

**1. The contract (server, every client).** Deterministic checks in `remember`, with errors that teach:

- Reject a `claim` that contains relative time words ("today", "yesterday", "tonight", "this morning", "last week" and so on). The error states today's date in the user's time zone.
- Reject a `memory` whose `happened_at` is in the future.
- Warn, without rejecting, when a new tag is within one edit of an existing tag, or differs only by plural.
- Every tool result includes `now` in the user's time zone. The time zone moves from `PocketLink` to a per-user profile.

**2. The Memento skill (clients with Agent Skills).** A `SKILL.md` in the open Agent Skills format, kept in `skill/memento/` and versioned with the server. It holds worked examples for capture and recipes for reading: trend questions, monthly digests, citing sources. It is also the distiller's instructions, so there is one house style.

**3. The inbox fallback.**

- `remember` accepts `raw_text` alone. It is then stored as a chat `Capture` in the inbox, with no derived fields. Every validation error offers this as the way out, so a weak model never loses a capture by giving up.
- Each registered client has a `mode`: `direct` or `inbox`. In `inbox` mode, every `remember` goes to the inbox, and any derived fields the client sent are kept as hints.
- A client's mode is set from its eval results: it is `direct` if it meets the pass bar in its best configuration, and `inbox` otherwise. The results are recorded in `docs/evals/results/`.

**4. The distiller is a client.** A separate process in `distiller/`, run on a schedule (every 15 minutes, so spoken or typed reminders aren't late). It uses one chosen model through its provider's API, uses the skill as its instructions, and reaches Memento only through MCP with its own token. It must never import server code. Principle 2 holds: the server still never generates text.

## Consequences

- What gets stored is consistent regardless of the client's model. Quality of derived fields varies by client but is measured, and inbox mode removes the weakest ones from direct writes.
- `Capture` generalises beyond Pocket: `source` becomes `pocket` or `chat`. The exact-excerpt rule applies to both; the solo-voice rule applies to Pocket only.
- Entries from inbox-mode clients are structured up to 15 minutes late. The morning email already reports waiting items.
- The skill has two consumers, the clients and the distiller, so changing it is a behaviour change: it needs eval runs, like tool text (Principle 7).
- Adds a deployable (the distiller) and a model-provider API key, held by the distiller, never by the server.
