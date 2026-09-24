# 0015. Pocket's action items are ignored

Status: accepted, September 2026. Supersedes 0007, and the clause of 0006 that keeps action items as hints.

## Context

0007 turned Pocket's dated action items into reminders at ingest, and completed them when they were ticked off in Pocket. Reading nine days of real action items through Pocket's MCP server (24 September) showed what that path carries:

- An action item is Pocket's model's to-do list, not something the owner said. Of 15 items since 15 September, 11 had no date, and several came from passing remarks ("Research Cat Hotel video/story").
- Some are messages and email drafts (`send_message`, `draft_email`), not reminders.
- Due times are unreliable: one item has `dueDate` in UTC and `payload.reminder.dueDateTime` with no zone, for the same appointment.
- Tracking completion ties Memento to Pocket's action-item ids, and it needed its own fix (`current_version`) once a client superseded Pocket's reminder.

Memento exists to recall what the owner said and to summarise it across time. A task list belongs to Pocket.

## Decision

Ingest ignores action items entirely. They are not turned into entries, not kept in `Capture.hints`, and `action_items.*` events are logged as ignored. Pocket's summary stays as a hint.

A reminder the owner speaks is in the transcript. The client distils it from there like any other entry, with the owner's words as `raw_text`.

## Consequences

Nothing reaches the store from Pocket except the transcript and its summary. Spoken reminders wait for a client or the distiller (M7) instead of arriving at once. The `reminder` kind, `complete_reminder` and `due_reminders` are unchanged: they serve reminders written by clients. `PocketLink.timezone` is no longer read by ingest; M3 moves it to a per-user profile.
