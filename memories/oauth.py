"""
Memento's OAuth 2.1 authorization server (M8, 0025).

claude.ai and ChatGPT connect from their own servers and will not send a static
bearer token, so Memento issues tokens instead. It is its own authorization
server: one user, one issuer, no third party holding the keys to a memory store.

What the MCP authorization spec (revision 2026-07-28) requires of us, and where
it lives:

- RFC 9728 protected resource metadata, and `resource_metadata` on every 401,
  so a client can find this server at all. In `mcp_server.py` for the header,
  `protected_resource_metadata` here for the document.
- RFC 8414 authorization server metadata.
- Client ID Metadata Documents, the spec's preferred registration: the client_id
  *is* an https URL we fetch. `resolve_metadata_document`.
- Dynamic Client Registration (RFC 7591), deprecated but still the fallback, and
  what today's clients are likeliest to use. `register`.
- Authorization code with PKCE, S256 only.
- RFC 8707: the client names the resource it wants a token for, and we refuse to
  accept a token issued for anything else. `verify_access_token`.
- RFC 9207: the `iss` parameter on the authorization response.

Tokens are hashed at rest, as bearer tokens are (0016). An issued token resolves
to a `Client` row, so provenance (`client_name`) and scope checks work exactly as
they already do — the MCP tools cannot tell the two credential kinds apart.
"""

import base64
import hashlib
import ipaddress
import json
import secrets
import socket
import urllib.error
import urllib.request
from datetime import timedelta
from urllib.parse import urlencode, urlparse, urlunparse

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata

from .models import Client, OAuthClient, OAuthGrant, OAuthToken, Scope

# The minimal set for basic use. `memento:forget` is deliberately absent: the
# spec says scopes_supported should be the minimum for basic functionality, and
# deleting memories is not that (Principle 5). A client that needs it is told so
# by a 403 step-up challenge, and the owner grants it on the consent screen.
BASIC_SCOPES = [Scope.READ, Scope.WRITE]
ALL_SCOPES = [Scope.READ, Scope.WRITE, Scope.FORGET]

TOKEN_PREFIX = "memento_oauth_"
CIMD_TIMEOUT = 5
CIMD_MAX_BYTES = 64 * 1024
CIMD_CACHE = timedelta(hours=24)


def issuer() -> str:
    return settings.MEMENTO_BASE_URL


def resource() -> str:
    return settings.MEMENTO_RESOURCE_URL


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# --- discovery ---------------------------------------------------------------


@require_GET
def protected_resource_metadata(request):
    """RFC 9728. What a client reads first, having followed the 401's resource_metadata."""
    doc = ProtectedResourceMetadata(
        resource=resource(),
        authorization_servers=[issuer()],
        scopes_supported=[str(s) for s in BASIC_SCOPES],
        resource_name="Memento",
        bearer_methods_supported=["header"],
    )
    return _metadata_response(doc)


@require_GET
def authorization_server_metadata(request):
    """RFC 8414. `issuer` is compared by exact string, so it comes from one setting."""
    doc = OAuthMetadata(
        issuer=issuer(),
        authorization_endpoint=f"{issuer()}/oauth/authorize",
        token_endpoint=f"{issuer()}/oauth/token",
        registration_endpoint=f"{issuer()}/oauth/register",
        revocation_endpoint=f"{issuer()}/oauth/revoke",
        scopes_supported=[str(s) for s in ALL_SCOPES],
        response_types_supported=["code"],
        grant_types_supported=["authorization_code", "refresh_token"],
        token_endpoint_auth_methods_supported=["none", "client_secret_post"],
        code_challenge_methods_supported=["S256"],
        # Both advertisements are load-bearing: a client picks its registration
        # mechanism from the first, and decides whether a missing `iss` is an
        # error from the second (RFC 9207 §2.4).
        client_id_metadata_document_supported=True,
        authorization_response_iss_parameter_supported=True,
    )
    return _metadata_response(doc)


def _metadata_response(doc) -> JsonResponse:
    response = JsonResponse(doc.model_dump(mode="json", exclude_none=True))
    # Discovery is fetched cross-origin by browser-based clients.
    response["Access-Control-Allow-Origin"] = "*"
    response["Cache-Control"] = "public, max-age=3600"
    return response


# --- client registration -----------------------------------------------------


@csrf_exempt
@require_POST
def register(request):
    """
    Dynamic Client Registration (RFC 7591). Deprecated by the spec in favour of
    metadata documents, and kept because that deprecation is newer than the
    clients. Unauthenticated by design: the owner is not involved until consent.
    """
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _oauth_error("invalid_client_metadata", "Send a JSON body.", status=400)
    uris = body.get("redirect_uris") or []
    if not isinstance(uris, list) or not uris:
        return _oauth_error(
            "invalid_redirect_uri",
            "redirect_uris must be a non-empty list of absolute URIs.",
            status=400,
        )
    try:
        uris = [_check_redirect_uri(u) for u in uris]
    except ValidationError as e:
        return _oauth_error("invalid_redirect_uri", e.message, status=400)

    client = OAuthClient.objects.create(
        client_id=f"memento-client-{secrets.token_urlsafe(18)}",
        client_name=str(body.get("client_name") or "")[:128],
        client_uri=str(body.get("client_uri") or "")[:512],
        redirect_uris=uris,
    )
    return JsonResponse(
        {
            "client_id": client.client_id,
            "client_id_issued_at": int(client.registered_at.timestamp()),
            "redirect_uris": client.redirect_uris,
            "client_name": client.client_name,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        status=201,
    )


def _check_redirect_uri(value: str) -> str:
    """
    A redirect URI must be absolute and carry no fragment. http is allowed only
    for loopback, which is how a desktop client receives its callback.
    """
    parsed = urlparse(str(value))
    if not parsed.scheme or not parsed.netloc or parsed.fragment:
        raise ValidationError(f"{value} is not an absolute URI without a fragment.")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValidationError(f"{value} must use https, or http only on loopback.")
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError(f"{value} must use https, or http only on loopback.")
    return str(value)


def resolve_metadata_document(client_id: str) -> OAuthClient:
    """
    Client ID Metadata Documents: the client_id is an https URL serving its own
    metadata, so nothing is registered in advance and the id is portable.

    We fetch it, which means this server makes an outbound request to an address
    a stranger chose. That is the price of the mechanism, so the fetch is
    restricted: https only, public addresses only, one redirect-free GET, a
    timeout, and a size cap.
    """
    stored = OAuthClient.objects.filter(client_id=client_id).first()
    now = timezone.now()
    if stored and stored.metadata_fresh_until and stored.metadata_fresh_until > now:
        return stored

    document = _fetch_metadata_document(client_id)
    if document.get("client_id") != client_id:
        raise ValidationError("The document's client_id must equal the URL it was fetched from.")
    uris = document.get("redirect_uris") or []
    if not isinstance(uris, list) or not uris:
        raise ValidationError("The document must list at least one redirect_uris entry.")
    uris = [_check_redirect_uri(u) for u in uris]
    name = str(document.get("client_name") or "")[:128]
    if not name:
        raise ValidationError("The document must include client_name.")

    fields = {
        "client_name": name,
        "client_uri": str(document.get("client_uri") or "")[:512],
        "redirect_uris": uris,
        "from_metadata_document": True,
        "metadata_fresh_until": now + CIMD_CACHE,
    }
    if stored:
        for key, value in fields.items():
            setattr(stored, key, value)
        stored.save(update_fields=[*fields])
        return stored
    return OAuthClient.objects.create(client_id=client_id, **fields)


def is_metadata_document_id(client_id: str) -> bool:
    """The draft requires an https URL with a path, which is what tells the two kinds apart."""
    parsed = urlparse(client_id)
    return parsed.scheme == "https" and bool(parsed.netloc) and parsed.path not in {"", "/"}


def _fetch_metadata_document(url: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValidationError("A client_id document URL must be https.")
    _refuse_private_address(parsed.hostname)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "Memento"}
    )
    try:
        with opener.open(request, timeout=CIMD_TIMEOUT) as response:
            raw = response.read(CIMD_MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ValidationError(f"Could not fetch {url}: {e}") from e
    if len(raw) > CIMD_MAX_BYTES:
        raise ValidationError(f"{url} returned more than {CIMD_MAX_BYTES} bytes.")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValidationError(f"{url} did not return JSON.") from e
    if not isinstance(document, dict):
        raise ValidationError(f"{url} did not return a JSON object.")
    return document


def _refuse_private_address(hostname: str) -> None:
    """Every address the name resolves to must be public, or the fetch is an SSRF."""
    try:
        infos = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValidationError(f"Could not resolve {hostname}.") from e
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValidationError(f"{hostname} resolves to a non-public address.")


def _load_client(client_id: str) -> OAuthClient:
    if is_metadata_document_id(client_id):
        return resolve_metadata_document(client_id)
    client = OAuthClient.objects.filter(client_id=client_id).first()
    if client is None:
        raise ValidationError("Unknown client_id. Register first, or use a metadata document URL.")
    return client


# --- authorization -----------------------------------------------------------


@login_required
def authorize(request):
    """
    The authorization endpoint. Behind Django's login, so the owner authenticates
    with their own admin credentials and nothing else holds them.

    Errors before the redirect URI is known are shown to the user; after it is
    known they go back to the client, as OAuth requires.
    """
    params = request.POST if request.method == "POST" else request.GET
    client_id = params.get("client_id", "")
    try:
        oauth_client = _load_client(client_id)
    except ValidationError as e:
        return _authorize_problem(request, e.message)

    redirect_uri = params.get("redirect_uri", "")
    if redirect_uri not in oauth_client.redirect_uris:
        # Never redirect to an unregistered URI: that is the open-redirect hole.
        return _authorize_problem(
            request, "redirect_uri is not one this client registered.", client=oauth_client
        )

    state = params.get("state") or ""
    if params.get("response_type") != "code":
        return _redirect_error(redirect_uri, "unsupported_response_type", state)
    challenge = params.get("code_challenge") or ""
    if not challenge or params.get("code_challenge_method", "S256") != "S256":
        return _redirect_error(
            redirect_uri,
            "invalid_request",
            state,
            "PKCE with code_challenge_method=S256 is required.",
        )
    asked = params.get("resource") or ""
    if asked and not _same_resource(asked, resource()):
        return _redirect_error(
            redirect_uri, "invalid_target", state, f"This server issues tokens for {resource()}."
        )

    requested = _requested_scopes(params.get("scope"))
    if request.method != "POST":
        return render(
            request,
            "oauth/consent.html",
            {
                "oauth_client": oauth_client,
                "scopes": [(str(s), Scope(s).label) for s in requested],
                "params": {
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "response_type": "code",
                    "scope": " ".join(str(s) for s in requested),
                    "resource": asked,
                },
            },
        )

    if params.get("decision") != "allow":
        return _redirect_error(redirect_uri, "access_denied", state, "The owner declined.")

    granted = [s for s in requested if params.get(f"scope_{s.split(':')[1]}") == "on"] or requested
    client = _client_row(request.user, oauth_client, granted)
    code = secrets.token_urlsafe(32)
    OAuthGrant.objects.create(
        code_hash=_hash(code),
        oauth_client=oauth_client,
        client=client,
        redirect_uri=redirect_uri,
        code_challenge=challenge,
        scopes=granted,
        resource=asked or resource(),
    )
    query = {"code": code, "iss": issuer()}
    if state:
        query["state"] = state
    return redirect(_with_query(redirect_uri, query))


def _requested_scopes(raw: str | None) -> list[str]:
    asked = [s for s in (raw or "").split() if s]
    known = [s for s in asked if s in set(ALL_SCOPES)]
    return known or [str(s) for s in BASIC_SCOPES]


def _client_row(user, oauth_client: OAuthClient, scopes: list[str]) -> Client:
    """
    The Client row an OAuth token acts as. One per (owner, OAuth client), so
    provenance names the client the owner recognises, and the scopes recorded are
    the ones they just granted. Its token is sealed: no bearer token can act as
    this client (as for Claude Desktop over stdio, 0023).
    """
    from . import services

    name = (oauth_client.client_name or urlparse(oauth_client.client_id).netloc or "oauth")[:64]
    client = Client.objects.filter(owner=user, name=name).first()
    if client is None:
        client, _ = services.create_client(user, name, scopes=scopes)
        services.seal_client(client)
    if sorted(client.scopes) != sorted(scopes) or client.revoked_at:
        client.scopes, client.revoked_at = scopes, None
        client.save(update_fields=["scopes", "revoked_at"])
    return client


def _authorize_problem(request, message: str, client: OAuthClient | None = None) -> HttpResponse:
    return render(
        request,
        "oauth/problem.html",
        {"message": message, "oauth_client": client},
        status=400,
    )


def _redirect_error(redirect_uri: str, error: str, state: str, description: str = ""):
    query = {"error": error, "iss": issuer()}
    if description:
        query["error_description"] = description
    if state:
        query["state"] = state
    return redirect(_with_query(redirect_uri, query))


def _with_query(uri: str, query: dict) -> str:
    parsed = urlparse(uri)
    existing = parsed.query
    merged = f"{existing}&{urlencode(query)}" if existing else urlencode(query)
    return urlunparse(parsed._replace(query=merged))


def _same_resource(asked: str, ours: str) -> bool:
    """RFC 8707 compares resource identifiers with the scheme and host case-folded."""

    def norm(value: str) -> str:
        parsed = urlparse(value.rstrip("/"))
        return urlunparse(
            parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower())
        )

    return norm(asked) == norm(ours)


# --- tokens ------------------------------------------------------------------


@csrf_exempt
@require_POST
def token(request):
    """The token endpoint. Public clients, so PKCE is what proves the caller."""
    grant_type = request.POST.get("grant_type")
    if grant_type == "authorization_code":
        return _exchange_code(request)
    if grant_type == "refresh_token":
        return _exchange_refresh(request)
    return _oauth_error(
        "unsupported_grant_type", "Use authorization_code or refresh_token.", status=400
    )


def _exchange_code(request):
    code = request.POST.get("code") or ""
    grant = (
        OAuthGrant.objects.select_related("oauth_client", "client__owner")
        .filter(code_hash=_hash(code))
        .first()
    )
    if grant is None:
        return _oauth_error("invalid_grant", "Unknown or already-used code.", status=400)
    if grant.used_at or timezone.now() - grant.created_at > OAuthGrant.LIFETIME:
        # A replayed code is a sign of theft, so drop every token from it too.
        OAuthToken.objects.filter(oauth_client=grant.oauth_client, client=grant.client).update(
            revoked_at=timezone.now()
        )
        grant.delete()
        return _oauth_error("invalid_grant", "That code has expired or been used.", status=400)
    if request.POST.get("client_id") != grant.oauth_client.client_id:
        return _oauth_error("invalid_client", "client_id does not match the code.", status=401)
    if request.POST.get("redirect_uri") != grant.redirect_uri:
        return _oauth_error("invalid_grant", "redirect_uri does not match the request.", status=400)
    verifier = request.POST.get("code_verifier") or ""
    if not _pkce_matches(verifier, grant.code_challenge):
        return _oauth_error("invalid_grant", "code_verifier does not match.", status=400)

    grant.used_at = timezone.now()
    grant.save(update_fields=["used_at"])
    return _issue(grant.oauth_client, grant.client, grant.scopes, grant.resource)


def _exchange_refresh(request):
    presented = request.POST.get("refresh_token") or ""
    stored = (
        OAuthToken.objects.select_related("oauth_client", "client__owner")
        .filter(token_hash=_hash(presented), use=OAuthToken.Use.REFRESH)
        .first()
    )
    if stored is None or stored.revoked_at or stored.expires_at <= timezone.now():
        return _oauth_error("invalid_grant", "That refresh token is not usable.", status=400)
    if request.POST.get("client_id") != stored.oauth_client.client_id:
        return _oauth_error("invalid_client", "client_id does not match the token.", status=401)
    asked = _requested_scopes(request.POST.get("scope")) if request.POST.get("scope") else None
    if asked and not set(asked) <= set(stored.scopes):
        return _oauth_error("invalid_scope", "A refresh cannot widen scope.", status=400)
    # Rotate: the presented token is spent, as OAuth 2.1 requires of public clients.
    stored.revoked_at = timezone.now()
    stored.save(update_fields=["revoked_at"])
    return _issue(stored.oauth_client, stored.client, asked or stored.scopes, stored.resource)


def _issue(oauth_client: OAuthClient, client: Client, scopes: list[str], for_resource: str):
    now = timezone.now()
    access = TOKEN_PREFIX + secrets.token_urlsafe(32)
    refresh = TOKEN_PREFIX + secrets.token_urlsafe(32)
    OAuthToken.objects.create(
        token_hash=_hash(access),
        use=OAuthToken.Use.ACCESS,
        oauth_client=oauth_client,
        client=client,
        scopes=scopes,
        resource=for_resource or resource(),
        expires_at=now + OAuthToken.ACCESS_LIFETIME,
    )
    OAuthToken.objects.create(
        token_hash=_hash(refresh),
        use=OAuthToken.Use.REFRESH,
        oauth_client=oauth_client,
        client=client,
        scopes=scopes,
        resource=for_resource or resource(),
        expires_at=now + OAuthToken.REFRESH_LIFETIME,
    )
    response = JsonResponse(
        {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": int(OAuthToken.ACCESS_LIFETIME.total_seconds()),
            "refresh_token": refresh,
            "scope": " ".join(scopes),
        }
    )
    response["Cache-Control"] = "no-store"
    return response


def _pkce_matches(verifier: str, challenge: str) -> bool:
    if not verifier or not challenge:
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return secrets.compare_digest(computed, challenge)


@csrf_exempt
@require_POST
def revoke(request):
    """RFC 7009. Always 200, so a caller learns nothing from probing."""
    presented = request.POST.get("token") or ""
    OAuthToken.objects.filter(token_hash=_hash(presented), revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )
    return HttpResponse(status=200)


def verify_access_token(presented: str) -> tuple[Client, list[str]] | None:
    """
    The live Client an access token acts as, and the scopes it was granted.

    The audience check is the point: a token minted for another resource must not
    work here even if it is otherwise valid (RFC 8707, and the confused-deputy
    problem it exists to prevent).
    """
    if not presented.startswith(TOKEN_PREFIX):
        return None
    stored = (
        OAuthToken.objects.select_related("client__owner")
        .filter(
            token_hash=_hash(presented),
            use=OAuthToken.Use.ACCESS,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
            client__revoked_at__isnull=True,
            client__owner__is_active=True,
        )
        .first()
    )
    if stored is None:
        return None
    if not _same_resource(stored.resource, resource()):
        return None
    return stored.client, list(stored.scopes)


def _oauth_error(error: str, description: str, *, status: int) -> JsonResponse:
    response = JsonResponse({"error": error, "error_description": description}, status=status)
    response["Cache-Control"] = "no-store"
    return response


def unauthorised_headers(*, error: str = "invalid_token", scopes: list[str] | None = None) -> str:
    """
    The WWW-Authenticate challenge. `resource_metadata` is how a client discovers
    this authorization server at all, so a 401 without it is a dead end — which
    is exactly how Memento looked to claude.ai before M8.
    """
    parts = [
        'Bearer realm="memento"',
        f'error="{error}"',
        f'resource_metadata="{issuer()}/.well-known/oauth-protected-resource/mcp"',
    ]
    if scopes:
        parts.append(f'scope="{" ".join(scopes)}"')
    return ", ".join(parts)
