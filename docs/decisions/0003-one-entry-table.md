# 0003. One Entry table with kinds

Status: accepted, September 2026

## Context

Memories, thoughts, decisions, reminders and digests share almost every field, and longitudinal queries want them together.

## Decision

A single `Entry` table with a `kind` field, with kind-specific rules enforced by check constraints. Tags are a Postgres array with a GIN index; entity-like tags use prefixes (`person:`, `project:`, `place:`, `org:`).

## Consequences

Simple queries and a single-query `timeline`. Renaming a tag means updating many rows, which is fine at personal scale. Procedural memory is deliberately out of scope.
