# Running Memento on your Mac

While Memento has one user on one computer, it runs entirely on that computer (ADR 0020). Nothing listens beyond `127.0.0.1`, and deployment (`docs/deploy.md`) is parked.

```
Claude Desktop ──mcp-remote──▶ 127.0.0.1:8000/mcp ──▶ Postgres (Docker, 127.0.0.1:5432)
                                     ▲    ▲
       launchd, every 15 min:  distiller  Pocket pull (planned, 0021)
```

## Once: set it up

You need Docker Desktop (set to start at login), [uv](https://docs.astral.sh/uv/), and Node (for `npx`, which Claude Desktop uses to connect).

Keep the repository outside `~/Documents`, `~/Desktop` and `~/Downloads`, for example in `~/Code/memento`. macOS blocks launchd jobs from those folders unless you grant Full Disk Access.

```bash
cp .env.example .env          # the defaults suit a Mac
make setup                    # dependencies and git hooks
make db migrate               # Postgres in Docker, then the schema
make superuser                # your login for the admin at http://127.0.0.1:8000/admin/
```

Set your time zone, which every tool result uses for `now` (0017): in the admin, add a **Profile** for your user with, for example, `Europe/London`.

## Keep it running

```bash
make launchd-install          # the server, now and at every login
make launchd-status           # what's running, and the last exit codes
```

Logs are in `~/Library/Logs/Memento/`. `make launchd-uninstall` stops the server and removes the job. After pulling new code, run `make launchd-install` again to restart it.

## Connect Claude Desktop

1. Create a token for Claude Desktop. It is shown once.

   ```bash
   make client USER=<your username> NAME=claude-desktop
   ```

2. Put it in a file that only you can read, so it never appears in a process list:

   ```bash
   printf 'Authorization: Bearer %s\n' '<token>' > ~/.memento-claude-desktop
   chmod 600 ~/.memento-claude-desktop
   ```

3. Add Memento to `~/Library/Application Support/Claude/claude_desktop_config.json`, replacing `<you>` with your macOS user name, then quit and reopen Claude Desktop:

   ```json
   {
     "mcpServers": {
       "memento": {
         "command": "npx",
         "args": ["-y", "mcp-remote@latest", "http://127.0.0.1:8000/mcp",
                  "--transport", "http-only",
                  "--header-file", "/Users/<you>/.memento-claude-desktop"]
       }
     }
   }
   ```

   If Memento doesn't appear, Claude Desktop may not find `npx` on its path. Use the full path instead, such as `/opt/homebrew/bin/npx` (run `which npx` to find yours).

Claude Desktop's **Custom connectors** setting can't be used. It connects from Anthropic's servers, which can't reach your Mac.

Two things are not yet known for Claude Desktop, so the capture policy stays in the first 500 characters of the `remember` description:

- whether it reads the server's `instructions`
- whether it truncates tool descriptions

### The skill

Claude Desktop accepts uploaded skills: in **Customize > Skills**, choose **+**, then **Upload a skill**. Code execution must be on. But an uploaded skill's description is limited to 200 characters, and `skill/memento`'s is longer. The long description is what made the skill trigger in Claude Code's evals. A shorter one is a behaviour change, so it needs its own eval run before it ships. Until then, Claude Desktop works from the tool descriptions alone. Claude Code without the skill scored 45 of 54 on the same cases.

## The distiller

It needs an Anthropic API key and a token of its own. Both go in `distiller/.env`, never in `.env`: the server refuses to start with a model key in its environment (0020).

```bash
make client USER=<your username> NAME=distiller      # read and write, never forget
cat > distiller/.env <<'EOF'
MEMENTO_URL=http://127.0.0.1:8000/mcp
MEMENTO_TOKEN=<the distiller's token>
ANTHROPIC_API_KEY=<your key>
EOF
chmod 600 distiller/.env
make distil                                          # once, by hand, to check
make launchd-install LAUNCHD_JOBS="server distiller" # then every 15 minutes
```

## Back up

There is one copy of your memories, on this Mac. Back it up and keep the file somewhere else too.

```bash
make backup                                   # to ~/Memento backups/
make backup BACKUP_DIR=~/Dropbox/Memento      # or anywhere else
make restore FILE=~/Memento\ backups/memento-2026-09-25-091725.dump CONFIRM=yes
```

**A backup keeps what you later forget.** `forget` deletes from the database, not from backups taken before it. Restoring an older backup brings those things back. To forget something completely, delete the backups that contain it.

## When it's time to deploy

Follow `docs/deploy.md`. To move your data, run `make backup` here, then `pg_restore` into the new database.
