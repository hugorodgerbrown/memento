# Build plan (Phase 5)

Each milestone ends in something usable and a green CI. Don't start a milestone until the previous one meets its acceptance criteria. Decisions referenced as 00NN are in `docs/decisions/`.

## M1. Deployable skeleton on Render (staging)

- Web service and Render Postgres 16, configured from environment variables.
- Migrations run before each deploy; `collectstatic` runs at build.
- Health check on `/healthz`; admin reachable over HTTPS.
- Switch serving to ASGI (uvicorn workers) in preparation for M2.

**Done when:** a push to `main` deploys to staging, `/healthz` returns `ok`, and you can log in to the admin.

In progress. `render.yaml` describes both resources and the build, pre-deploy and
start commands; serving is gunicorn with uvicorn workers over `config.asgi`, and
`docs/deploy.md` has the first-run steps. Outstanding: apply the blueprint in
Render, create the superuser, and confirm the deployed health check and admin.

## M2. MCP server and client registry

- Streamable HTTP endpoint at `/mcp`, using the official Python MCP SDK mounted in Django's ASGI app.
- A `Client` model: owner, name (becomes `client_name` in provenance), hashed bearer token, scopes, and `mode` (`direct` by default, or `inbox`; see 0013).
- The nine tools from `docs/mcp-tools.md`, each a thin wrapper over `services.py`, with the exact descriptions, schema field descriptions, annotations and result shapes in the spec.
- `forget` enforces preview-then-confirm, except `services.is_simple_undo()`.
- `inbox` carries context (0014): with each capture, a bounded set of the owner's current entries that it might update, so a client can supersede instead of duplicating.
- `timeline` and `list_tags` say that counts measure mentions, not occurrences; `recall` forbids inferring that something didn't happen from the absence of an entry (0014).
- Server `instructions` are sent too, for clients that read them.

**Done when:** contract tests call every tool through the MCP layer; a test fails if `remember`'s description exceeds 500 characters; Claude Code can log and recall against staging.

Built (0016): `/mcp` serves the nine tools, stateless, with per-client bearer tokens from `manage.py create_client`. Contract tests cover every tool in-process and over HTTP, and a test compares each description with `docs/mcp-tools.md`. Outstanding: Claude Code against staging, which needs M1's deploy.

## M3. The contract (0013, part 1)

- Move the time zone from `PocketLink` to a per-user profile, with a data migration.
- Every tool result includes `now` in the user's time zone.
- `remember` rejects a claim containing relative time words, and a `memory` dated in the future. The errors state today's date in the user's time zone.
- `remember` warns when a new tag is within one edit of an existing tag, or differs only by plural.
- `remember` accepts `raw_text` alone and stores it as a `Capture` with `source="chat"` in the inbox. Every validation error offers this as the way out. Clients in `inbox` mode always take this path, with any derived fields kept as hints.
- Generalise `Capture` for chat: a generated `external_id`, a single segment, and the exact-excerpt rule. The solo-voice rule stays Pocket-only. The duplicate guard covers raw-only captures.

**Done when:** each rule has a test named for it, including a weak-client simulation: repeated invalid calls end in an inbox capture, never a lost one.

Built (0017), ahead of M1's deploy by choice: `Profile` holds the time zone; every result carries `now`; `remember` rejects relative time in claims and memories dated ahead (a scheduled `change` excepted, per 0009), warns on near-duplicate tags, and keeps raw-only saves and inbox-mode clients' words as chat captures. Tests are in `memories/tests/test_contract.py`. The tool-text changes have not had an eval run.

## M4. Pocket live on staging

- Point a personal Pocket webhook at staging `/ingest/pocket/`.
- Record real deliveries (with personal content redacted) as test fixtures: a solo note, a conversation, a transcript edit, a label change, a deletion.
- Resolve every Pocket item under "Verify before relying" in `CLAUDE.md`, and update `pocket.py` and its tests to match reality.
- Decide push versus pull now that Pocket ships an MCP server with a recency mode (`docs/first-recordings.md`). Push stays the plan unless the unknowns above bite.

**Done when:** fixtures from real payloads replace the hand-written ones and the suite passes.

## M5. Dogfood and baseline evals

- Use Memento daily from Claude Code for a week.
- Run `docs/evals/capture-policy.json` against Claude Code without any skill. Record the results in `docs/evals/results/`.

**Done when:** a baseline exists for every case, with failures categorised: dates, splitting, kinds, tags, verbatim, or policy.

Baseline recorded (24 Sep 2026): `evals/capture.py` (`make eval`) runs every case against Claude Code, isolated, on its own database. 45 of 54 runs passed (83%); failures and what they mean are in `docs/evals/results/2026-09-24-claude-code-no-skill-findings.md`. Outstanding: `past-midnight`, which needs a run between 00:00 and 04:00, and the week of daily use. Finding 4, month precision refused by format, is fixed in the server by 0018.

## M6. The Memento skill (0013, part 2)

- Author `skill/memento/SKILL.md` in the open Agent Skills format: frontmatter name and description with a clear trigger, then worked examples (starting with the padel message), kinds and their edge cases, splitting rules, tag conventions, date resolution, correction versus change, and reading recipes for trend questions, monthly digests and citations.
- Include the plantar fasciitis thread from `docs/first-recordings.md`: recall before distilling, choose `change` over a second entry, and write as one unit whatever will later be updated as one unit, because `supersedes` is one-to-one (0014).
- Keep `SKILL.md` short; put long examples in `references/` so clients load them only when needed.
- Re-run the evals with the skill installed. Keep changes that improve the failure categories from M5, and revert changes that don't.
- Package it for distribution: a Claude plugin and an OpenAI plugin, each bundling the skill with the Memento connector where the platform allows.

**Done when:** the skill measurably beats the baseline on Claude Code, and the with/without results are recorded.

## M7. The distiller (0013, part 3)

- A separate process in `distiller/`. It must not import from `memories` or `config`; it reaches Memento only through MCP, with its own `Client` token.
- Every 15 minutes: read `inbox`, distil each capture into entries with exact excerpts, and close it. Its instructions are the skill.
- The model is configurable; the provider key lives with the distiller, never the server.
- Deploy as a Render cron job.

**Done when:** Pocket notes and raw-only chat captures are structured within 15 minutes without anyone opening a client, and the distiller passes the eval bar.

## M8. OAuth 2.1 for claude.ai and ChatGPT

- Authorisation server via django-oauth-toolkit: authorisation code + PKCE, protected-resource metadata, authorisation-server metadata, dynamic client registration. Client ID metadata documents when both clients support them.
- OAuth clients become `Client` rows, with scopes `memento:read`, `memento:write` and `memento:forget`.
- Connect claude.ai (web and mobile) and ChatGPT. Run the evals on each, with and without the skill, and set each client's `mode` from the results.

**Done when:** both clients connect, every client has a recorded eval result and a mode, and no client in `direct` mode is below the bar.

## M9 and M10. Morning email and read-only timeline

Blocked on Phase 4 design. The services already exist (`due_reminders()`, `inbox_count()`, `recall()`, `timeline()`).

- M9: daily cron job sending due reminders, waiting inbox items, and "on this day". No model involved.
- M10: a read-only web timeline for browsing and auditing entries, digests and their citations, with each entry's client and model visible.

## Day-30 review

Against the kill criteria in `docs/brief.md`. Also decide: semantic search, a `measures` field for numbers, and whether any client should change mode.
