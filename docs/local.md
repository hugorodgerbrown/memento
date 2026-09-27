# Memento on your Mac

This is the **development** Memento: where the code is run, tested and tried out. The real one, holding real memories, is on Render — see [`deploy.md`](deploy.md) (ADR 0024).

Nothing here listens beyond `127.0.0.1`, and nothing here is your real memory store. Claude Desktop can talk to this copy, and Pocket recordings are pulled into it every 15 minutes.

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
2. In the admin, add a **Pocket link**: speaker label `Hugo`, Pocket user id left blank, and tick **One speaker is me**.
3. Check, then import, then keep it going:

   ```bash
   make pocket-pull SINCE=2026-09-14 DRY=1     # shows what it would do; stores nothing
   make pocket-pull SINCE=2026-09-14
   make launchd-install LAUNCHD_JOBS="server pocket"
   ```

Recordings with one speaker are kept. Conversations are skipped (0022). Voice notes wait in the inbox until you ask Claude to sort them.

**The pull writes here, to the development database, not to the real Memento** (0024). Until Pocket is connected to Render — by its webhook, or by the pull as a cron job there — recordings you make do not reach your real memories.

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

- **This database is disposable; the one on Render is not.** `make backup` dumps this one, and `make restore FILE=... CONFIRM=yes` puts a dump back into it — which is how you would test a dump of the real Memento. To dump the real one, see the backups section of [`deploy.md`](deploy.md). A backup keeps what you later forget; to forget something completely, delete the backups that hold it.
- **Claude Desktop starts Memento itself** (0023), so it works whenever Docker is running, and it talks to *this* copy. Pointing Claude Desktop at your real memories is not solved yet: its Custom connectors setting needs OAuth (M8), and the `mcp-remote` alternative is what failed here and caused 0023. Claude Code is the client that works against the deploy today; see [`deploy.md`](deploy.md).
- **The distiller** (optional) sorts the inbox on a schedule with its own Anthropic key. The key goes in `distiller/.env`, never in `.env`; the server refuses to start with a model key in its environment. Create its token with `make client USER=<you> NAME=distiller`, write `MEMENTO_URL=http://127.0.0.1:8000/mcp`, `MEMENTO_TOKEN=...` and `ANTHROPIC_API_KEY=...` to `distiller/.env`, check it with `make distil`, then run `make launchd-install LAUNCHD_JOBS="server pocket distiller"`.
- **Deployment happened** (0024). [`deploy.md`](deploy.md) is the runbook for the real Memento; this page stays as the development setup.
