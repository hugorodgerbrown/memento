# 0007. Pocket's dated action items become reminders at once

Status: superseded by [0015](0015-ignore-pocket-action-items.md), September 2026

## Context

Spoken reminders are time-sensitive and shouldn't wait for inbox processing. But an action item's title is written by Pocket's model, not the user.

## Decision

Dated action items create reminders immediately. `raw_text` is the whole transcript (the user's words); `claim` is Pocket's title; provenance names Pocket's model. Undated items stay as hints. Completing one in Pocket completes it here.

## Consequences

Reminders fire on time without breaking 0002. Clients can later supersede a reminder with a tighter excerpt.
