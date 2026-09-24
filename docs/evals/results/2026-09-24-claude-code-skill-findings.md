# Skill vs control: Claude Code (24 Sep 2026)

The M6 measurement. Two runs on the same evening, same server (with 0018), same model (`claude-opus-5-5`, pinned with `--model`), same flags, three runs per case. The only difference is `--skill`, which loads `skill/memento` as a plugin. Each run's JSON records the skills Claude Code loaded: `memento:memento` in 54 of 54 skill runs and 0 of 54 control runs.

| | Control | With skill |
|---|---|---|
| Passed | 45 / 54 (83%) | **54 / 54 (100%)** |
| policy failures | 8 | 0 |
| dates failures | 3 | 0 |
| Saves with no receipt (graded since the follow-up below) | 0 | 0 |
| Sensitive saved without asking | 0 | 0 |

Reports: [control](2026-09-24-claude-code-control.md), [skill](2026-09-24-claude-code-skill.md), each with its JSON.

The control reproduces the M5 baseline (45 of 54). Dropping `--disable-slash-commands` from the harness, which the skill needs, didn't move it.

## What the skill fixed

Each of the categories the M5 findings handed to M6:

| M5 finding | Control | With skill | What the skill runs did |
|---|---|---|---|
| An aside during a task isn't logged | 0/3 | 3/3 | Saved "I'm exhausted today", helped with the migration, ended with a one-line receipt. |
| Care displaces capture | 1/3 | 3/3 | Replied with care first (and pointed to support), then saved both observations, then one gentle receipt line. |
| Changes logged as new facts | 0/3 | 3/3 | `recall` before saving; superseded Lisbon as `change`; the receipt said Lisbon "now shows as true until then". |
| Month precision for "last weekend" | 2 runs | 0 | `2026-09-19`, day, with "the weekend of 19 Sep 2026" in the claim. |
| Drafting | 2/3 | 3/3 | Drafted, then "Want me to log the surgery in Memento?" |

Nothing that passed in the control failed with the skill.

## Worth knowing

- **0018 shows up in the control.** Without the skill, the `change` case now sends `happened_at: "2026-09"` for "last weekend". The server accepts it where it used to refuse it, so the fault shows as a precision that is too coarse (graded as dates) instead of a retry. The skill's precision table fixes it.
- **Silent saves: none.** This section first said two control runs (`correction`, `reminder`) saved without telling the user. That was wrong. The hand check matched only a few words, and those replies said "I've corrected it" and "I've set a reminder". The grader now checks Principle 9 itself: a run that saves must say so. Re-grading every stored run (M5 baseline, control, skill) finds no silent saves and changes no score.
- **`weak-client-escape`** passes in both runs by fixing the claim instead of falling back to the inbox. That's allowed, but it means the inbox path is still unexercised by a real client.

## Limits

- Three runs per case, one model, one evening. 100% is an upper-bound estimate. What it supports is that the skill beats the control on every M5 failure category, with no regressions.
- The skill's author could see the cases. `memories/tests/test_skill.py` fails if the skill quotes any eval message, except the padel message the build plan asks for and "don't log that", the product's own undo phrase. The skill's examples are new messages that test the same behaviour.
- `past-midnight` is still not run in either configuration (it needs 00:00 to 04:00).
- This is Claude Code only. claude.ai and ChatGPT need M8 before they can be measured.
