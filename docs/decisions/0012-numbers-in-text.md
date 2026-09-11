# 0012. Numbers stay in text until day 30

Status: accepted, September 2026

## Context

Logs contain quantities: durations, severity, sleep quality. A structured field would make aggregate questions exact.

## Decision

Keep numbers in raw text and claims for now. Revisit a `measures` field at the day-30 review.

## Consequences

Aggregate questions over long periods rely on the client parsing prose, which is fine for trends and fragile for totals.
