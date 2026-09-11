# 0004. Keyword search in v1, semantic search deferred

Status: accepted, September 2026

## Context

Semantic search needs embeddings, which means a model in the loop, and MCP clients can't supply vectors.

## Decision

Postgres full-text search, with claim weighted above raw text, plus client-written tags at capture time. Revisit at day 30 if recall spot-checks fail.

## Consequences

No embedding infrastructure. Paraphrased queries can miss, so `recall`'s description tells clients to retry with synonyms and tags.
