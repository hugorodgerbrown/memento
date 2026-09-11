# 0008. Deleting in Pocket doesn't delete in Memento

Status: accepted, September 2026

## Context

Pocket may delete recordings over time, and the user may tidy their Pocket library.

## Decision

`recording.deleted` is logged and otherwise ignored. Memento is the permanent record; forgetting is done deliberately in Memento.

## Consequences

Forgetting in Memento doesn't delete the recording in Pocket either; the user does both.
