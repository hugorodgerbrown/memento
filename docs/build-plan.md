# Build plan (Phase 5)

Each milestone ends in something usable and a green CI. Don't start a milestone until the previous one meets its acceptance criteria. Decisions referenced as 00NN are in `docs/decisions/`.

## Now: deployed (0024)

Deployment has resumed and M1 has landed. The real Memento runs on Render as `memento` (not staging: there is one environment, and it holds real memories from day one), and the Mac becomes the development environment. **M1 and M8 are unparked**, and M8 — OAuth, so claude.ai and ChatGPT can connect — is the next milestone after M1's deploy is green. `docs/deploy.md` is the runbook; `docs/local.md` is now the development setup.

Pocket stays on the pull, on the Mac, and so writes to the development database: connecting it to the real Memento is deliberately a separate step (0024), after 0021's first dry run against the real account.

## M1. Deployable skeleton on Render

- Web service and Render Postgres 16, configured from environment variables.
- Migrations run before each deploy; `collectstatic` runs at build.
- Health check on `/healthz`; admin reachable over HTTPS.
- Switch serving to ASGI (uvicorn workers) in preparation for M2.

**Done when:** a push to `main` deploys, `/healthz` returns `ok`, and you can log in to the admin.

**Done, 27 Sep 2026.** The blueprint is applied: `memento`, `memento-db` and
`memento-distiller` in Frankfurt. Build 52s, migrations succeeded as their own
pre-deploy step, `/healthz` answers `ok` (so Postgres is reachable), `/mcp`
answers `401` to an unauthenticated call, `/ingest/pocket/` answers `401` as
intended, the admin serves over HTTPS with hashed static files, and the superuser
and Profile exist. Render assigned `memento-ru31.onrender.com`.

Outstanding, and not blocking: the custom domain. `domains:` in the blueprint did
not create it, so `memento-app.me` needs adding on the service's Custom Domains
page and its registrar records replacing.

## M2. MCP server and client registry

- Streamable HTTP endpoint at `/mcp`, using the official Python MCP SDK mounted in Django's ASGI app.
- A `Client` model: owner, name (becomes `client_name` in provenance), hashed bearer token, scopes, and `mode` (`direct` by default, or `inbox`; see 0013).
- The nine tools from `docs/mcp-tools.md`, each a thin wrapper over `services.py`, with the exact descriptions, schema field descriptions, annotations and result shapes in the spec.
- `forget` enforces preview-then-confirm, except `services.is_simple_undo()`.
- `inbox` carries context (0014): with each capture, a bounded set of the owner's current entries that it might update, so a client can supersede instead of duplicating.
- `timeline` and `list_tags` say that counts measure mentions, not occurrences; `recall` forbids inferring that something didn't happen from the absence of an entry (0014).
- Server `instructions` are sent too, for clients that read them.

**Done when:** contract tests call every tool through the MCP layer; a test fails if `remember`'s description exceeds 500 characters; Claude Code can log and recall against staging.

Built (0016): `/mcp` serves the nine tools, stateless, with per-client bearer tokens from `manage.py create_client`. Contract tests cover every tool in-process and over HTTP, and a test compares each description with `docs/mcp-tools.md`. Outstanding: Claude Code against the deploy, which needs M1.

## M3. The contract (0013, part 1)

- Move the time zone from `PocketLink` to a per-user profile, with a data migration.
- Every tool result includes `now` in the user's time zone.
- `remember` rejects a claim containing relative time words, and a `memory` dated in the future. The errors state today's date in the user's time zone.
- `remember` warns when a new tag is within one edit of an existing tag, or differs only by plural.
- `remember` accepts `raw_text` alone and stores it as a `Capture` with `source="chat"` in the inbox. Every validation error offers this as the way out. Clients in `inbox` mode always take this path, with any derived fields kept as hints.
- Generalise `Capture` for chat: a generated `external_id`, a single segment, and the exact-excerpt rule. The solo-voice rule stays Pocket-only. The duplicate guard covers raw-only captures.

**Done when:** each rule has a test named for it, including a weak-client simulation: repeated invalid calls end in an inbox capture, never a lost one.

Built (0017), ahead of M1's deploy by choice: `Profile` holds the time zone; every result carries `now`; `remember` rejects relative time in claims and memories dated ahead (a scheduled `change` excepted, per 0009), warns on near-duplicate tags, and keeps raw-only saves and inbox-mode clients' words as chat captures. Tests are in `memories/tests/test_contract.py`. The tool-text changes have not had an eval run.

## M4. Pocket live (0021, 0024)

Now: the pull is built (`make pocket-pull`, 0021) and tested against Pocket's REST shape as a third-party SDK describes it. It runs on the Mac, so it writes to the development database, not to the real Memento. **Pocket therefore reaches nothing real until this milestone lands** (0024).

Done when a dry run against the real account matches expectations, real responses (redacted) replace the hand-written pull fixtures, the unconfirmed items in 0021 are settled, and Pocket is connected to the deploy — by the webhook, which is now possible and is the intended mechanism, with the pull retired to backfills (0021). If the webhook's unknowns bite, the fallback is the pull as a second Render cron job with `POCKET_API_KEY`.

- ~~Point Pocket's webhook at the real Memento's `/ingest/pocket/`~~ Done, 27 Sep 2026 (`docs/deploy.md`, 0026).
- Record real deliveries (with personal content redacted) as test fixtures: a solo note (done, 27 Sep 2026: `real_delivery`, including its label change), a conversation, a transcript edit, a deletion. Each delivery's shape is in the service log, without content.
- Resolve every Pocket item under "Verify before relying" in `CLAUDE.md`, and update `pocket.py` and its tests to match reality.
- Decide push versus pull now that Pocket ships an MCP server with a recency mode (`docs/first-recordings.md`). Push stays the plan unless the unknowns above bite.

**Done when:** fixtures from real payloads replace the hand-written ones and the suite passes.

## M5. Dogfood and baseline evals

- Use Memento daily from Claude Code for a week.
- Run `docs/evals/capture-policy.json` against Claude Code without any skill. Record the results in `docs/evals/results/`.

**Done when:** a baseline exists for every case, with failures categorised: dates, splitting, kinds, tags, verbatim, or policy.

Baseline recorded (24 Sep 2026): `evals/capture.py` (`make eval`) runs every case against Claude Code, isolated, on its own database. 45 of 54 runs passed (83%); failures and what they mean are in `docs/evals/results/2026-09-24-claude-code-no-skill-findings.md`. `past-midnight` passed 3/3 at 00:30 on 25 Sep, with and without the skill. Outstanding: the week of daily use. Finding 4, month precision refused by format, is fixed in the server by 0018.

## M6. The Memento skill (0013, part 2)

- Author `skill/memento/SKILL.md` in the open Agent Skills format: frontmatter name and description with a clear trigger, then worked examples (starting with the padel message), kinds and their edge cases, splitting rules, tag conventions, date resolution, correction versus change, and reading recipes for trend questions, monthly digests and citations.
- Include the plantar fasciitis thread from `docs/first-recordings.md`: recall before distilling, choose `change` over a second entry, and write as one unit whatever will later be updated as one unit, because `supersedes` is one-to-one (0014).
- Keep `SKILL.md` short; put long examples in `references/` so clients load them only when needed.
- Re-run the evals with the skill installed. Keep changes that improve the failure categories from M5, and revert changes that don't.
- Package it for distribution: a Claude plugin and an OpenAI plugin, each bundling the skill with the Memento connector where the platform allows.

**Done when:** the skill measurably beats the baseline on Claude Code, and the with/without results are recorded.

Skill built and measured (24 Sep 2026): `skill/memento` passes 54 of 54 runs on Claude Code against 45 of 54 for a same-day control, fixing every M5 failure category with no regressions (`docs/evals/results/2026-09-24-claude-code-skill-findings.md`). `make eval SKILL=1 MODEL=…` runs it. Its description was shortened to 197 characters to fit Claude Desktop's skill upload, with no loss on Claude Code (54/54 each, 25 Sep; `make skill-zip`). Outstanding: packaging as a Claude plugin and an OpenAI plugin.

## M7. The distiller (0013, part 3)

- A separate process in `distiller/`. It must not import from `memories` or `config`; it reaches Memento only through MCP, with its own `Client` token.
- Every 15 minutes: read `inbox`, distil each capture into entries with exact excerpts, and close it. Its instructions are the skill.
- The model is configurable; the provider key lives with the distiller, never the server.
- Deploy as a Render cron job.

**Done when:** Pocket notes and raw-only chat captures are structured within 15 minutes without anyone opening a client, and the distiller passes the eval bar.

Built (0019), not yet measured: `distiller/distil.py` runs the skill on `claude-sonnet-5` through the Tool Runner, over notes received in the last 35 minutes. It leaves for the user what it would ask about, and it is offered only six tools, never `forget`. `inbox` gained `received_since`. `render.yaml` describes the cron job. `make distil-eval` runs the capture-policy cases as inbox notes; its plumbing is verified against the real server with a scripted model. Outstanding: a real eval run (it needs `ANTHROPIC_API_KEY`), and the cron job going live with M1's deploy, where the key lives in the cron job's own environment (0024).

## M8. OAuth 2.1 for claude.ai and ChatGPT (server built, 0025)

- **The hostname is settled:** `memento-app.me`, decided before any client registered, so the issuer never has to move (0024).
- Authorisation server in Django, on the MCP SDK's metadata models rather than django-oauth-toolkit (0025): authorisation code + PKCE (S256 only), RFC 9728 protected-resource metadata, RFC 8414 authorisation-server metadata, RFC 8707 audience binding, RFC 9207 `iss`, and both registration mechanisms — Client ID Metadata Documents and dynamic client registration.
- OAuth clients become `Client` rows, with scopes `memento:read`, `memento:write` and `memento:forget`. `forget` is not advertised as basic and is an unticked box on the consent screen.
- Connect claude.ai (web and mobile) and ChatGPT. Run the evals on each, with and without the skill, and set each client's `mode` from the results.

**Done when:** both clients connect, every client has a recorded eval result and a mode, and no client in `direct` mode is below the bar.

Built (0025) and **claude.ai is connected** (27 Sep 2026). 76 tests in `memories/tests/test_oauth.py` cover the discovery chain, both registration mechanisms, PKCE, code replay, refresh rotation and reuse, audience binding, per-token scopes, and an OAuth token reaching the tools with provenance intact — while bearer tokens keep working (0016).

claude.ai used **Client ID Metadata Documents**, not dynamic registration: `client_id=https://claude.ai/oauth/mcp-oauth-client-metadata`, no call to `/oauth/register`. It requested `memento:read memento:write` only, so `forget` is not in its grant.

Two reviews of this surface found eight defects before it was used, each now fixed with a test named for it (0025). Outstanding: ChatGPT, then the eval runs on each client and setting each one's `mode` from the results.

## M9 and M10. Morning email and read-only timeline

Blocked on Phase 4 design. The services already exist (`due_reminders()`, `inbox_count()`, `recall()`, `timeline()`).

- M9: daily cron job sending due reminders, waiting inbox items, and "on this day". No model involved.
- M10: a read-only web timeline for browsing and auditing entries, digests and their citations, with each entry's client and model visible.

## Day-30 review

Against the kill criteria in `docs/brief.md`. Also decide: semantic search, a `measures` field for numbers, and whether any client should change mode.
