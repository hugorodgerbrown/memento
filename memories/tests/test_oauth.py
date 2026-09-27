"""
OAuth 2.1 (M8, 0025). Tests are named for the rule they protect.

The MCP authorization spec is a chain: a 401 must point at the protected resource
metadata, which must point at the authorization server, which must let a client
register and then get a token bound to this resource. Break any link and
claude.ai sees a dead end, so each link has a test.
"""

import base64
import hashlib
import json
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

import httpx2
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from mcp import Client as MCPClient

from memories import oauth
from memories import services as s
from memories.mcp_server import http_app
from memories.models import Client, OAuthClient, OAuthGrant, OAuthToken, Scope

BASE = "https://memento-app.me"
RESOURCE = f"{BASE}/mcp"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "a" * 64


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


@override_settings(MEMENTO_BASE_URL=BASE, MEMENTO_RESOURCE_URL=RESOURCE)
class DiscoveryTests(TestCase):
    """The chain a client walks before it can ask for anything."""

    def test_protected_resource_metadata_names_this_server_and_its_issuer(self):
        body = self.client.get(reverse("oauth-protected-resource")).json()
        self.assertEqual(body["resource"], RESOURCE)
        self.assertEqual(body["authorization_servers"], [BASE])
        self.assertEqual(body["bearer_methods_supported"], ["header"])

    def test_forget_is_not_advertised_as_a_basic_scope(self):
        """Principle 5: deleting memories is not part of basic functionality."""
        body = self.client.get(reverse("oauth-protected-resource")).json()
        self.assertEqual(body["scopes_supported"], ["memento:read", "memento:write"])
        self.assertNotIn("memento:forget", body["scopes_supported"])

    def test_metadata_is_served_at_the_root_path_too(self):
        """Clients differ on whether they append the resource path, so both work."""
        root = self.client.get(reverse("oauth-protected-resource-root"))
        self.assertEqual(root.json()["resource"], RESOURCE)

    def test_authorization_server_metadata_advertises_what_we_actually_do(self):
        body = self.client.get(reverse("oauth-authorization-server")).json()
        self.assertEqual(body["issuer"], BASE)
        self.assertEqual(body["authorization_endpoint"], f"{BASE}/oauth/authorize")
        self.assertEqual(body["token_endpoint"], f"{BASE}/oauth/token")
        self.assertEqual(body["registration_endpoint"], f"{BASE}/oauth/register")
        self.assertEqual(body["code_challenge_methods_supported"], ["S256"])
        # A client picks its registration mechanism from the first flag and
        # decides whether a missing iss is an error from the second.
        self.assertTrue(body["client_id_metadata_document_supported"])
        self.assertTrue(body["authorization_response_iss_parameter_supported"])

    def test_issuer_has_no_trailing_slash(self):
        """RFC 8414 compares issuers by exact string; a stray slash breaks every client."""
        self.assertFalse(
            self.client.get(reverse("oauth-authorization-server")).json()["issuer"].endswith("/")
        )


@override_settings(MEMENTO_BASE_URL=BASE, MEMENTO_RESOURCE_URL=RESOURCE)
class RegistrationTests(TestCase):
    def test_dynamic_registration_returns_a_public_client(self):
        response = self.client.post(
            reverse("oauth-register"),
            data=json.dumps({"client_name": "Claude", "redirect_uris": [REDIRECT]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["token_endpoint_auth_method"], "none")
        self.assertEqual(body["redirect_uris"], [REDIRECT])
        self.assertTrue(OAuthClient.objects.filter(client_id=body["client_id"]).exists())

    def test_registration_refuses_a_redirect_uri_that_is_not_https_or_loopback(self):
        response = self.client.post(
            reverse("oauth-register"),
            data=json.dumps({"redirect_uris": ["http://evil.example.com/cb"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_redirect_uri")

    def test_registration_allows_loopback_for_desktop_clients(self):
        response = self.client.post(
            reverse("oauth-register"),
            data=json.dumps({"redirect_uris": ["http://127.0.0.1:33418/callback"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)

    def test_registration_needs_at_least_one_redirect_uri(self):
        response = self.client.post(
            reverse("oauth-register"), data=json.dumps({}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)


DOCUMENT_ID = "https://claude.ai/oauth/client-metadata.json"
DOCUMENT = {
    "client_id": DOCUMENT_ID,
    "client_name": "Claude",
    "redirect_uris": [REDIRECT],
}


@override_settings(MEMENTO_BASE_URL=BASE, MEMENTO_RESOURCE_URL=RESOURCE)
class MetadataDocumentTests(TestCase):
    """
    Client ID Metadata Documents: the spec's preferred registration, where the
    client_id is a URL this server fetches. Fetching a stranger's URL is an SSRF
    surface, so the restrictions are the interesting part.
    """

    def test_a_valid_document_registers_the_client(self):
        with mock.patch.object(oauth, "_fetch_metadata_document", return_value=DOCUMENT):
            client = oauth.resolve_metadata_document(DOCUMENT_ID)
        self.assertEqual(client.client_name, "Claude")
        self.assertTrue(client.from_metadata_document)
        self.assertEqual(client.redirect_uris, [REDIRECT])

    def test_a_document_whose_client_id_differs_from_its_url_is_refused(self):
        """The draft's core check: otherwise any URL could claim any identity."""
        wrong = {**DOCUMENT, "client_id": "https://elsewhere.example/other.json"}
        with (
            mock.patch.object(oauth, "_fetch_metadata_document", return_value=wrong),
            self.assertRaisesMessage(Exception, "must equal the URL"),
        ):
            oauth.resolve_metadata_document(DOCUMENT_ID)

    def test_a_document_without_a_name_is_refused(self):
        """The consent screen has to be able to say who is asking."""
        with (
            mock.patch.object(
                oauth, "_fetch_metadata_document", return_value={**DOCUMENT, "client_name": ""}
            ),
            self.assertRaisesMessage(Exception, "client_name"),
        ):
            oauth.resolve_metadata_document(DOCUMENT_ID)

    def test_a_cached_document_is_not_refetched(self):
        with mock.patch.object(oauth, "_fetch_metadata_document", return_value=DOCUMENT) as fetch:
            oauth.resolve_metadata_document(DOCUMENT_ID)
            oauth.resolve_metadata_document(DOCUMENT_ID)
        self.assertEqual(fetch.call_count, 1)

    def test_a_stale_document_is_refetched(self):
        with mock.patch.object(oauth, "_fetch_metadata_document", return_value=DOCUMENT) as fetch:
            oauth.resolve_metadata_document(DOCUMENT_ID)
            OAuthClient.objects.update(metadata_fresh_until=timezone.now() - timedelta(minutes=1))
            oauth.resolve_metadata_document(DOCUMENT_ID)
        self.assertEqual(fetch.call_count, 2)

    def test_a_url_resolving_to_a_private_address_is_refused(self):
        """Without this the fetch is a way to make Memento probe its own network."""
        with (
            mock.patch.object(
                oauth.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]
            ),
            self.assertRaisesMessage(Exception, "non-public address"),
        ):
            oauth._fetch_metadata_document("https://localhost.example/c.json")

    def test_an_ipv4_mapped_private_address_is_refused(self):
        """::ffff:169.254.169.254 must not slip past the check by wearing an IPv6 coat."""
        with (
            mock.patch.object(
                oauth.socket,
                "getaddrinfo",
                return_value=[(10, 1, 6, "", ("::ffff:169.254.169.254", 443, 0, 0))],
            ),
            self.assertRaisesMessage(Exception, "non-public address"),
        ):
            oauth._fetch_metadata_document("https://cloud.example/c.json")

    def test_one_bad_address_among_good_ones_is_refused(self):
        """Answering with a public address and an internal one must not pass."""
        with (
            mock.patch.object(
                oauth.socket,
                "getaddrinfo",
                return_value=[
                    (2, 1, 6, "", ("93.184.216.34", 443)),
                    (2, 1, 6, "", ("10.0.0.5", 443)),
                ],
            ),
            self.assertRaisesMessage(Exception, "non-public address"),
        ):
            oauth._fetch_metadata_document("https://mixed.example/c.json")

    def test_the_connection_goes_to_the_address_that_was_checked(self):
        """
        Resolving for the check and letting an HTTP library resolve again would let
        a short-TTL record answer public once and internal the second time (DNS
        rebinding). The address is pinned, so the socket opens to what passed.
        """
        with (
            mock.patch.object(
                oauth.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]
            ),
            mock.patch.object(
                oauth.socket, "create_connection", side_effect=OSError("stop here")
            ) as connect,
            self.assertRaisesMessage(Exception, "Could not fetch"),
        ):
            oauth._fetch_metadata_document("https://client.example/c.json")
        connect.assert_called_once()
        self.assertEqual(connect.call_args.args[0], ("93.184.216.34", 443))

    def test_a_document_url_on_another_port_is_refused(self):
        with self.assertRaisesMessage(Exception, "port 443"):
            oauth._fetch_metadata_document("https://client.example:8443/c.json")

    def test_only_an_https_url_with_a_path_is_treated_as_a_document_id(self):
        self.assertTrue(oauth.is_metadata_document_id(DOCUMENT_ID))
        self.assertFalse(oauth.is_metadata_document_id("https://claude.ai"))
        self.assertFalse(oauth.is_metadata_document_id("http://claude.ai/c.json"))
        self.assertFalse(oauth.is_metadata_document_id("memento-client-abc"))


@override_settings(MEMENTO_BASE_URL=BASE, MEMENTO_RESOURCE_URL=RESOURCE)
class AuthorizeTests(TestCase):
    def setUp(self):
        self.me = get_user_model().objects.create_user(username="sam", password="pw")
        self.oauth_client = OAuthClient.objects.create(
            client_id="memento-client-abc", client_name="Claude", redirect_uris=[REDIRECT]
        )
        self.client.force_login(self.me)

    def params(self, **overrides):
        base = {
            "client_id": self.oauth_client.client_id,
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge_for(VERIFIER),
            "code_challenge_method": "S256",
            "state": "xyz",
            "resource": RESOURCE,
        }
        return {**base, **overrides}

    def test_the_owner_must_be_logged_in(self):
        self.client.logout()
        response = self.client.get(reverse("oauth-authorize"), self.params())
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_consent_names_the_client_and_lists_what_it_could_do(self):
        response = self.client.get(reverse("oauth-authorize"), self.params())
        self.assertContains(response, "Claude")
        self.assertContains(response, "Read entries and the inbox")
        self.assertContains(response, "Save entries and close captures")

    def test_an_unregistered_redirect_uri_is_shown_not_redirected_to(self):
        """Redirecting an error to an unverified URI is the open-redirect hole."""
        response = self.client.get(
            reverse("oauth-authorize"), self.params(redirect_uri="https://evil.example/cb")
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("Location", response)

    def test_an_unknown_client_is_shown_not_redirected_to(self):
        response = self.client.get(reverse("oauth-authorize"), self.params(client_id="nope"))
        self.assertEqual(response.status_code, 400)

    def test_pkce_is_required(self):
        response = self.client.get(reverse("oauth-authorize"), self.params(code_challenge=""))
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(query["error"], ["invalid_request"])

    def test_plain_pkce_is_refused(self):
        """OAuth 2.1: S256 only."""
        response = self.client.get(
            reverse("oauth-authorize"), self.params(code_challenge_method="plain")
        )
        query = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(query["error"], ["invalid_request"])

    def test_a_token_asked_for_another_resource_is_refused(self):
        """RFC 8707: we only issue tokens for ourselves."""
        response = self.client.get(
            reverse("oauth-authorize"), self.params(resource="https://elsewhere.example/mcp")
        )
        query = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(query["error"], ["invalid_target"])

    def test_allowing_returns_a_code_with_state_and_iss(self):
        response = self.client.post(
            reverse("oauth-authorize"),
            self.params(
                decision="allow",
                scope_read="on",
                scope_write="on",
                scope="memento:read memento:write",
            ),
        )
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(query["state"], ["xyz"])
        # RFC 9207: the client compares this with the issuer it recorded.
        self.assertEqual(query["iss"], [BASE])
        self.assertTrue(query["code"][0])

    def test_declining_tells_the_client_and_creates_nothing(self):
        response = self.client.post(reverse("oauth-authorize"), self.params(decision="deny"))
        query = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(query["error"], ["access_denied"])
        self.assertFalse(OAuthGrant.objects.exists())

    def test_consent_creates_a_client_row_so_provenance_names_it(self):
        self.client.post(reverse("oauth-authorize"), self.params(decision="allow", scope_read="on"))
        client = Client.objects.get(owner=self.me, name="Claude")
        self.assertEqual(client.token_prefix, "sealed")  # no bearer token can act as it

    def test_the_consent_screen_does_not_pre_tick_forget(self):
        """
        Granting deletion must be a deliberate act (0025), which means the box
        arrives unticked. Read and write are pre-ticked; forget is not.
        """
        response = self.client.get(
            reverse("oauth-authorize"),
            self.params(scope="memento:read memento:write memento:forget"),
        )
        html = response.content.decode()
        self.assertRegex(html, r'name="scope_read"[^>]*\bchecked\b')
        self.assertNotRegex(html, r'name="scope_forget"[^>]*\bchecked\b')
        self.assertIn("Deleting is permanent", html)

    def test_forget_is_only_granted_when_the_owner_ticks_it(self):
        self.client.post(
            reverse("oauth-authorize"),
            self.params(
                decision="allow",
                scope="memento:read memento:write memento:forget",
                scope_read="on",
                scope_write="on",
            ),
        )
        grant = OAuthGrant.objects.get()
        self.assertNotIn(Scope.FORGET, grant.scopes)

    def test_forget_is_granted_when_ticked(self):
        self.client.post(
            reverse("oauth-authorize"),
            self.params(
                decision="allow",
                scope="memento:read memento:write memento:forget",
                scope_read="on",
                scope_write="on",
                scope_forget="on",
            ),
        )
        self.assertIn(Scope.FORGET, OAuthGrant.objects.get().scopes)


@override_settings(MEMENTO_BASE_URL=BASE, MEMENTO_RESOURCE_URL=RESOURCE)
class TokenTests(TestCase):
    def setUp(self):
        self.me = get_user_model().objects.create_user(username="sam", password="pw")
        self.oauth_client = OAuthClient.objects.create(
            client_id="memento-client-abc", client_name="Claude", redirect_uris=[REDIRECT]
        )
        self.client.force_login(self.me)
        response = self.client.post(
            reverse("oauth-authorize"),
            {
                "client_id": self.oauth_client.client_id,
                "redirect_uri": REDIRECT,
                "response_type": "code",
                "code_challenge": challenge_for(VERIFIER),
                "code_challenge_method": "S256",
                "resource": RESOURCE,
                "scope": "memento:read memento:write",
                "decision": "allow",
                "scope_read": "on",
                "scope_write": "on",
            },
        )
        self.code = parse_qs(urlparse(response["Location"]).query)["code"][0]

    def exchange(self, **overrides):
        data = {
            "grant_type": "authorization_code",
            "code": self.code,
            "redirect_uri": REDIRECT,
            "client_id": self.oauth_client.client_id,
            "code_verifier": VERIFIER,
        }
        return self.client.post(reverse("oauth-token"), {**data, **overrides})

    def test_a_code_becomes_an_access_and_refresh_token(self):
        body = self.exchange().json()
        self.assertEqual(body["token_type"], "Bearer")
        self.assertEqual(body["scope"], "memento:read memento:write")
        self.assertTrue(body["access_token"])
        self.assertTrue(body["refresh_token"])

    def test_the_wrong_verifier_is_refused(self):
        response = self.exchange(code_verifier="b" * 64)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_grant")

    def test_a_mismatched_redirect_uri_is_refused(self):
        self.assertEqual(self.exchange(redirect_uri="https://claude.ai/other").status_code, 400)

    def test_a_mismatched_client_id_is_refused(self):
        self.assertEqual(self.exchange(client_id="someone-else").status_code, 401)

    def test_a_code_works_once_and_a_replay_revokes_what_it_issued(self):
        """A replayed code means someone else has it, so the tokens it made are burnt."""
        first = self.exchange().json()
        replay = self.exchange()
        self.assertEqual(replay.status_code, 400)
        self.assertIsNone(oauth.verify_access_token(first["access_token"]))

    def test_an_expired_code_is_refused(self):
        OAuthGrant.objects.update(created_at=timezone.now() - timedelta(minutes=10))
        self.assertEqual(self.exchange().status_code, 400)

    def test_a_refresh_token_rotates(self):
        first = self.exchange().json()
        second = self.client.post(
            reverse("oauth-token"),
            {
                "grant_type": "refresh_token",
                "refresh_token": first["refresh_token"],
                "client_id": self.oauth_client.client_id,
            },
        ).json()
        self.assertNotEqual(first["refresh_token"], second["refresh_token"])
        # The spent one cannot be used again.
        again = self.client.post(
            reverse("oauth-token"),
            {
                "grant_type": "refresh_token",
                "refresh_token": first["refresh_token"],
                "client_id": self.oauth_client.client_id,
            },
        )
        self.assertEqual(again.status_code, 400)

    def test_a_refresh_cannot_widen_scope(self):
        first = self.exchange().json()
        response = self.client.post(
            reverse("oauth-token"),
            {
                "grant_type": "refresh_token",
                "refresh_token": first["refresh_token"],
                "client_id": self.oauth_client.client_id,
                "scope": "memento:read memento:write memento:forget",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_scope")

    def test_revoking_stops_the_token(self):
        body = self.exchange().json()
        self.client.post(reverse("oauth-revoke"), {"token": body["access_token"]})
        self.assertIsNone(oauth.verify_access_token(body["access_token"]))

    def test_an_unknown_grant_type_is_refused(self):
        response = self.client.post(reverse("oauth-token"), {"grant_type": "password"})
        self.assertEqual(response.json()["error"], "unsupported_grant_type")


@override_settings(MEMENTO_BASE_URL=BASE, MEMENTO_RESOURCE_URL=RESOURCE)
class AudienceTests(TestCase):
    """RFC 8707: a token issued for another resource must not work here."""

    def setUp(self):
        self.me = get_user_model().objects.create(username="sam")
        self.client_row, _ = s.create_client(self.me, "Claude")
        self.oauth_client = OAuthClient.objects.create(
            client_id="memento-client-abc", client_name="Claude", redirect_uris=[REDIRECT]
        )

    def issue(self, resource: str) -> str:
        token = oauth.TOKEN_PREFIX + "audience-test"
        OAuthToken.objects.create(
            token_hash=oauth._hash(token),
            use=OAuthToken.Use.ACCESS,
            oauth_client=self.oauth_client,
            client=self.client_row,
            scopes=[Scope.READ],
            resource=resource,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        return token

    def test_a_token_for_this_resource_works(self):
        verified = oauth.verify_access_token(self.issue(RESOURCE))
        self.assertIsNotNone(verified)
        self.assertEqual(verified[1], [Scope.READ])

    def test_a_token_for_another_resource_does_not(self):
        self.assertIsNone(oauth.verify_access_token(self.issue("https://elsewhere.example/mcp")))

    def test_an_expired_token_does_not(self):
        token = self.issue(RESOURCE)
        OAuthToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(oauth.verify_access_token(token))

    def test_a_token_whose_client_was_revoked_does_not(self):
        token = self.issue(RESOURCE)
        Client.objects.update(revoked_at=timezone.now())
        self.assertIsNone(oauth.verify_access_token(token))


@override_settings(MEMENTO_BASE_URL=BASE, MEMENTO_RESOURCE_URL=RESOURCE)
class MCPOverOAuthTests(TestCase):
    """
    Through config.asgi, as it is served. Both credential kinds must work: the
    distiller and Claude Code keep their bearer tokens (0016) while claude.ai
    uses OAuth, and the tools cannot tell the difference.
    """

    def setUp(self):
        self.me = get_user_model().objects.create(username="sam")
        self.bearer_client, self.bearer = s.create_client(self.me, "claude-code")
        self.oauth_client = OAuthClient.objects.create(
            client_id="memento-client-abc", client_name="claude-ai", redirect_uris=[REDIRECT]
        )
        self.client_row, _ = s.create_client(self.me, "claude-ai")
        self.access = oauth.TOKEN_PREFIX + "live-access-token"
        OAuthToken.objects.create(
            token_hash=oauth._hash(self.access),
            use=OAuthToken.Use.ACCESS,
            oauth_client=self.oauth_client,
            client=self.client_row,
            scopes=[Scope.READ, Scope.WRITE],
            resource=RESOURCE,
            expires_at=timezone.now() + timedelta(hours=1),
        )

    def request(self, go):
        app = http_app()

        async def run():
            async with app.app.router.lifespan_context(app.app):
                transport = httpx2.ASGITransport(app=app)
                async with httpx2.AsyncClient(
                    transport=transport, base_url="http://localhost"
                ) as http:
                    return await go(http)

        return async_to_sync(run)()

    def test_the_401_points_at_the_protected_resource_metadata(self):
        """Without resource_metadata a client that only speaks OAuth has nowhere to go."""

        async def go(http):
            return await http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

        response = self.request(go)
        self.assertEqual(response.status_code, 401)
        challenge = response.headers["www-authenticate"]
        self.assertIn(
            f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"', challenge
        )
        self.assertIn('scope="memento:read memento:write"', challenge)

    def test_an_oauth_access_token_reaches_the_tools(self):
        from mcp.client.streamable_http import streamable_http_client

        async def go(http):
            http.headers["Authorization"] = f"Bearer {self.access}"
            transport = streamable_http_client("http://localhost/mcp", http_client=http)
            async with MCPClient(transport) as c:
                return await c.call_tool(
                    "remember",
                    {"raw_text": "Walked the dog", "claim": "Walked the dog.", "kind": "memory"},
                )

        result = self.request(go)
        self.assertFalse(result.is_error, result.content)

    def test_provenance_records_the_oauth_client_name(self):
        from mcp.client.streamable_http import streamable_http_client

        async def go(http):
            http.headers["Authorization"] = f"Bearer {self.access}"
            transport = streamable_http_client("http://localhost/mcp", http_client=http)
            async with MCPClient(transport) as c:
                return await c.call_tool(
                    "remember",
                    {"raw_text": "Read a book", "claim": "Read a book.", "kind": "memory"},
                )

        self.request(go)
        from memories.models import Entry

        self.assertEqual(Entry.objects.get().client_name, "claude-ai")

    def test_a_bearer_token_client_still_works(self):
        """0016 must not regress: the distiller and the evals depend on it."""
        from mcp.client.streamable_http import streamable_http_client

        async def go(http):
            http.headers["Authorization"] = f"Bearer {self.bearer}"
            transport = streamable_http_client("http://localhost/mcp", http_client=http)
            async with MCPClient(transport) as c:
                return await c.call_tool("list_tags", {})

        self.assertFalse(self.request(go).is_error)

    def test_a_revoked_oauth_token_is_refused(self):
        OAuthToken.objects.update(revoked_at=timezone.now())

        async def go(http):
            return await http.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Authorization": f"Bearer {self.access}"},
            )

        self.assertEqual(self.request(go).status_code, 401)


class SettingsTests(TestCase):
    def test_production_refuses_to_start_without_a_canonical_url(self):
        """
        The issuer is a URL registered clients remember, so it cannot be guessed
        from a request. Starting without it would mint tokens under whatever host
        happened to arrive first.
        """
        import importlib
        import os

        import config.settings

        self.addCleanup(importlib.reload, config.settings)
        with (
            mock.patch.dict(
                os.environ,
                {"DJANGO_DEBUG": "0", "DJANGO_SECRET_KEY": "x", "MEMENTO_BASE_URL": ""},
            ),
            self.assertRaisesMessage(Exception, "MEMENTO_BASE_URL"),
        ):
            importlib.reload(config.settings)


@override_settings(MEMENTO_BASE_URL=BASE, MEMENTO_RESOURCE_URL=RESOURCE)
class ImpersonationTests(TestCase):
    """
    A registered client chooses its own `client_name`, and registration is
    unauthenticated. So the name is a label, never an identity: it must not decide
    which Client row an OAuth token acts as.
    """

    def setUp(self):
        self.me = get_user_model().objects.create_user(username="sam", password="pw")
        # What an owner already has, with a live bearer token.
        self.existing, self.bearer = s.create_client(self.me, "claude-code")
        self.impostor = OAuthClient.objects.create(
            client_id="memento-client-impostor",
            client_name="claude-code",  # the same name on purpose
            redirect_uris=[REDIRECT],
        )
        self.client.force_login(self.me)

    def consent(self, **extra):
        return self.client.post(
            reverse("oauth-authorize"),
            {
                "client_id": self.impostor.client_id,
                "redirect_uri": REDIRECT,
                "response_type": "code",
                "code_challenge": challenge_for(VERIFIER),
                "code_challenge_method": "S256",
                "resource": RESOURCE,
                "scope": "memento:read memento:write",
                "decision": "allow",
                "scope_read": "on",
                "scope_write": "on",
                **extra,
            },
        )

    def test_a_name_collision_does_not_take_over_an_existing_client(self):
        self.consent()
        grant = OAuthGrant.objects.get()
        self.assertNotEqual(grant.client_id, self.existing.pk)
        self.assertEqual(grant.client.oauth_client, self.impostor)

    def test_a_name_collision_gets_a_distinguishable_name(self):
        """Provenance must not be ambiguous about which client wrote an entry."""
        self.consent()
        name = OAuthGrant.objects.get().client.name
        self.assertNotEqual(name, "claude-code")
        self.assertIn("claude-code", name)

    def test_consenting_never_revives_a_revoked_bearer_client(self):
        """
        Revoking is the kill switch. A stranger registering under the revoked
        client's name and getting the owner to consent must not lift it.
        """
        Client.objects.filter(pk=self.existing.pk).update(revoked_at=timezone.now())
        self.consent()
        self.existing.refresh_from_db()
        self.assertIsNotNone(self.existing.revoked_at)
        self.assertIsNone(s.authenticate(self.bearer))

    def test_consenting_never_changes_an_existing_clients_scopes(self):
        self.consent(scope="memento:read memento:write memento:forget", scope_forget="on")
        self.existing.refresh_from_db()
        self.assertNotIn(Scope.FORGET, self.existing.scopes)

    def test_the_row_an_oauth_token_acts_as_is_always_sealed(self):
        self.consent()
        self.assertEqual(OAuthGrant.objects.get().client.token_prefix, "sealed")

    def test_re_consent_reuses_the_same_row_for_the_same_client(self):
        self.consent()
        first = OAuthGrant.objects.get().client_id
        self.consent()
        self.assertEqual(set(OAuthGrant.objects.values_list("client_id", flat=True)), {first})


@override_settings(MEMENTO_BASE_URL=BASE, MEMENTO_RESOURCE_URL=RESOURCE)
class ConsentIntegrityTests(TestCase):
    """What is granted must be what the screen showed as ticked."""

    def setUp(self):
        self.me = get_user_model().objects.create_user(username="sam", password="pw")
        self.oauth_client = OAuthClient.objects.create(
            client_id="memento-client-abc", client_name="Claude", redirect_uris=[REDIRECT]
        )
        self.client.force_login(self.me)

    def test_allowing_with_nothing_ticked_grants_nothing(self):
        """
        Clearing every box and pressing Allow must not hand over everything the
        client asked for — least of all memento:forget (Principle 5).
        """
        response = self.client.post(
            reverse("oauth-authorize"),
            {
                "client_id": self.oauth_client.client_id,
                "redirect_uri": REDIRECT,
                "response_type": "code",
                "code_challenge": challenge_for(VERIFIER),
                "code_challenge_method": "S256",
                "resource": RESOURCE,
                "scope": "memento:read memento:write memento:forget",
                "decision": "allow",
            },
        )
        query = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(query["error"], ["access_denied"])
        self.assertFalse(OAuthGrant.objects.exists())


@override_settings(MEMENTO_BASE_URL=BASE, MEMENTO_RESOURCE_URL=RESOURCE)
class RevocationTests(TestCase):
    """Revoking the Client row has to stop renewal too, or it is not a kill switch."""

    def setUp(self):
        self.me = get_user_model().objects.create(username="sam")
        self.oauth_client = OAuthClient.objects.create(
            client_id="memento-client-abc", client_name="Claude", redirect_uris=[REDIRECT]
        )
        self.client_row, _ = s.create_client(self.me, "Claude")
        self.client_row.oauth_client = self.oauth_client
        self.client_row.save(update_fields=["oauth_client"])
        self.refresh = oauth.TOKEN_PREFIX + "refresh-token-for-revocation"
        OAuthToken.objects.create(
            token_hash=oauth._hash(self.refresh),
            use=OAuthToken.Use.REFRESH,
            oauth_client=self.oauth_client,
            client=self.client_row,
            scopes=[Scope.READ],
            resource=RESOURCE,
            expires_at=timezone.now() + timedelta(days=90),
        )

    def refresh_once(self):
        return self.client.post(
            reverse("oauth-token"),
            {
                "grant_type": "refresh_token",
                "refresh_token": self.refresh,
                "client_id": self.oauth_client.client_id,
            },
        )

    def test_a_live_client_can_refresh(self):
        self.assertEqual(self.refresh_once().status_code, 200)

    def test_a_revoked_client_cannot_refresh(self):
        """Otherwise a revoked client rotates a fresh 90-day token indefinitely."""
        Client.objects.filter(pk=self.client_row.pk).update(revoked_at=timezone.now())
        self.assertEqual(self.refresh_once().status_code, 400)

    def test_an_inactive_owner_cannot_refresh(self):
        get_user_model().objects.filter(pk=self.me.pk).update(is_active=False)
        self.assertEqual(self.refresh_once().status_code, 400)
