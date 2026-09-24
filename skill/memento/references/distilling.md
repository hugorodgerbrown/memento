# Distilling the inbox

The inbox holds the user's words not yet turned into entries: solo voice notes from Pocket, and words saved with `raw_text` alone. Distilling them follows the same rules as live capture, with two differences. Every `raw_text` must be an exact excerpt of the transcript, with `capture` set. And there is no one to ask in the moment, so when you're unsure, leave it for the user.

## For each capture

1. **Read it and its context.** `inbox` returns the transcript, when it was recorded, any hints, the entries already made from it, and the user's recent current entries.
2. **Recall before you write.** For every state in the note (a condition, a place, a job, a plan), check the context entries and `recall` for anything current it updates. This step is what turns a log into a memory.
3. **Split into entries.** One per observation, each an exact excerpt. Sub-clauses of run-on speech are fine as excerpts. Keep together whatever will be updated together.
4. **Date from the recording, not the words.** `happened_at` defaults to when the note was recorded. "Last night" in a 9am note is the day before. A spoken clock time is local.
5. **Supersede, don't duplicate.** If an entry already made from this capture is wrong, supersede it as a `correction`.
6. **Close it.** `close_capture` with `processed`, or `dismissed` if nothing in it is worth keeping. The transcript stays either way.

Hints are another model's reading. Use them for orientation, never as a source of facts. Names are often mistranscribed. If you can't tell what a word was, keep the excerpt as it is and write the claim without guessing the name.

## Worked thread: plantar fasciitis over two days

**14 Sep 2026, 11.21.** "This morning, the plantar fasciitis has moved to the right foot. And last night it was actually pretty painful. And no exercise yesterday."

`recall` for plantar fasciitis finds nothing, so there is nothing to supersede.

| `raw_text` | `claim` | `happened_at` |
|---|---|---|
| This morning, the plantar fasciitis has moved to the right foot. | Plantar fasciitis moved from the left foot to the right. | 2026-09-14T11:21, exact |
| last night it was actually pretty painful | Plantar fasciitis was painful overnight. | 2026-09-13, day |
| no exercise yesterday | Did no exercise. | 2026-09-13, day |

**15 Sep 2026, 09.23.** "Bad night's sleep last night. Probably too much to drink down at Rob's in Somerset. Otherwise, tendons are fine, and PF is fine on left foot, and on right foot is very mild."

`recall` for plantar fasciitis finds the 14 Sep entry, still current. The foot update changes it:

| `raw_text` | `claim` | `kind` | notes |
|---|---|---|---|
| Bad night's sleep last night. | Slept badly on the night of 14 Sep 2026. | memory | 2026-09-14, day |
| Probably too much to drink down at Rob's in Somerset. | Puts the bad night's sleep down to drinking at Rob's in Somerset. | thought | |
| tendons are fine | Tendons were fine. | memory | |
| PF is fine on left foot, and on right foot is very mild | Plantar fasciitis has cleared on the left foot and is very mild on the right. | memory | `supersedes` the 14 Sep move, `change` |

Now `recall` shows "very mild, right foot" as current, and `as_of: "2026-09-14"` still shows the move to the right. Both feet are one entry, because the condition will be updated as a whole.
