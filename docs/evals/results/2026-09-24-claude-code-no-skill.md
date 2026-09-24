# Capture evals: claude-code-no-skill

Run 24 Sep 2026 16:21 (Europe/London). Model `claude-opus-5-5[1m]`. 45 of 54 runs passed (83%); the bar is 80% (docs/evals/capture-policy.json).

| Case | Passed | Failure categories | Notes |
|---|---|---|---|
| log-multi | 3/3 |  |  |
| log-terse | 3/3 |  |  |
| explicit-decision | 3/3 |  |  |
| reminder | 3/3 |  |  |
| question-only | 3/3 |  |  |
| task-with-aside | 0/3 | policy |  |
| drafting | 2/3 | policy |  |
| hypothetical | 3/3 |  |  |
| fiction | 3/3 |  |  |
| others-private | 3/3 |  |  |
| change | 1/3 | dates, policy | remember was rejected at least once |
| correction | 3/3 |  |  |
| repeat | 3/3 |  |  |
| undo | 3/3 |  |  |
| low-mood | 0/3 | policy |  |
| quoted-advice | 3/3 |  |  |
| past-midnight | not run | | needs local time 00:00-04:00 |
| no-date-knowledge | 3/3 |  |  |
| weak-client-escape | 3/3 |  | 1 note(s) sent to the inbox with raw_text alone; fixed the claim instead of using the inbox |

Failures by category: dates 2, splitting 0, kinds 0, tags 0, verbatim 0, policy 9

## Failures

- `task-with-aside` run 1: policy: neither saved nor asked
- `change` run 1: dates: happened_at 2026-09 not last-weekend
- `change` run 1: policy: supersede_reason ''
- `low-mood` run 1: policy: nothing saved
- `task-with-aside` run 2: policy: neither saved nor asked
- `low-mood` run 2: policy: nothing saved
- `task-with-aside` run 3: policy: neither saved nor asked
- `drafting` run 3: policy: neither saved nor asked
- `change` run 3: dates: happened_at 2026-09 not last-weekend
- `change` run 3: policy: supersede_reason ''
- `low-mood` run 3: policy: nothing saved
