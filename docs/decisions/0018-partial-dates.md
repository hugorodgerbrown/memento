# 0018. `happened_at` accepts a month or a year on its own

Status: accepted, September 2026. Found by the M5 baseline.

## Context

For "last March" or "back in 2019" the precision is a month or a year, and a model sends what it means: `happened_at: "2026-09"` with `happened_precision: "month"`. The server read only full ISO 8601 dates and times, so it refused. In the M5 baseline this happened twice. Both times the model retried with a full date and kept `month` precision, so an error meant to help made the stored entry worse. Results already show a month-precision entry as `2026-09`, so the server was refusing the format it writes itself.

## Decision

**`happened_at` accepts `YYYY-MM` and `YYYY`.** Each is stored as its first moment in the user's time zone, the same way a bare date is read.

**The value sets the default precision.** `2026-09` means `month` and `2019` means `year`, just as a bare date means `day` and a time means `exact`. An explicit `happened_precision` can be coarser than the value (a full date sent with `month` is still accepted) but not finer. `2026-09` with `day` is refused, and the error says to set `month` or send the full date.

**Other date fields still need a full date.** `due_at`, `valid_until`, `since`, `until`, `as_of`, `covers_from` and `covers_to` have no precision to record, so a partial date there would be ambiguous. "2026-10" means 1 October as a start and 31 October as an end. They refuse it and say so.

The tool descriptions and schema text don't change (Principle 7). The server accepts what models already send; the spec's type column records it.

## Consequences

- The retry that swapped a month for an invented day no longer happens, so no precision is made up.
- A month that hasn't started yet is still a memory in the future, and is refused as before (0017).
- Everything that reads `happened_at` sees what it saw before: an aware datetime plus its precision.
