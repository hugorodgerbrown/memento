# Memento

A personal memory store with an MCP interface. People log their lives from any MCP client (Claude, ChatGPT, Claude Code) and from a Pocket voice recorder. The server stores durably and dumbly. All interpretation happens in the calling LLM: distilling before storage, analysing after retrieval.

Read `docs/brief.md` first, then `docs/mcp-tools.md`. The diagrams in `docs/diagrams/` are the fastest way in.

## Principles: these are requirements, not preferences

Each principle is enforced in code and covered by tests. If a task seems to need breaking one, stop and ask rather than working around it.

1. **Raw is sacred.** `Entry.raw_text` and a `Capture` transcript are never rewritten. This is enforced by a save guard and by Postgres triggers (`0002_immutability_triggers`). Corrections are new entries.
2. **The server never generates text.** No LLM SDKs in dependencies, and no calls to model APIs from server code. The server validates, stores, searches, counts, schedules and deletes.
3. **Every answer cites.** Digests link to their sources through `Citation`.
4. **Changes append.** `supersedes` plus `supersede_reason`: `correction` means it was never true; `change` means it was true until then, and sets `valid_until`.
5. **Forgetting is a right.** `services.forget()` hard-deletes, cascades to citing digests, and leaves a content-free `Tombstone`. Admin deletion is disabled on purpose.
6. **Time has two axes.** `happened_at` (+ precision) is not `recorded_at`.
7. **Tool descriptions are the product.** The first ~500 characters of `remember` are the capture policy for every client. Treat edits to tool descriptions like UI copy changes: deliberate, reviewed, and eval-tested.
8. **Your words, not other people's.** Only solo Pocket recordings in the owner's voice are stored; skipped recordings leave no content.
9. **Nothing is saved silently.** Every save returns a receipt; "don't log that" is a one-step undo.

## Where things live

| Path | What |
|---|---|
| `memories/models.py` | `Entry`, `Citation`, `Capture`, `PocketLink`, `IngestLog`, `Tombstone`, `Client`, `Profile`. Constraints live here. |
| `memories/services.py` | All behaviour. MCP tools and views must be thin wrappers around these functions. |
| `memories/pocket.py` | Pocket ingest: the webhook and, on one Mac, the scheduled pull (0021, `manage.py pocket_pull`), through one store path. Solo-voice rule; action items ignored (0015). |
| `memories/mcp_server.py` | The nine MCP tools, bearer auth and the `/mcp` app. Descriptions must match `docs/mcp-tools.md`. |
| `memories/views.py` | `/ingest/pocket/`, `/healthz`. |
| `memories/tests/` | One file per area; tests are named for the principle or decision they protect. |
| `docs/decisions/` | Architecture decision records. Add one for any decision that changes behaviour. |
| `docs/build-plan.md` | What to build next, in order, with acceptance criteria. |
| `docs/local.md` | How Memento runs now: on the owner's Mac (0020). launchd jobs in `ops/launchd/`, Claude Desktop setup, backups. |
| `render.yaml` | Staging on Render, **parked** (0020). Explained in `docs/deploy.md`. |
| `docs/evals/capture-policy.json` | How clients should react to real messages. Run against every client. |
| `evals/capture.py` | Runs those cases against Claude Code (`make eval`), isolated, on the `memento_eval` database. A client: never imports `memories` or `config`. Results in `docs/evals/results/`. |
| `skill/memento/` | The Memento skill in Agent Skills format (M6). Also the distiller's instructions. `make eval SKILL=1` measures it. |
| `distiller/` | The distiller (M7, 0019): a scheduled MCP client that structures the inbox with one chosen model. Its own `pyproject.toml`; tests with `cd distiller && uv run python -m unittest`. |

## Commands

```bash
make setup     # uv sync + git hooks
make db        # local Postgres 16 via Docker
make migrate
make test      # full suite (needs Postgres; SQLite is not supported)
make check     # everything CI runs: lint, format, migrations check, tests
make run       # development server, with /mcp (uvicorn, reloads)
make client USER=hugo NAME=claude-code   # MCP client + bearer token, shown once
make eval      # capture evals against Claude Code (RUNS=3, CASE=id, SKILL=1, MODEL=id)
make distil    # run the distiller once (MEMENTO_URL, MEMENTO_TOKEN, ANTHROPIC_API_KEY)
make distil-eval  # capture-policy cases as inbox notes, through the distiller
make serve     # ASGI, exactly as Render runs it (collectstatic first)
make local-server   # as launchd runs it on the Mac: 127.0.0.1:8000, migrations first
make launchd-install [LAUNCHD_JOBS="server distiller"]   # keep it running (macOS)
make backup / make restore FILE=... CONFIRM=yes          # pg_dump to ~/Memento backups
make pocket-pull [SINCE=2026-09-14] [DRY=1]             # Pocket recordings, by pull (0021)
make skill-zip      # dist/memento-skill.zip, for Claude Desktop's skill upload
```

Copy `.env.example` to `.env` first. Python 3.14 (for `uuid.uuid7`), Django 6.1, Postgres 16, managed by uv.

## Conventions

- **Tests first for behaviour.** Every behaviour change ships with a test. Run `make check` before every commit; CI runs the same steps.
- **Errors teach.** A `ValidationError` message is read by a model that will retry, so say what to do instead.
- **Migrations:** never edit an applied migration. Database-level rules go in `RunSQL` with a working reverse.
- **No server-side model calls, ever** (Principle 2). If something seems to need one, it belongs in the client or a digest.
- **Deterministic ingest.** Webhooks map fields; they don't interpret. Idempotency comes from unique keys, not checks-then-writes.
- **British English** in user-facing text and docs.
- Keep the MCP surface at nine tools. Adding a tool needs an ADR.
- **The distiller is a client.** It never imports `memories` or `config`, and the server never holds a model-provider key (0013): the key lives in `distiller/.env`, and settings refuse to start with one in the environment.
- **One Mac for now (0020).** Nothing listens beyond `127.0.0.1`. Don't propose deploying until the owner says so.
- **The skill is behaviour.** Like tool text, changes to `skill/` need eval runs before they merge.

## Status

Phases 1 to 3 (interrogate, define, model) are done: the data model, services, Pocket ingest and tool spec. M2 is built: the MCP server at `/mcp` with per-client bearer tokens (0016). M3, the contract, is built too (0017), ahead of M1's deploy. M6, the skill, is built and beats a same-day control on Claude Code (54/54 against 45/54). Its description fits Claude Desktop's 200-character upload limit (`make skill-zip`); plugin packaging is outstanding. **Deployment is parked (0020):** Memento runs on the owner's Mac with Claude Desktop as the client, M1 and M8 wait, and Pocket comes in by a scheduled pull (0021), awaiting its first dry run against the real account. The pull is temporary: once a server Pocket can reach exists, the webhook is the mechanism and the pull is kept for backfills only. Phase 4 (designing the morning email and web timeline) hasn't started; the build plan says where it slots in. Cross-model consistency (0013: contract, skill, inbox fallback, distiller) is built for M3 and M6; the distiller (M7, 0019) is built and awaits its eval run.

## Verify before relying on these

These were researched or inferred, not confirmed against live systems:

- **Pocket payloads.** The REST pull's envelope and field names come from a third-party SDK (pocket-laravel), not Pocket's docs, which were unreachable; `make pocket-pull DRY=1` on the Mac is the first real check. Whether `updated_at` changes on edits, and the rate limits, are unknown. For the webhook: whether `speakers.labeled` carries the full transcript. Milestone 4 records real responses as fixtures.
- **Client behaviour.** claude.ai ignores MCP server `instructions` and truncates tool descriptions at ~500 characters (anthropics/claude-ai-mcp#93, open). For Claude Desktop neither is confirmed either way (checked Sep 2026), and uploaded skills are limited to a 200-character description (the skill's fits: 197, measured equal to the long one on Claude Code, 25 Sep). Re-check; it decides where guidance must live.
- **MCP authorisation spec** details (protected-resource metadata, dynamic client registration, client ID metadata documents) and what claude.ai and ChatGPT currently require.
