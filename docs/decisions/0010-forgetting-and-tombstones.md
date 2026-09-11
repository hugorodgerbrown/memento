# 0010. Forgetting cascades and leaves a tombstone

Status: accepted, September 2026

## Context

A store you can't delete from discourages honest writing. But deletion must be complete, and sources like Pocket re-send webhooks.

## Decision

`forget` hard-deletes every version in a chain and every digest citing any of them, transitively. `scope=source` also removes the voice note and all its entries. A `Tombstone` keeps only ids, the object type, the source id and the date, and blocks re-delivery. A single fresh entry can be undone in one step.

## Consequences

Clients can tell "forgotten" from "never saved". Tombstones hold identifiers, not content.
