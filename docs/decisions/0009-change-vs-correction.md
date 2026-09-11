# 0009. Corrections and changes are different

Status: accepted, September 2026

## Context

"The meeting is Wednesday, not Tuesday" and "I moved from Lisbon to Porto" both replace an entry, but only the first means the old entry was never true.

## Decision

Every supersede states `correction` or `change`. A change must say when it happened, and closes the old entry's `valid_until`; correcting a change's date moves that end date. `recall` has three views: `current` (optionally `as_of` a date), `history` and `all`. `timeline` counts things for the period they were true.

## Consequences

"Where did I live in 2025?" works. A future-dated change shows both entries until it happens. Chains are protected from direct deletion.
