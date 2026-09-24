# 0019. The distiller: a stateless window over the inbox, and what it leaves for you

Status: accepted, September 2026. Built in M7; implements part 4 of 0013.

## Context

0013 makes the distiller a scheduled MCP client that turns the inbox into entries, using the skill as its instructions. Building it raised three questions 0013 doesn't answer.

1. **Some notes need the user.** Live, a client asks "Log this?" when a message is someone else's private news or a draft for someone else. The distiller has no one to ask.
2. **So some notes stay in the inbox.** `inbox` returned the oldest notes first, at most 50. A handful of notes left for the user would sit at the front of every run, and new notes would never be seen.
3. **Cron jobs forget.** Nothing records that the distiller has already looked at a note, and adding that state would mean a new table or a new tool.

## Decision

**The distiller leaves what it would ask about.** It saves nothing from such a note and doesn't close it. The note waits in the inbox, the morning email (M9) lists it, and the user decides in any client. Notes with nothing worth keeping (a question, a hypothetical) are closed as `dismissed`, so they don't wait for anyone.

**Each run looks at a window, not the whole inbox.** `inbox` takes `received_since` and shows each note's `received_at`. The distiller runs every 15 minutes and asks for notes received in the last 35: every note gets two or three chances, and a run that dies mid-way is finished by the next one. That works because `inbox` shows the entries already made from a note, and the duplicate guard refuses the same words twice. After the window, a note belongs to the user. `--since` re-opens an older window by hand, for example after an outage.

The window is on `received_at`, not `captured_at`. A voice note can arrive hours after it was spoken, and it is the arrival that the distiller hasn't seen.

**The distiller's reach is narrow.** Its `Client` has `memento:read` and `memento:write`, never `memento:forget`. It is offered only `list_tags`, `recall`, `timeline`, `remember`, `close_capture` and `complete_reminder`. The code chooses which note to work on and hands it over, so the model is never given `inbox` or `save_digest`.

**One model, chosen by setting.** `DISTILLER_MODEL`, `claude-sonnet-5` by default. The provider key lives with the distiller, never the server (Principle 2). The distiller has its own `pyproject.toml` and lockfile, so no model SDK ever enters the server's dependencies.

## Consequences

- A note is structured within 15 minutes, left for the user, or dismissed. Nothing is lost, and a stuck note never blocks new ones.
- `inbox` gains a parameter. That is a tool-text change, measured by the distiller's evals (Principle 7).
- A note left after its window is not retried automatically. That is deliberate: the user is the fallback, and the morning email is where they see it.
- The distiller is measured like any client: `evals/distilling.py` runs the capture-policy cases as inbox notes. It reuses the same grader, which accepts "left" wherever the live policy says "ask".
