# 0017. How the contract is enforced

Status: accepted, September 2026. Built in M3; implements part 1 of 0013.

## Context

0013 sets the rules: the server states the date in the user's time zone, rejects relative time words and memories dated in the future, warns on near-duplicate tags, and keeps anything a client can't structure in the inbox. Building them raised five questions 0013 doesn't answer.

## Decision

**Time zone.** A `Profile` per user holds it, moved from `PocketLink` in three migrations (schema, data, schema). Users without a profile are treated as UTC. Every tool result carries `now` in that zone, and dates or times sent without a zone are read in it, not in the server's.

**A scheduled change may be dated ahead.** 0009 says a future-dated change shows both entries until it happens ("moving to Porto in December"). 0013 rejects a memory dated in the future. A memory that supersedes another as a `change` is exempt; every other memory dated more than five minutes ahead is rejected.

**Relative time is checked in `claim` only.** `raw_text` is the user's words and keeps its "yesterday". The list is deliberately narrow (today, tonight, yesterday, tomorrow, recently, this/last/next with a period, "n days ago"); weekday names are allowed because "Monday 28 Sep 2026" is how a good claim reads.

**Raw-only saves are idempotent by key, not by lookup.** A chat capture's `external_id` is the date in the user's zone plus a hash of the whitespace-squashed, case-folded words, so the same words on the same day hit the unique constraint on `Capture`. The solo-voice rule stays in `pocket.py`; the exact-excerpt rule applies to chat captures as to voice notes.

**"Don't log that" covers inbox saves.** `is_simple_undo` also accepts a single chat capture received in the last 15 minutes with nothing made from it, so Principle 9 holds for raw-only saves.

**Where the fallback is offered.** Every validation error from `remember` ends with the way out: call again with `raw_text` alone. Clients in `inbox` mode, and any call with no derived fields, go straight to the inbox; derived fields a client did send are kept in `hints.fields`.

## Consequences

- A weak client cannot lose a capture: each error names the date or the problem and the way out, and the way out always succeeds for non-empty words.
- Tag warnings are advisory. The entry is saved; the receipt carries `warnings`.
- `remember`'s schema now makes `claim` and `kind` optional, and the `inbox` description covers typed notes as well as voice notes. Both are tool-text changes awaiting an eval run (Principle 7).
