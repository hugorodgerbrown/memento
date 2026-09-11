# Build plan (Phase 5)

Each milestone ends in something usable and a green CI. Don't start a milestone until the previous one meets its acceptance criteria.

## M1. Deployable skeleton on Render (staging)

- Web service and Render Postgres 16, configured from environment variables.
- Migrations run before each deploy; `collectstatic` runs at build.
- Health check on `/healthz`; admin reachable over HTTPS.
- Switch serving to ASGI (uvicorn workers) in preparation for M2.

**Done when:** a push to `main` deploys to staging, `/healthz` returns `ok`, and you can log in to the admin.

## M2. MCP server with a bearer token

- Streamable HTTP endpoint at `/mcp`, using the official Python MCP SDK mounted in Django's ASGI app.
- Day-one auth: a static bearer token per client, stored hashed, mapped to a user and a `client_name`.
- The nine tools from `docs/mcp-tools.md`, each a thin wrapper over `services.py`, with the exact descriptions, schema field descriptions, annotations and result shapes in the spec.
- `forget` enforces preview-then-confirm, except `services.is_simple_undo()`.
- Server `instructions` are sent too, for clients that read them.

**Done when:** contract tests call every tool through the MCP layer; a test fails if `remember`'s description exceeds 500 characters; Claude Code can log and recall against staging.

## M3. Pocket live on staging

- Point a personal Pocket webhook at staging `/ingest/pocket/`.
- Record real deliveries (with personal content redacted) as test fixtures: a solo note with a dated reminder, a conversation, a transcript edit, a label change, a deletion.
- Resolve every Pocket item under "Verify before relying" in `CLAUDE.md`, and update `pocket.py` and its tests to match reality.

**Done when:** fixtures from real payloads replace the hand-written ones and the suite passes.

## M4. Dogfood through Claude Code, and run the evals

- Use Memento daily from Claude Code for a week.
- Run `docs/evals/capture-policy.json` against Claude Code. Record the results in `docs/evals/results/`.

**Done when:** the pass bar in the eval file is met, and every failure is either fixed in tool text or logged as a known issue.

## M5. OAuth 2.1 for claude.ai and ChatGPT

- Authorisation server via django-oauth-toolkit: authorisation code + PKCE, protected-resource metadata, authorisation-server metadata, dynamic client registration. Client ID metadata documents when both clients support them.
- Scopes: `memento:read`, `memento:write`, `memento:forget`.
- Connect claude.ai (web and mobile) and ChatGPT; re-run the evals on each.

**Done when:** both clients connect, and pass the eval bar.

## M6 and M7. Morning email and read-only timeline

Blocked on Phase 4 design. The services already exist (`due_reminders()`, `inbox_count()`, `recall()`, `timeline()`).

- M6: daily cron job sending due reminders, waiting voice notes, and "on this day". No model involved.
- M7: a read-only web timeline for browsing and auditing entries, digests and their citations.

## Day-30 review

Against the kill criteria in `docs/brief.md`. Also decide: semantic search, and a `measures` field for numbers.
