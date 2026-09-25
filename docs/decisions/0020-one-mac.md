# 0020. One person, one Mac: deployment is parked

Status: accepted, September 2026. Parks M1 and the distiller's Render cron job until the owner says otherwise.

## Context

Memento has one user, its owner, and one developer, the same person. The build plan assumed a hosted staging service from M1 onwards, because Pocket's webhook and the web clients (claude.ai, ChatGPT) need a public URL. The owner wants to use Memento from their own Mac for a period first, without running anything on the internet.

## Decision

**Memento runs on the owner's Mac, and deployment is parked.** `render.yaml` and `docs/deploy.md` stay in the repository, unapplied, until the owner says it is time. Nothing here is removed; it waits.

**Nothing listens beyond the Mac.** The server binds to `127.0.0.1:8000`. Postgres publishes its port on `127.0.0.1` only; it used to listen on every interface, with a password in the repository.

**launchd keeps it running.** `make launchd-install` installs per-user LaunchAgents: the server (started at login, restarted if it stops) and, once configured, the distiller and the Pocket pull (0021), each every 15 minutes. Each job runs a `make` target, so there is one way to start anything, and logs go to `~/Library/Logs/Memento/`.

**The model key stays out of the server's way.** On one Mac the distiller's key sits next to the server. It lives in `distiller/.env`, which only `make distil` reads, and the server now refuses to start if a model-provider key is in its environment (Principle 2, 0013).

**Clients are the ones a Mac can reach.** Claude Desktop connects to `http://127.0.0.1:8000/mcp` with its own bearer token; `docs/local.md` has the setup. (Since 0023, Claude Desktop starts Memento itself over stdio instead.) claude.ai and ChatGPT on the web and phone can't reach the Mac, so M8 (OAuth) waits with deployment.

**Pocket will be pulled, not pushed.** Its webhook can't reach the Mac, so a scheduled job will pull new recordings through Pocket's API instead, into the same ingest path. That is its own decision (0021).

**Backups are manual.** `make backup` writes a dated `pg_dump` to `~/Memento backups` (or `BACKUP_DIR`); `make restore FILE=... CONFIRM=yes` replaces the database with one.

## Consequences

- One copy of the data, on one disk. Until the owner runs `make backup` and keeps the file somewhere else, a lost Mac is a lost memory.
- **Backups are the one place forgetting doesn't reach.** `forget` hard-deletes from the database (Principle 5), but not from dump files taken earlier, and restoring one brings back everything forgotten since and drops the tombstones that recorded it. `make restore` says so before it runs. To forget something completely, delete the backups that hold it.
- Nothing runs while the Mac sleeps. A note spoken overnight is pulled when it wakes, and the distiller's window is on `received_at` (0019), so it is still seen.
- M1, M8 and the web clients wait. M4 becomes the Pocket pull. The evals, the skill and the distiller are unchanged.
- Resuming deployment means applying the blueprint (`docs/deploy.md`) and moving the data with `make backup` and `pg_restore`, which this ADR's backup format already supports.
