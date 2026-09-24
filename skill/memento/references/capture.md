# Capture examples

Dates assume `now` is Thursday 24 Sep 2026, Europe/London.

## An aside during a task

> Stinking cold today, but can you review this PR?

Save one entry, then review the PR:

| `raw_text` | `claim` | `kind` | `happened_at` |
|---|---|---|---|
| Stinking cold today | Had a bad cold on 24 Sep 2026. | memory | 2026-09-24, day |

Receipt, before the review: "Noted the cold in Memento." The PR is the task, not a log.

## A hard week

> honestly the last few days have been awful, anxious all the time and barely eating

Reply with care first. Then save two entries, because anxiety and appetite are tracked separately:

| `raw_text` | `claim` | `kind` |
|---|---|---|
| the last few days have been awful, anxious all the time | Felt anxious all the time over the days to 24 Sep 2026. | memory |
| barely eating | Was barely eating in the days to 24 Sep 2026. | memory |

`happened_at` 2026-09-24, day. End with one gentle line: "I've kept this in Memento; tell me if you'd rather I didn't."

## A change to a standing fact

> started at Monzo on Monday, first week done

`recall` with `query: "job work"`, or `tags: ["work"]`. It returns a current entry: "Works at Wise." Supersede it:

| field | value |
|---|---|
| `raw_text` | started at Monzo on Monday |
| `claim` | Started a new job at Monzo on 21 Sep 2026. |
| `kind` | memory |
| `happened_at` | 2026-09-21, day |
| `supersedes` | the Wise entry's id |
| `supersede_reason` | `change` |
| tags | work, org:monzo |

The Wise entry now ends on 21 Sep 2026. `recall` answers "where did I work in August?" correctly, and `view: "current"` shows only Monzo. Without the `recall`, both would read as current.

"first week done" adds nothing on its own; leave it in the conversation.

## A correction

> no wait, the dentist was Tuesday not Wednesday

`recall` the dentist entry. Supersede it as `correction` with the new date, and keep the user's new words as `raw_text`. A correction means the old entry was never true, so `view: "current"` and `view: "history"` both hide it.

When you rewrite a claim someone else wrote (a voice note's summary, say) into the user's own words, nothing has changed in the world. Use `correction`.

## Granularity: write together what changes together

`supersedes` is one-to-one: one entry can be replaced by only one entry. Plan for the next update.

Plantar fasciitis in both feet is one condition that will be updated as a whole ("left's clear now, right's mild"). Keep it as one entry, so the next update can supersede it cleanly:

| `raw_text` | `claim` |
|---|---|
| PF is fine on left foot, and on right foot is very mild | Plantar fasciitis has cleared on the left foot and is very mild on the right. |

Things that move independently get their own entries: sleep, a separate injury, a workout.

## Reported advice

> physio says no running for a month

Save it as the user's experience: "Physio advised no running for a month (24 Sep 2026)." It is not medical fact, so don't add advice of your own to the claim.

## Reminders

> remind me to renew the car insurance on the 3rd

`kind: reminder`, `due_at: 2026-10-03`. The 3rd of this month has passed, so the next 3rd is meant. For a bare weekday, "on Friday", use the next one after `now`. `claim`: "Renew the car insurance by 3 Oct 2026."

## Coarse times

| Said | `happened_at` | precision |
|---|---|---|
| "broke my wrist last March" | `2026-03` | month |
| "we met in 2015" | `2015` | year |
| "had a migraine last weekend" | `2026-09-19` | day ("the weekend of 19 Sep 2026" in the claim) |
| "I've been vegetarian for years" | omit | |

## Not saved

| Message | Why | What to do |
|---|---|---|
| "is magnesium good for cramp?" | A question | Answer it |
| "if I took the Berlin offer, what would rent be?" | A hypothetical | Answer it |
| "write a poem about a runner with a bad knee" | Fiction | Write it |
| "draft a note to the school saying Sam's off sick" | Drafting; Sam's illness is Sam's | Draft it; don't offer to log |
| "draft a message to my manager that I'm off for surgery" | Drafting, but the surgery is the user's | Draft it, then offer "Want me to log the surgery?" |
| "my friend's been made redundant, don't tell anyone" | Someone else's private news | Ask before saving |
| "as I said, good sleep" | Already saved this conversation | Nothing. If you do resend it, the server returns the existing entry |
