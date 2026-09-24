# Reading recipes

Entries are what the user mentioned, not a diary. A missing entry means it wasn't mentioned, not that it didn't happen. Counts are mentions, not occurrences.

## A question about the past

"When did I last see Fred?", "what did I decide about consulting?"

1. `list_tags` with a prefix (`person:`), if a tag might exist.
2. `recall` with a `query` and/or `tags`. If nothing comes back, try synonyms and related tags before saying so.
3. Answer from the entries only. Give each claim its date, and quote `raw_text` when wording matters: "On 23 Sep 2026 you wrote: 'played padel yesterdy for 90 minutes with Fred'."
4. If nothing matches, say so plainly. Never fill the gap from general knowledge or from this conversation.

## What was true then

"Where was I living last year?": `recall` with `as_of: "2025-06-01"`. An entry's `valid_until` tells you when it stopped being true: "you lived in Lisbon until 12 Feb 2026".

## How something changed

"How has my knee been?", "how has my thinking on hiring changed?"

1. `timeline` with `tag_prefix` or over the period, `bucket: "month"`. This shows the shape without the contents.
2. `recall` with `view: "history"` on the tags and months that matter. History includes states that later changed.
3. Describe the arc with dates and citations. Say "you mentioned knee pain in four entries in August", not "your knee hurt four times".

## A summary of a period, saved as a digest

"Summarise my September and keep it."

1. `timeline` for the month, then `recall` with `since` and `until` on the month and a `limit` that covers it.
2. Write the synthesis. Every statement must be traceable to an entry.
3. Only if the user asked to keep it: `save_digest` with `sources` set to exactly the ids you drew on, plus `covers_from`, `covers_to`, the full text as `raw_text`, and a one-sentence `claim`.
4. Don't state frequencies the entries don't carry.

## Forgetting

"Forget what I said about Ana."

1. `recall` to find the entries.
2. `forget` with `confirm: false`. Show the user, in plain words, everything the preview lists, including digests that would go.
3. Only after they agree, call again with `confirm: true` and the `confirm_token`.

The one shortcut: "don't log that" straight after a save. Then pass `confirm: true` directly.
