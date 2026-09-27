# 0026. One user, so Pocket's webhook needs no user id

Status: accepted, 27 September 2026. Amends 0006 on how a delivery finds its owner. The solo-voice rule (Principle 8, 0022) is unchanged.

## Context

M4 connects Pocket to the real Memento by its signed webhook (0021, 0024). The webhook found its owner by matching the payload's `user.id` to `PocketLink.pocket_user_id`, and rejected anything else as "unknown Pocket user". Pocket doesn't obviously show that id anywhere: its account details, through its own MCP server, give an email, a name and a plan, but no id. On the Mac the pull never needed it, so `docs/local.md` said to type "any Pocket user id". Carried over to Render, that would reject every delivery.

Memento has one user, and that user is the owner. The signature already proves a delivery came from their Pocket account, because only that account's webhook holds the secret.

## Decision

**The only Pocket link takes every signed delivery.** If exactly one link exists, a delivery reaches it whatever `user.id` it carries, or with none at all.

**The id is kept, in case it is needed later.** `pocket_user_id` may be left blank. The first delivery that carries a user id fills it in. An id that was already set is not overwritten: the owner set it, and a delivery doesn't get to change it.

**More than one link still needs an exact match.** With two or more links an unknown id is rejected, as before, so no one's recordings could reach someone else if Memento ever had a second user. Two links may both leave the id blank; uniqueness applies only to ids that are set.

**No link, no delivery.** With no link, deliveries are rejected, and the log says to add one in the admin.

## Consequences

- Connecting Pocket needs the webhook URL, the secret in Render and a Pocket link with the id left blank. Nothing has to be looked up.
- The pull (`manage.py pocket_pull`) already used "the only linked one" and is unchanged.
- A second user would need their id set before their first delivery, and the owner's too. That is a job for whoever adds the second user, and the multi-link rule already refuses to guess.
