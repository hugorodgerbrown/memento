# 0001. The server never generates text

Status: accepted, September 2026

## Context

Memento could run its own model to summarise, tag and analyse, or leave that to the calling client.

## Decision

The calling LLM does all interpretation: distilling before storage, analysing after retrieval. The server validates, stores, searches, counts, schedules and deletes. Longitudinal analysis works through aggregation tools (`timeline`, `list_tags`) and client-written digests that cite their sources.

## Consequences

No inference cost or model lock-in, and entries written today can be reinterpreted by better models later. Capture quality depends on tool descriptions (see 0011). Analyses over large periods must be staged: shape first, then detail.
