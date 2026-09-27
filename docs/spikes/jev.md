# Spike: Jev for the distiller's decisions

Status: scoped, 27 September 2026. Waiting on Jev early access. Nothing here changes the distiller, the server or the skill; a go leads to an ADR amending 0019.

## The question

Can a "System One" model (TypeSafe's Jev) make the distiller's *decisions* as well as the distiller's model does, faster and cheaper, with a confidence we can act on?

Jev (as described second-hand; `typesafe.ai` was blocked from where this was written, so none of this is verified) takes a block of text and a set of typed questions, and answers them all at once as typed values with calibrated confidence. It writes no text. So it can't write an entry's `claim`, and it can't replace the distiller. It could make the choices the distiller makes around the writing.

## Four decisions, tested separately

| # | Decision | Asked of Jev as | Gold label comes from |
|---|---|---|---|
| 1 | **Triage:** keep, dismiss, or leave for the owner | one of three, with confidence | eval `expect`; owner's review of the real notes |
| 2 | **Kind** of each excerpt: memory, thought, decision, reminder | one of four, per excerpt | eval `entries[].kind`; owner spot-check |
| 3 | **Tags** from the existing vocabulary (`list_tags`) | a subset of a closed set | eval `tags_include`; the baseline's tags, owner-corrected |
| 4 | **Updates an entry?** none, or which of the current entries, and `change` or `correction` | a choice among N + none | eval `supersede_reason` cases, plus synthetic ones; real PF chain |

Kind and tags are asked per excerpt, using the gold excerpts, so they test classification and not splitting. Splitting a note into entries is writing, and stays with the model.

## Arms

```
                    ┌──────────────────────── A. baseline ───────────────────────┐
 inbox note ──┬──▶  │ distiller as it is (claude-sonnet-5): decides AND writes   │ ──▶ entries
              │     └────────────────────────────────────────────────────────────┘
              │     ┌──── B. Jev decides ────┐
              ├──▶  │ triage, kind, tags,    │ ──▶ scored against gold, decision by decision
              │     │ supersede, + confidence │
              │     └──────────┬─────────────┘
              │                ▼  C. stretch: Jev's decisions handed to a cheaper writer
              └──────────────▶ claude-haiku-4-5 writes claims and quotes ──▶ entries, graded as A
```

- **A** is the baseline, and the distiller's first eval run (M7 awaits one).
- **B** answers the four questions.
- **C** answers the cost question. Triage alone saves little: on the real notes only 6 of 41 were dismissed, and the other 35 still need a writer. The saving, if any, comes from a cheaper writer once the hard choices are made.

## Data

**Eval set.** The 19 capture-policy cases (`docs/evals/capture-policy.json`), run through the existing harness (`evals/distilling.py`). Only two involve updating an entry, so the spike adds about eight synthetic ones (changes, corrections, and near-misses that should be new entries), kept in the spike's folder until they earn a place in the policy file.

**Real set.** The 41 Pocket notes backfilled on 27 Sep 2026, which the live distiller has already handled (35 processed, 6 dismissed, 0 left, at 15:30 UTC). They reach the spike through a development copy, never the real Memento:

```
Pocket ──make pocket-pull SINCE=2026-09-14──▶ Mac, development Memento   (same solo-voice rule: only stored notes)
                                                  │ MCP `inbox` (the harness is a client)
                                                  ▼
                                      snapshot, gitignored ──▶ arms A, B, C
```

The pull applies the same rules as production, so conversations and other people's words are filtered out before anything is sent anywhere, and the harness reads only what Memento stored. The snapshot is taken before arm A runs, because A closes the notes.

**Gold labels for the real set** come from the owner reviewing arm A's decisions once, blind to Jev's, in a labelling sheet: a tick or a correction per note (triage) and per entry (kind, tags, supersede).

## Privacy gate (before any real note leaves the Mac)

1. Read TypeSafe's terms: retention, training on inputs, where data is processed. This needs `typesafe.ai` allowed.
2. The owner signs off on those terms, in writing, in the findings.
3. Notes forgotten in Memento are excluded (the pull already refuses them).
4. Only aggregates are committed. Transcripts, excerpts and per-note results stay in the gitignored snapshot folder and are deleted when the spike ends.

If the terms don't pass, the spike runs on the eval set alone and says so.

## Measures, with the bar set before running

| Measure | Bar for "go" |
|---|---|
| Triage accuracy | at least the baseline's, on both sets |
| Critical triage errors: keeping what isn't the owner's (a greeting, a recording played aloud, someone else's news) | **zero** |
| Dismissing a real note the owner wanted | at most one on the real set |
| Calibration: accuracy per confidence band | high-confidence answers (the top band covering at least 80% of notes) are at least as accurate as the baseline; low confidence is a usable "leave for the owner" |
| Kind accuracy | 90% or more, and no more than 3 points below the baseline |
| Tags: precision, recall, new near-duplicate tags | precision at least the baseline's; no new near-duplicates |
| Supersede: right entry and right reason | at least the baseline's |
| Arm C against arm A (existing grader) | no drop in the eval pass rate, at 30% or less of the cost per note |
| Latency per note | reported; no bar |

Each arm runs three times, to see variance. With 60 notes, differences of a few points are noise; the findings will say which differences are.

## Build

- `spikes/jev/`: its own `pyproject.toml`, like the distiller. A client: it never imports `memories` or `config`. It reads through MCP only.
- The Jev key lives in `spikes/jev/.env` (gitignored). The server never sees it. If Jev is adopted, `TYPESAFE_API_KEY` joins `MODEL_KEYS` in `config/settings.py`, so the server refuses to start with it.
- The Jev call sits behind one small interface, so the harness can be built and tested against a stand-in while access is pending.

## Timebox: three working days from access

| Day | Work |
|---|---|
| 0 (now) | Apply for early access. Allow `typesafe.ai` and `docs.typesafe.ai`. Read the docs and the terms: privacy gate. |
| 1 | Development copy of the 41 notes, snapshot, synthetic supersede cases. Arm A, three runs. Owner labels the real set. |
| 2 | Arms B and C, three runs each. |
| 3 | Analysis, calibration chart, findings in `docs/evals/results/`, go or no-go. |

## What comes out

- **Go:** an ADR amending 0019 ("one chosen model" becomes a decider and a writer), with the numbers, the confidence threshold, and what low confidence does. Then eval runs, as for any skill or tool-text change.
- **No-go:** the findings, and why, so it isn't re-litigated without new evidence.
- **Either way:** the distiller's first measured baseline, and eight more supersede cases.

## Out of scope

Server code, tool descriptions, the skill, the production distiller, and anything that sends a note to TypeSafe from the real Memento.
