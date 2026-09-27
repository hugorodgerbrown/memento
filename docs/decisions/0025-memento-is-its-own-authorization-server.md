# 0025. Memento is its own authorization server

Status: accepted, 27 September 2026. Supersedes the second half of [0005](0005-auth-staging.md) (the choice of django-oauth-toolkit). Implements M8.

## Context

claude.ai and ChatGPT connect from Anthropic's and OpenAI's servers, and neither will send a static bearer token: their connector settings offer OAuth and nothing else. Before this, an unauthenticated call to `/mcp` returned a bare `WWW-Authenticate: Bearer realm="memento", error="invalid_token"` and every OAuth discovery path returned 404, so those clients had no way in at all. That is also the wall `mcp-remote` hit on the owner's Mac, which is what 0023 recorded.

0005 chose django-oauth-toolkit for this. Two things have changed since.

The MCP authorization spec moved. Revision 2026-07-28 **requires** RFC 9728 protected resource metadata, **deprecates** Dynamic Client Registration in favour of **Client ID Metadata Documents**, requires RFC 8707 resource indicators with audience validation, and says the authorization server SHOULD return RFC 9207's `iss`. M8's plan was written against the older shape and led with DCR.

And the MCP Python SDK now ships an authorization server: `mcp/server/auth/` has handlers for authorize, token, register and revoke, both metadata documents, and bearer middleware.

## Decision

**Memento is its own authorization server.** One user, one issuer. No third party is introduced to hold the keys to a memory store, and no account is created anywhere else.

**Not django-oauth-toolkit.** It implements none of the MCP-specific parts — RFC 9728, registration, resource indicators — so they would be hand-written anyway, on top of a second token system living beside the `Client` bearer tokens from 0016. The endpoints are Django views in `memories/oauth.py`, using the SDK's pydantic metadata models so the documents are shaped by the spec rather than by us.

**Endpoints are Django's, not the SDK's routes.** The authorization endpoint needs the owner to log in and consent, which means Django's auth, sessions, CSRF and templates. Mounting Starlette routes beside Django for one step of one flow buys nothing, and everything stays testable with Django's test client.

**An OAuth token resolves to a `Client` row, found by the OAuth client and never by its name.** Registration is unauthenticated, so the owner is unknown until they consent; at that moment one `Client` is created per (owner, OAuth client), with the scopes just granted and a sealed token so no bearer token can act as it (as for Claude Desktop over stdio, 0023). Provenance (`client_name`) and every scope check therefore work unchanged, and the nine tools cannot tell the two credential kinds apart.

**A `client_name` is a label, not an identity.** Whoever registers chooses it, unauthenticated, so a stranger can ask to be called `claude-code`. Keying the `Client` row on the name would let them land on the row an existing bearer-token client already uses — inheriting its live token, rewriting its scopes, and lifting its revocation. So the row is keyed on the OAuth client, a colliding display name is disambiguated rather than merged (an entry's `client_name` must not be ambiguous about who wrote it), and the row is always created here and always sealed. Re-consent updates that client's own row, including lifting a revocation the owner chose to lift by consenting again while logged in; it can never reach another client's row.

**An empty consent is a refusal.** If the owner clears every box and presses Allow, nothing is granted and the client is told `access_denied`. Treating an empty selection as "everything asked for" would hand over `memento:forget` at exactly the moment the owner had unticked it.

**Both registration mechanisms.** Client ID Metadata Documents, because the spec prefers them and an https client_id needs nothing stored in advance; and Dynamic Client Registration, because the deprecation is newer than the clients and is likelier than not to be what they still use. Both are advertised, and a client picks.

**`memento:forget` is not a basic scope.** `scopes_supported` is read and write only. The spec says that field should carry the minimum needed for basic functionality, and deleting memories is not that (Principle 5). The consent screen lists `forget` as an unticked box with a plain warning, so granting deletion is a deliberate act by the owner rather than a default.

**Tokens are audience-bound.** A token records the `resource` it was issued for and is refused here if that is not this server, which is what RFC 8707 exists to prevent. The authorization endpoint refuses to issue a token for anyone else's resource.

**Fetching a Client ID Metadata Document is restricted.** It means this server makes an outbound request to an address a stranger chose. So: https only, port 443 only, every resolved address must be globally routable, no redirects, a five-second timeout, a 64KB cap, and the document's `client_id` must equal the URL it came from.

**And the address that was checked is the address that is used.** Resolving the name to check it and then handing the *name* to an HTTP library lets it resolve again — a record with a short TTL can answer publicly for the check and internally for the fetch, which is DNS rebinding and defeats the check entirely. So the validated IP is pinned and connected to directly, with SNI and `Host` set to the hostname so TLS still verifies. Fetch failures are logged and reported generically, because the precise reason one host refused a connection is not something this server should read back to whoever asked.

## What a review of this changed

The first implementation was reviewed before it went live, and four things were wrong in ways worth recording, because each was a case of the code not doing what this document says.

1. **The `Client` row was keyed on `client_name`.** An unauthenticated registration under an existing client's name would have taken over its row: inherited its unsealed bearer token, rewritten its scopes, and cleared its `revoked_at`. A leaked, revoked bearer token could have been brought back to life by getting the owner to approve a lookalike.
2. **Unticking every box granted everything.** An `or requested` fallback meant an empty selection fell back to the full request, including `memento:forget`.
3. **The metadata-document fetch was open to DNS rebinding.** The address was checked and then re-resolved by the HTTP library.
4. **The refresh endpoint did not check whether the client was still live**, so revoking a client left its refresh chain rotating.

A second review found four more.

5. **`@login_required` pointed at a login that does not exist.** Django's default `LOGIN_URL` is `/accounts/login/`, and only the admin's login is mounted, so the first authorization any client ever started would have ended at a 404. Caught only by review because every test and every manual run happened to log in first.
6. **A token's scopes were not what the tools checked.** `verify_access_token` returned the token's own grant and the caller dropped it, so authorization used the shared `Client` row instead. A refresh narrowed to `memento:read` still carried the client's `memento:forget`. The scopes now travel with the request, and a bearer token's scopes are its client's, as before.
7. **Refresh-token reuse did not revoke the family.** A spent token coming back is the signal it was copied, but it was simply not found, so the thief's replacement chain survived while the legitimate client got an error. Tokens now share a family, and reuse revokes all of it — both parties re-authorize, which is the safe outcome.
8. **Codes and rotations were claimed with a read and then a write.** Two requests arriving together could both pass the check. Both are now one conditional update.

All eight are fixed and each has a test named for it. The lesson recorded here: on this surface, the tests must assert the security property rather than the happy path — every one of these passed a suite that only checked the flow worked.

## Consequences

- **Memento is now reachable by any spec-compliant MCP client**, not only ones that can send a custom header. claude.ai, ChatGPT and Claude Desktop's own connector settings become possible; 0023's stdio connection stays the right answer for a local development database.
- **A 401 from `/mcp` now carries `resource_metadata` and `scope`.** That is the link that was missing, and it is what turns a dead end into a discoverable server.
- **Two gaps against the spec, both deliberate and recorded.**
  - There is no HTTP 403 `insufficient_scope` step-up challenge. A tool called without the scope for it still returns a tool error, because that error is read by a model that can act on it and say so to the user ("Errors teach"), whereas a 403 aborts the request mid-session. Revisit if a client turns out to re-authorize usefully on one.
  - `private_key_jwt` client authentication is not supported. Both clients are public clients using PKCE.
- **The issuer is now load-bearing configuration.** `MEMENTO_BASE_URL` is the issuer and the base of the resource identifier; registered clients remember both, and RFC 8414 compares issuers by exact string. Production refuses to start without it, rather than deriving it from whichever host a request arrived on. Changing it after clients have registered means they must register again — which is why the custom domain was settled before any of this shipped (0024).
- **The owner can revoke, and revocation reaches renewal.** The consent screen says so, so `OAuthToken` is in the admin with a revoke action, and revoking the `Client` row stops every token that acts as it — refresh included. The refresh endpoint checks the same liveness as the access path; without that a revoked client could rotate a fresh ninety-day token indefinitely and revocation would not be a kill switch at all.
- **What claude.ai and ChatGPT actually require is still unverified.** Nothing readable settles whether they use metadata documents or registration today. Both are implemented and advertised, so either works; which one they choose is recorded when they first connect.
