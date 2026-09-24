---
name: memento
description: Keeps the user's personal memory in Memento, through its MCP tools (remember, recall, timeline, list_tags, inbox, forget). Use whenever the user mentions anything about their own life, even in passing or in the middle of another task - health, symptoms, sleep, exercise, food, mood and feelings, work, people they saw, places, decisions, plans, reminders - and whenever something they say may change a fact already saved (they moved, changed job, a symptom got better or worse). Also use when they ask about their own past, patterns or trends, want a summary of a period, say "don't log that", ask to forget something, or ask to process their Memento inbox.
---

# Memento

Memento is the user's memory. The server stores their words and never interprets them: you decide what to save, and you read it back. The tool descriptions set the policy; this skill shows how to apply it. If the Memento tools are deferred, load them before using them.

## 1. Read the whole message for the user's life

Most logging arrives unannounced. Before acting on a request, ask: did the user just tell me something about their own life?

- **An aside during a task is a log.** "Stinking cold today, but can you review this PR?" Save "Stinking cold today", say so in one line, then do the task. Never log the task itself.
- **Hard days are their life too.** When someone says they are struggling, respond with care first. Then save their words, and mention it gently in one short line at the end: "I've kept that in Memento; say if you'd rather I didn't." Don't skip the save because the subject is painful, and don't make the save the focus.
- **Don't save** questions, hypotheticals, fiction, or text you are drafting for someone else. For a drafted email that states a fact about the user ("I'm off for surgery next week"), write it, then offer: "Want me to log the surgery?"
- **Other people's private news** ("my brother's getting divorced, keep it quiet") is theirs, not the user's. Ask before saving.
- If you are unsure, ask "Log this?" once.

## 2. Before saving: tags, then the facts it might change

1. Call `list_tags` and reuse what exists.
2. If the message could update something that stays true over time, **call `recall` first**. That covers where they live, their job, a relationship, a health condition, a routine, or a plan. If a current entry now reads wrong, supersede it rather than adding a second fact:
   - `change`: it was true until now (they moved, the pain eased). Set `happened_at` to when it changed.
   - `correction`: it was never true (wrong number, wrong day, a mistranscribed name).
3. `supersedes` is one-to-one. Write as one entry whatever will later be updated as one unit. See `references/capture.md`.

## 3. Write each entry

One entry per observation. For each:

| Field | Rule |
|---|---|
| `raw_text` | The user's words for this observation, copied exactly, typos included. |
| `claim` | One standalone sentence with real dates and names. Never "today" or "yesterday": the server refuses them. |
| `kind` | `memory` (happened, or a state), `thought`, `decision`, or `reminder` (needs `due_at`). |
| `happened_at` | When it happened, worked out from the server's `now`, not your own sense of the date. |

The precision is how exactly the user said it. Don't make it finer:

| The user said | `happened_at` | `happened_precision` |
|---|---|---|
| "this morning", "yesterday", "on Monday" | that date, e.g. `2026-09-23` | `day` |
| "last weekend" | the Saturday, e.g. `2026-09-19` | `day`, with "the weekend of 19 Sep 2026" in the claim |
| "at 7.40", "just now" | the full time | `exact` |
| "last March" | `2026-03` | `month` |
| "back in 2019" | `2019` | `year` |
| no time at all, for something ongoing | omit both | |

"Tonight" or "this evening" said between midnight and 4am means the evening just gone, the day before `now`.

## Worked example

> woke up this morning and tendonitis is bad. played padel yesterdy for 90 minutes with Fred. Good sleep

Three observations, three entries, on 24 Sep 2026:

| `raw_text` | `claim` | `kind` | `happened_at` | tags |
|---|---|---|---|---|
| woke up this morning and tendonitis is bad | Woke up on 24 Sep 2026 with bad tendonitis. | memory | 2026-09-24, day | tendonitis, health |
| played padel yesterdy for 90 minutes with Fred | Played padel for 90 minutes with Fred on 23 Sep 2026. | memory | 2026-09-23, day | padel, exercise, person:fred |
| Good sleep | Slept well on the night to 24 Sep 2026. | memory | 2026-09-24, day | sleep |

The typo stays in `raw_text` and is fixed in `claim`. More examples, including changes and what not to save, are in `references/capture.md`.

## 4. After saving

Tell the user in one line what you saved. If they reply "don't log that", call `forget` with that entry's id and `confirm: true`. Nothing is saved silently.

## 5. When the server refuses

Errors say what to do. Fix the call and retry. If you still can't make a valid entry, call `remember` with `raw_text` alone: the words wait in the inbox and nothing is lost. Never drop a capture because the call was hard.

## Reading, digests and the inbox

- Questions about their past, patterns over time, or a summary of a period: `references/reading.md`.
- Processing the inbox (voice notes and saved words): `references/distilling.md`.
