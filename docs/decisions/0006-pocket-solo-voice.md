# 0006. Pocket: webhook into an inbox, solo recordings only

Status: accepted, September 2026

## Context

Pocket can record meetings and conversations as well as personal notes. Pocket's own summaries are another model's reading, and reviews of their accuracy are mixed.

## Decision

Signed webhooks store the transcript at once as a `Capture` in an inbox. Only recordings where every segment is labelled with the owner's voice are stored; others are skipped, and `IngestLog` records the reason with no content. Pocket's summary and action items are kept as hints. The client distils the inbox into entries.

## Consequences

Nothing is lost if a client isn't opened. Other people's words never enter the store. Lectures and unlabelled recordings wait for voice-print labelling.
