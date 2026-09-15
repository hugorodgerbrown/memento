# 0014. Memento records what you said, not everything that happened

Status: accepted, September 2026. To be built in M2, M6 and M7 of the build plan.

## Context

The first real Pocket recordings (`docs/first-recordings.md`) showed the same
root cause behind two different failures, one on each side of the store.

Writing: a note on 15 September updated a state recorded on 14 September — plantar
fasciitis, left foot to right, then mild. That is a textbook `change` (0009), and
it is the reason the supersede chain exists. But `inbox` gives the client the
transcript, Pocket's hints and the entries made from *that capture* only. Nothing
tells it an entry already exists to supersede. Miss the link and `view="current"`
returns two standing facts that contradict each other.

Reading: the corpus is a record of utterances, not a diary. "No exercise
yesterday" is stored because it was said, not because a day passed. So a gap in
a tag means the subject stopped being mentioned, which is not the same as the
thing stopping. `timeline` returns counts per tag and reads, to a model, exactly
like frequency data.

## Decision

1. **`inbox` carries context.** Each capture comes with a bounded set of the
   owner's current entries that could plausibly be what it updates: the most
   recent current entries, plus those matching the capture's hint tags. Enough to
   supersede rather than duplicate, small enough not to flood a context window.
2. **The skill requires the check.** Distilling a capture begins with a `recall`,
   and the skill carries worked examples of choosing `change` over a second entry
   — including the plantar fasciitis thread, and the granularity trap: `supersedes`
   is one-to-one, so anything that will later be updated as a unit is written as a
   unit.
3. **Counts say what they are.** The `timeline` and `list_tags` descriptions state
   that counts measure mentions, not occurrences. The `recall` description forbids
   concluding that something did not happen from the absence of an entry, and
   `save_digest` forbids asserting a frequency the cited entries don't carry.

## Consequences

- A state tracked over time reads as one thread with a `valid_until`, instead of a
  pile of standing facts. This is the difference between a log and a memory.
- `inbox` results grow. The set is bounded and the payload reports what it held back.
- Answers get more honest and less impressive: "you mentioned poor sleep four times"
  rather than "you slept badly four times".
- Splitting granularity becomes a judgement with consequences, so it needs eval
  cases, not just a rule.
- No data model change. It is a change to tool text and to the `inbox` payload,
  which makes it a behaviour change: it ships with eval runs (Principle 7).
