# 0023. Claude Desktop starts Memento itself

Status: accepted, 25 September 2026. Amends 0020 on how Claude Desktop connects.

## Context

On the Mac, Claude Desktop reached Memento over HTTP through `mcp-remote`, with a bearer token kept in a file. On the owner's Mac the token was refused, `mcp-remote` fell back to looking for OAuth, and Memento was unreachable. It had too many parts for one person on one computer, and the owner asked for it to be simpler.

## Decision

**Claude Desktop runs Memento's tools over stdio.** `manage.py mcp_stdio` serves the same nine tools, with the same descriptions and services, to the process that started it. `make connect-desktop` writes Claude Desktop's settings to run it with `uv`. There is no network, no token and no `mcp-remote`.

**No token, because none protects anything here.** Whoever can start `mcp_stdio` can already read the database and `.env`. Entries are still recorded against a `claude-desktop` client, which has read, write and forget, so receipts, "don't log that" and `client_name` all work as before. That client's token is sealed: it is replaced with one nobody is shown, and older Claude Desktop clients are revoked. So no token left over from the HTTP connector still works. Forgetting still previews and waits for a yes.

**HTTP stays.** `/mcp` with bearer tokens is still how the distiller, the evals and, once deployed, the web clients connect (0016). This changes only Claude Desktop on the Mac.

## Consequences

- Claude Desktop needs the database (Docker) running, but not the server. The server is still needed for the Pocket pull and the distiller.
- `make connect-desktop` is safe to rerun. It keeps other servers in Claude Desktop's settings and backs up the old file.
