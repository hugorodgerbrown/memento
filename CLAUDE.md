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
| `memories/pocket.py` | Pocket webhook: signature check, solo-voice rule. Action items are ignored (0015). |
| `memories/mcp_server.py` | The nine MCP tools, bearer auth and the `/mcp` app. Descriptions must match `docs/mcp-tools.md`. |
| `memories/views.py` | `/ingest/pocket/`, `/healthz`. |
| `memories/tests/` | One file per area; tests are named for the principle or decision they protect. |
| `docs/decisions/` | Architecture decision records. Add one for any decision that changes behaviour. |
| `docs/build-plan.md` | What to build next, in order, with acceptance criteria. |
| `render.yaml` | Staging: web service, Postgres and the deploy steps. Explained in `docs/deploy.md`. |
| `docs/evals/capture-policy.json` | How clients should react to real messages. Run against every client. |
| `skill/memento/` | *Planned (M6).* The Memento skill in Agent Skills format. Also the distiller's instructions. |
| `distiller/` | *Planned (M7).* Scheduled MCP client that structures the inbox with one chosen model. |

## Commands

```bash
make setup     # uv sync + git hooks
make db        # local Postgres 16 via Docker
make migrate
make test      # full suite (needs Postgres; SQLite is not supported)
make check     # everything CI runs: lint, format, migrations check, tests
make run       # development server, with /mcp (uvicorn, reloads)
make client USER=hugo NAME=claude-code   # MCP client + bearer token, shown once
make serve     # ASGI, exactly as Render runs it (collectstatic first)
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
- **The distiller is a client.** It never imports `memories` or `config`, and the server never holds a model-provider key (0013).
- **The skill is behaviour.** Like tool text, changes to `skill/` need eval runs before they merge.

## Status

Phases 1 to 3 (interrogate, define, model) are done: the data model, services, Pocket ingest and tool spec. M2 is built: the MCP server at `/mcp` with per-client bearer tokens (0016). M3, the contract, is built too (0017), ahead of M1's deploy. Staging (M1) is described but not yet applied, and OAuth is M8. Phase 4 (designing the morning email and web timeline) hasn't started; the build plan says where it slots in. Cross-model consistency (0013: contract, skill, inbox fallback, distiller) is planned across M3, M6 and M7.

## Verify before relying on these

These were researched or inferred, not confirmed against live systems:

- **Pocket payloads.** Whether `speakers.labeled` carries the full transcript; the signature format (hex, with or without `sha256=`). Milestone 4 records real deliveries as fixtures.
- **Client behaviour.** As of March–April 2026 reports, claude.ai ignores MCP server `instructions` and truncates tool descriptions at ~500 characters, and Claude Desktop doesn't read `instructions`. Re-check; it decides where guidance must live.
- **MCP authorisation spec** details (protected-resource metadata, dynamic client registration, client ID metadata documents) and what claude.ai and ChatGPT currently require.
