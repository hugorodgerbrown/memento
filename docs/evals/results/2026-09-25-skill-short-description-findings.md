# A skill description that fits Claude Desktop (25 Sep 2026)

Claude Desktop uploads skills with a description of at most 200 characters. `skill/memento`'s was 657, and the description is what makes a skill trigger: M6's gains depended on it. So a short one was measured against the long one before replacing it.

**Setup.** The two runs were made in parallel on the same morning, against the same server, with `claude-opus-5-5`, three runs per case. The skill bodies were identical. The only difference was the frontmatter `description`, loaded with `--skill-dir`. `past-midnight` wasn't run: it needs 00:00 to 04:00.

| | Long (657) | Short (197) |
|---|---|---|
| Passed | 54 / 54 | **54 / 54** |
| Skill loaded | 54 / 54 | 54 / 54 |

Reports: [long](2026-09-25-claude-code-skill-long.md), [short](2026-09-25-claude-code-skill-short.md), each with its JSON.

The short description, now the skill's:

> The user's memory. Use whenever they mention their own life (health, mood, sleep, work, people, plans), even in passing or mid-task, or change a saved fact; and to recall, forget or sort the inbox.

It keeps the three cues behind the M6 fixes: *mid-task* (asides), a list of subjects that includes *mood* (hard days), and *change a saved fact* (recall before superseding). Read by hand, the short-description runs of those cases behave as the long ones did. The aside was saved with a receipt. Porto superseded Lisbon as a `change` on 19 Sep at day precision. Low mood got care first, then a save, then a gentle receipt.

**Limits.** This is measured on Claude Code, not Claude Desktop, which can't be driven headless. Both use the same models and the same skill mechanism, but Desktop's triggering is unmeasured. Watch the first week of use for asides that aren't saved, and re-check if they appear.
