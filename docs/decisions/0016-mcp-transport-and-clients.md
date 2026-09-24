# 0016. The MCP endpoint: stateless HTTP beside Django, one token per client

Status: accepted, September 2026. Built in M2.

## Context

M2 puts the nine tools behind `/mcp`. Three choices shape how that behaves: how the MCP server shares a process with Django, how a request is tied to a user, and how `forget` enforces preview-then-confirm when the protocol gives the server no memory of the preview.

## Decision

**Transport.** The official Python SDK (`mcp` 2.x) serves streamable HTTP at `/mcp`, stateless, with JSON responses. `config/asgi.py` sends `/mcp` and lifespan events to the SDK's app and everything else to Django. Stateless because staging runs two workers: a session held in one worker's memory would be missing on the next request. Tools are async wrappers that run the sync service functions through `sync_to_async`.

**Clients.** A `Client` row per MCP client: owner, name, scopes, `mode`, and a SHA-256 hash of its bearer token. The token is shown once, by `manage.py create_client`, and revoked in the admin. The client's name is written as `client_name` on every entry, so provenance comes from the token, not from what the model says. `mode` is stored now and acted on in M3 (0013). OAuth (M8) changes how tokens are issued, not how a request is tied to a client.

**Scopes.** Each tool needs `memento:read`, `memento:write` or `memento:forget` (0005). A missing scope is a tool error that tells the model to tell the user.

**Forget.** The preview returns a `confirm_token`: the plan's ids, signed with `SECRET_KEY` and valid for an hour. A confirm must carry it, and it only matches the same plan, so if anything joins the plan in between, such as a digest citing the entry, the confirm is refused and the client previews again. The "don't log that" exception (`is_simple_undo`) needs no token.

**Tool text.** The 0014 wording ships here: `timeline` and `list_tags` say counts are mentions; `recall` says a missing entry means it wasn't mentioned; `save_digest` forbids stating frequencies the sources don't carry; `inbox` says it lists recent current entries. A test compares every description with `docs/mcp-tools.md`, so the spec and the code can't drift.

## Consequences

- `make run` serves through uvicorn, not `runserver`, so `/mcp` works locally.
- `/mcp` bypasses Django's middleware, so the transport's own DNS-rebinding check is keyed to `ALLOWED_HOSTS`, and connections are recycled by hand before each tool call.
- `forget`'s schema gains `confirm_token`, and its description now says to pass it back.
- The inbox context is the 25 most recent current entries, not those matching the note's tags as 0014 proposed: Pocket captures carry no tags. The result says how many it held back.
- The descriptions changed in this milestone and haven't been through an eval run (Principle 7). M5 records the baseline.
