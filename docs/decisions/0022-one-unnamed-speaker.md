# 0022. On the pull, one speaker can count as you

Status: accepted, 25 September 2026. Amends 0021, and how Principle 8 is applied to pulled recordings. The webhook's rule (0006) is unchanged unless the owner opts in.

## Context

The first dry run against the owner's account (25 September) skipped all 35 recordings. Pocket's REST transcript labels speakers by diarisation, as `SPEAKER_00` and `SPEAKER_01`. It never uses the voice-print name the app shows, so no recording ever matched the Pocket link's `speaker_label` ("Hugo"). The detail response gives voice-print counts (`matchedSpeakerCount`, `speakerNamesAppliedCount`) but not which speaker matched. On the pull, the owner's voice cannot be told from anyone else's by its label.

Of the recordings checked, most were short notes with exactly one speaker (`SPEAKER_00`). One was a conversation with two speakers, and one long recording had no speaker labels at all.

## Decision

**One speaker counts as you, if you say so.** A Pocket link has a setting, `one_speaker_is_me`, which is off by default. When it is on, a recording with exactly one speaker is stored, whatever Pocket calls that speaker. The log records the reason as "solo, one speaker (0022)", so these are distinguishable from name-matched ones.

*Widened the same day.* At first only Pocket's placeholder label (`SPEAKER_00`) counted. The next dry run showed Pocket also labels single speakers by voice-print id (`USER_SPEAKER_<uuid>`), and its voice print didn't reliably tag the owner. The owner's words: what they record is theirs to manage, not Memento's. So any single speaker counts, and the field was renamed from `one_unnamed_speaker_is_me` (0008 renames it, so the tick is kept).

**The rest of Principle 8 holds.**
- Two or more speakers, named or not, are skipped.
- A transcript with no speaker labels is skipped.
- A later revision that adds a second voice is not kept.
- Skips still keep no content.

**The owner chose this, knowing the risk.** They wear the Pocket, so a solo recording is almost always them. When it isn't (a voicemail on speaker, a video playing, someone else talking alone nearby), it lands in the inbox, where it can be seen and forgotten. Pocket speaker labels can't prevent that; only reading the note can.

## Consequences

- Solo voice notes arrive by pull. When the setting is off, the skip reason says how to turn it on.
- A few recordings that are not the owner may be stored and will need forgetting. This is the only way anyone else's words can be kept, and it needs a single voice, with no one else speaking, on the owner's Pocket. What the owner records is theirs to manage.
- If Pocket's API later exposes the voice-print name on segments, `speaker_label` matches it first and this setting stops mattering. The same applies to the webhook once it is live, if its labels carry names (unconfirmed).
