# Decision records

Short records of decisions that shape behaviour: context, decision, consequences. Add a new one rather than editing an accepted record; mark the old one superseded.

| # | Decision |
|---|---|
| [0001](0001-intelligence-at-the-edges.md) | The server never generates text |
| [0002](0002-raw-is-sacred.md) | Raw words are immutable, enforced in the database |
| [0003](0003-one-entry-table.md) | One Entry table with kinds |
| [0004](0004-keyword-search-first.md) | Keyword search in v1, semantic search deferred |
| [0005](0005-auth-staging.md) | Bearer token first, then OAuth 2.1 |
| [0006](0006-pocket-solo-voice.md) | Pocket: webhook into an inbox, solo recordings only |
| [0007](0007-pocket-reminders-immediate.md) | Pocket's dated action items become reminders at once (superseded by 0015) |
| [0008](0008-pocket-deletes-dont-propagate.md) | Deleting in Pocket doesn't delete in Memento |
| [0009](0009-change-vs-correction.md) | Corrections and changes are different |
| [0010](0010-forgetting-and-tombstones.md) | Forgetting cascades and leaves a tombstone |
| [0011](0011-proactive-capture.md) | Capture is proactive, and the policy lives in the tool description |
| [0012](0012-numbers-in-text.md) | Numbers stay in text until day 30 |
| [0013](0013-cross-model-consistency.md) | Cross-model consistency: converge every client, with an inbox fallback |
| [0014](0014-what-you-said.md) | Memento records what you said, not everything that happened |
| [0015](0015-ignore-pocket-action-items.md) | Pocket's action items are ignored |
| [0016](0016-mcp-transport-and-clients.md) | The MCP endpoint: stateless HTTP beside Django, one token per client |
| [0017](0017-contract-choices.md) | How the contract is enforced |
| [0018](0018-partial-dates.md) | `happened_at` accepts a month or a year on its own |
| [0019](0019-the-distiller.md) | The distiller: a stateless window over the inbox, and what it leaves for you |
| [0020](0020-one-mac.md) | One person, one Mac: deployment is parked |
