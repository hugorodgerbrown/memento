# Memento on your Mac

Memento runs on your Mac and nowhere else (ADR 0020). Claude Desktop talks to it, and your Pocket recordings are pulled in every 15 minutes.

## Set up (once)

You need Docker Desktop (set to open at login), [uv](https://docs.astral.sh/uv/) and Node. Keep the `memento` folder somewhere like `~/Projects`, not in Documents, Desktop or Downloads, because macOS blocks background jobs there.

In Terminal, in the `memento` folder:

```bash
cp .env.example .env
make setup
make db migrate
make superuser                # choose a username and password
make launchd-install          # starts Memento now and at every login
make connect-desktop          # connects Claude Desktop
```

Then quit Claude Desktop with **Cmd-Q**, open it again, and ask: *What's in my Memento inbox?*

One more thing, in the admin at <http://127.0.0.1:8000/admin/> (log in with your superuser): add a **Profile** with your time zone, for example `Europe/London`.

## Pocket

1. In the Pocket app: **Settings > Developer > API Keys**, create a key. In `.env`, set `POCKET_API_KEY=pk_...`.
2. In the admin, add a **Pocket link**: speaker label `Hugo`, any Pocket user id, and tick **One speaker is me**.
3. Check, then import, then keep it going:

   ```bash
   make pocket-pull SINCE=2026-09-14 DRY=1     # shows what it would do; stores nothing
   make pocket-pull SINCE=2026-09-14
   make launchd-install LAUNCHD_JOBS="server pocket"
   ```

Recordings with one speaker are kept. Conversations are skipped (0022). Voice notes wait in the inbox until you ask Claude to sort them.

## The skill

```bash
make skill-zip
```

In Claude Desktop: **Customize > Skills > + > Upload a skill**, and choose `dist/memento-skill.zip`. Code execution must be on.

## Now and then

| When | Run |
|---|---|
| After pulling new code | `git pull && make migrate && make launchd-install LAUNCHD_JOBS="server pocket"` |
| To back up (keep a copy off this Mac too) | `make backup` |
| If the skill changed | `make skill-zip`, then upload it again |

## If something's wrong

| What you see | Do this |
|---|---|
| Claude Desktop doesn't know Memento | `make connect-desktop`, then **Cmd-Q** Claude Desktop and reopen it |
| Is Memento running? | `curl -s http://127.0.0.1:8000/healthz` should print `ok`. If not, run `make launchd-install` |
| The admin doesn't show a new setting | the server is running old code: run `make launchd-install` |
| `make db` says *port is already allocated* | another Postgres uses 5432. In `.env`, set `MEMENTO_DB_PORT=5433` and change `5432` to `5433` in `DATABASE_URL` |
| Anything else | logs are in `~/Library/Logs/Memento/`, and `make launchd-status` shows what's running |

## Good to know

- **There is one copy of your memories, on this Mac.** Run `make backup` and keep the file somewhere else too. A backup keeps what you later forget; to forget something completely, delete the backups that hold it. `make restore FILE=... CONFIRM=yes` puts a backup back.
- **Claude Desktop starts Memento itself** (0023), so it works whenever Docker is running. Its Custom connectors setting won't work: that connects from Anthropic's servers, which can't reach your Mac.
- **The distiller** (optional) sorts the inbox on a schedule with its own Anthropic key. The key goes in `distiller/.env`, never in `.env`; the server refuses to start with a model key in its environment. Create its token with `make client USER=<you> NAME=distiller`, write `MEMENTO_URL=http://127.0.0.1:8000/mcp`, `MEMENTO_TOKEN=...` and `ANTHROPIC_API_KEY=...` to `distiller/.env`, check it with `make distil`, then run `make launchd-install LAUNCHD_JOBS="server pocket distiller"`.
- **Deployment is parked.** When it's time, follow `docs/deploy.md` and move the data with `make backup`. Pocket's webhook then replaces the pull (0021).
