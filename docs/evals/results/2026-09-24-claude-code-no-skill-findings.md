# Baseline findings: Claude Code, no skill (24 Sep 2026)

The M5 baseline. Scores are in [the report](2026-09-24-claude-code-no-skill.md) and every run's tool calls, final reply and stored entries are in [the JSON](2026-09-24-claude-code-no-skill.json). Model `claude-opus-5-5`, three runs per case, run by `evals/capture.py` against the M3 contract (PR #5) with only the Memento MCP server connected and none of the operator's settings, hooks, CLAUDE.md or skills.

**45 of 54 runs passed (83%)**, over the 80% bar, so Claude Code qualifies for `direct` mode. No sensitive case was saved without asking. Every failure below was read by hand; two grading faults found on the way (an offer to save counted as not asking) were fixed in the grader and the runs re-scored, not re-run.

## Failures by category

| Category | Failures | Where |
|---|---|---|
| policy | 7 | `task-with-aside` 3, `low-mood` 3, `drafting` 1 |
| policy (supersede) | 2 | `change` 2 |
| dates | 2 | `change` 2 |
| splitting, kinds, tags, verbatim | 0 | |

## What the failures say

1. **An aside during a task is not logged.** "I'm exhausted today, but can you help me fix this Django migration error?" got help with the migration every time, and neither a save nor an offer (0/3). The model treats the message as a task.
2. **Care displaces capture.** "been feeling really low all week, not sleeping much" got a kind reply and a question about talking it through, and was never saved (0/3). The policy is to keep it, because it is the user's own life; the model's care behaviour comes first and capture never follows.
3. **Changes are logged as new facts.** For "Moved into the new flat in Porto last weekend!" with "I live in Lisbon" already stored, the model never called `recall`, so it saved a second standing fact instead of superseding Lisbon as a `change` (2 of 3 runs). This is 0014's problem, seen in a clean client.
4. **Month precision is rejected by format.** Twice the model sent `happened_at: "2026-09"` with precision `month`; the server refused it as not ISO 8601, and the model retried with a full date but kept `month` precision for "last weekend", which is a day. The server could accept `YYYY-MM` and `YYYY` when the precision says so.
5. **Drafting is mostly handled.** Two of three runs drafted the email and offered to save the surgery; one drafted without mentioning it.

## What worked

- **Dates.** Every relative date resolved correctly: "yesterdy", "this morning", "on Monday" (the next Monday), and "last weekend" where it was saved at day precision. `no-date-knowledge`, run with no system prompt and so no date from Claude Code, took today's date from the server's `now` in all three runs.
- **Splitting and verbatim.** "woke up this morning and tendonitis is bad. played padel yesterdy for 90 minutes with Fred. Good sleep" became three entries every time, with the typo kept in `raw_text`.
- **Skips.** Questions, hypotheticals and fiction were never saved (9/9). A repeat was not saved again (3/3).
- **Corrections and undo.** "actually it was 60 minutes not 90" superseded as a `correction` (3/3); "don't log that" called `forget` with `confirm: true` directly (3/3).

## How Claude Code sees the tools

Claude Code defers MCP tools: the model sees `mcp__memento__remember` by name and loads its description with `ToolSearch` only when it decides to use it. The capture policy in the first 500 characters of `remember` is therefore not in front of the model when it decides whether a message is worth logging, which fits failures 1 and 2. Server `instructions` did reach it. The skill (M6) is the place to close this for Claude Code.

## Not run

`past-midnight` needs a run between 00:00 and 04:00 local time, because the client's clock and the server's `now` both have to be past midnight. Run `make eval CASE=past-midnight` in that window to complete the baseline.

## For M6

The categories the skill should move: aside-during-task and low-mood capture (policy), recall-before-save for changes (policy), and precision for "last weekend" (dates). Re-run with the skill installed and keep only changes that improve these without costing the cases that pass.
