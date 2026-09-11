# 0002. Raw words are immutable, enforced in the database

Status: accepted, September 2026

## Context

Two lossy interpreters sit either side of the store: one rewrites on the way in, another on the way out. Unchecked, the record drifts from what was said. This is the film's warning: Leonard can't audit his notes.

## Decision

`Entry.raw_text` and `Capture` transcripts are immutable, enforced by a save guard and Postgres triggers. Derived fields (claim, tags, dates) sit beside the raw text and can be rebuilt. For voice notes, `raw_text` must be an exact excerpt of the transcript, and the server checks it.

## Consequences

Every answer can be traced to the user's own words. Typos and mishearings stay in raw text; fixes go in the claim. Transcript edits in Pocket append revisions.
