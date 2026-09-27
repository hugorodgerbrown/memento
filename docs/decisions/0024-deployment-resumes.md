# 0024. Deployment resumes: the real Memento runs on Render

Status: accepted, 27 September 2026. Supersedes 0020 and unparks M1 and M8. Amends 0021 on where the Pocket pull runs.

## Context

0020 parked deployment so that Memento could be used from one Mac first. It was built: launchd jobs, a local Postgres bound to `127.0.0.1`, Claude Desktop over stdio (0023), manual backups and a Pocket pull (0021). It was never lived in. At the time of this decision there were no launchd jobs installed, no logs in `~/Library/Logs/Memento/`, no backups in `~/Memento backups` and no Postgres container running, so there is no accumulated memory to move.

What the Mac cannot do is be reached. claude.ai and ChatGPT connect from Anthropic's and OpenAI's servers, so a laptop behind a home router is invisible to them, and a laptop that sleeps is invisible to Pocket. The owner wants to capture from the web and from a phone, which is the reason to be somewhere with a hostname.

## Decision

**The real Memento runs on Render, and it is not called staging.** `render.yaml` creates `memento` and `memento-db` in Frankfurt. This is the environment that holds real memories from its first day, so it is named for what it is. There is no separate staging environment: one user does not need two, and the local Mac setup is the place to try things first.

**The Mac becomes development.** Everything in `docs/local.md` still works and is still how the code is run, tested and demonstrated. It is no longer where the memories live. Claude Desktop's stdio connection (0023) stays the right answer for a local development database; against the real Memento, Claude Desktop connects over HTTPS with a bearer token.

**Bearer tokens now, OAuth next.** Claude Desktop and Claude Code get per-client bearer tokens (0005, 0016), which works the day the service is up. claude.ai and ChatGPT need OAuth 2.1, so M8 is unparked and is the next milestone rather than a someday one.

**The hostname is `memento-app.me`, decided before OAuth rather than after.** An OAuth issuer is a URL that registered clients remember, so moving to a custom domain after clients had registered would mean re-registering them. The domain was bought for this on 27 September 2026, before anything was applied, which is the cheapest moment it could have happened. It is the canonical host and will be the M8 issuer. Render's own `onrender.com` hostname keeps working, because it is what health checks are addressed to, and `settings.py` appends it to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`; the custom domain is in the blueprint, which the `onrender.com` one cannot be, since it does not exist until the service does.

**Pocket is not connected to the real Memento yet, and this is deliberate.** 0021's first dry run against the real account has not happened, and pointing an unproven ingest at the real store on the same day as a new deployment is two unproven things at once. Until it is connected, `POCKET_WEBHOOK_SECRET` is unset and every delivery is rejected with a 401.

**Backups are Render's, plus one copy that is not Render's.** The database keeps `ipAllowList: []`, so nothing outside Render reaches it. An off-Render copy therefore means opening the allow list to one address, taking a dump, and closing it again; `docs/deploy.md` says how. Render's own backups are the baseline and the reason this is no longer the single point of failure the Mac was.

## Consequences

- **Pocket recordings reach nothing until Pocket is connected.** The pull is a management command against whichever database it is pointed at, and on the Mac that is the development database, not the real one. So between this decision and Pocket's own, voice notes are captured by Pocket and not by Memento. Connecting it means either the webhook (0021's intended mechanism, now possible) or the pull as a second Render cron job with `POCKET_API_KEY`.
- **Forgetting now has two places it does not reach:** dump files, as 0020 already said, and Render's automatic backups, which are not ours to delete selectively. `forget` hard-deletes from the live database; a restore from any backup brings back what was forgotten after it was taken.
- Memento is now on the internet with bearer-token auth in front of it. Tokens are hashed at rest and scoped per client (0016), and `forget` is withheld by scope where it should be, but the attack surface is no longer "someone has my laptop".
- `docs/local.md` stops describing the only Memento and starts describing the development one. `make backup` and `make restore` keep working against it.
- The distiller moves off the Mac to a Render cron job, and its Anthropic key moves with it, into the cron job's environment. The web service still refuses to start if it finds a model key (Principle 2, 0013).
- 0020's `launchd` jobs, `make local-server` and the stdio connection are not removed. They are development tooling now, and the Pocket pull still needs them until Pocket moves.
