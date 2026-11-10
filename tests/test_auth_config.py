import asyncio
import os
import unittest
from unittest.mock import patch

from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.testclient import TestClient

import auth_config

CLAUDE = "https://claude.ai/api/mcp/auth_callback"
CLAUDE_COM = "https://claude.com/api/mcp/auth_callback"
ENV = {
    "ENTRA_CLIENT_ID": "11111111-1111-1111-1111-111111111111",
    "ENTRA_CLIENT_SECRET": "local-test-client-secret",
    "ENTRA_TENANT_ID": "22222222-2222-2222-2222-222222222222",
    "ENTRA_SCOPE": "mcp.access",
    "MCP_PUBLIC_BASE_URL": "https://cargo.example.test",
    "OAUTH_JWT_SIGNING_KEY": "local-test-signing-key-0123456789abcdef",
    "OAUTH_STORAGE_BACKEND": "redis",
    "REDIS_URL": "redis://localhost:6379/0",
    "OAUTH_STORAGE_ENCRYPTION_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
}
REGISTRATION = {
    "client_name": "Test client",
    "token_endpoint_auth_method": "none",
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
}


def _provider(**overrides):
    with patch.dict(os.environ, ENV | overrides, clear=True):
        return auth_config.build_entra_provider()


class RedirectAllowlistTests(unittest.TestCase):
    def test_exact_entries_do_not_match_lookalikes(self):
        patterns = [CLAUDE]

        self.assertTrue(auth_config.redirect_uri_allowed(CLAUDE, patterns))
        for uri in (
            "https://evil.example/cb",
            CLAUDE + "/",
            CLAUDE + "?next=https://evil.example",
            CLAUDE + "#fragment",
            "https://claude.ai.evil.example/api/mcp/auth_callback",
            "https://user@claude.ai/api/mcp/auth_callback",
            "http://claude.ai/api/mcp/auth_callback",
            "http://localhost:8080/callback",
        ):
            with self.subTest(uri=uri):
                self.assertFalse(auth_config.redirect_uri_allowed(uri, patterns))

    def test_loopback_entries_match_on_the_parsed_host(self):
        patterns = ["http://localhost:*", "http://127.0.0.1:*"]

        self.assertTrue(
            auth_config.redirect_uri_allowed("http://localhost:5123/callback", patterns)
        )
        self.assertTrue(
            auth_config.redirect_uri_allowed("http://127.0.0.1:5123/cb", patterns)
        )
        for uri in (
            "http://localhost:1@evil.example/cb",
            "http://localhost.evil.example:80/cb",
            "https://localhost:5123/callback",
            "http://127.0.0.1.evil.example:80/cb",
        ):
            with self.subTest(uri=uri):
                self.assertFalse(auth_config.redirect_uri_allowed(uri, patterns))

    def test_production_allows_only_claude(self):
        provider = _provider()

        self.assertEqual(provider._allowed_client_redirect_uris, [CLAUDE, CLAUDE_COM])

    def test_stored_client_with_foreign_callback_is_not_recognized(self):
        provider = _provider()
        allowed = OAuthClientInformationFull(client_id="a", redirect_uris=[AnyUrl(CLAUDE)])
        foreign = OAuthClientInformationFull(
            client_id="b",
            redirect_uris=[AnyUrl(CLAUDE), AnyUrl("https://evil.example/cb")],
        )

        self.assertTrue(provider._client_allowed(allowed))
        self.assertFalse(provider._client_allowed(foreign))


class RegistrationEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastmcp import FastMCP

        provider = _provider(
            MCP_PUBLIC_BASE_URL="http://localhost:8000", OAUTH_STORAGE_BACKEND="memory"
        )
        cls.provider = provider
        cls.client = TestClient(FastMCP("test", auth=provider).http_app(path="/mcp"))

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def register(self, *redirect_uris):
        return self.client.post(
            "/register", json=REGISTRATION | {"redirect_uris": redirect_uris}
        )

    def authorize(self, client_id, redirect_uri):
        return self.client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
                "code_challenge_method": "S256",
                "state": "abc",
                "scope": "mcp.access",
            },
            follow_redirects=False,
        )

    def test_foreign_callbacks_cannot_register(self):
        for uri in ("https://evil.example/cb", "http://localhost:1@evil.example/cb"):
            with self.subTest(uri=uri):
                response = self.register(uri)

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], "invalid_redirect_uri")

    def test_claude_callbacks_register(self):
        for uri in (CLAUDE, CLAUDE_COM):
            with self.subTest(uri=uri):
                response = self.register(uri)

                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.json()["redirect_uris"], [uri])

    def test_mixed_registration_keeps_only_allowlisted_callbacks(self):
        response = self.register(CLAUDE, "https://evil.example/cb")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["redirect_uris"], [CLAUDE])
        stored = asyncio.run(self.provider.get_client(response.json()["client_id"]))
        self.assertEqual([str(uri) for uri in stored.redirect_uris], [CLAUDE])

    def test_stored_client_with_foreign_callback_cannot_authorize(self):
        # A client stored before the allowlist was enforced.
        from fastmcp.server.auth.providers.azure import AzureProvider

        foreign = OAuthClientInformationFull(
            client_id="stored-before-the-fix",
            redirect_uris=[AnyUrl("https://evil.example/cb")],
        )
        asyncio.run(AzureProvider.register_client(self.provider, foreign))

        response = self.authorize(foreign.client_id, "https://evil.example/cb")

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("location", response.headers)

    def test_authorize_never_redirects_to_a_foreign_callback(self):
        claude_client = self.register(CLAUDE).json()["client_id"]
        local_client = self.register("http://localhost:5123/callback").json()["client_id"]

        for client_id, uri in (
            (claude_client, "https://evil.example/cb"),
            (local_client, "https://evil.example/cb"),
            (local_client, "http://localhost:1@evil.example/cb"),
        ):
            with self.subTest(uri=uri):
                response = self.authorize(client_id, uri)

                self.assertEqual(response.status_code, 400)
                self.assertNotIn("location", response.headers)

    def test_authorize_accepts_allowlisted_callbacks(self):
        claude_client = self.register(CLAUDE).json()["client_id"]
        local_client = self.register("http://localhost:5123/callback").json()["client_id"]

        for client_id, uri in (
            (claude_client, CLAUDE),
            (local_client, "http://localhost:5123/callback"),
            # Native clients may come back on another loopback port.
            (local_client, "http://localhost:6001/callback"),
        ):
            with self.subTest(uri=uri):
                response = self.authorize(client_id, uri)

                self.assertEqual(response.status_code, 302)
                self.assertTrue(
                    response.headers["location"].startswith("http://localhost:8000/consent")
                )


class ProviderConfigurationTests(unittest.TestCase):
    def test_configured_allowlist_replaces_the_default(self):
        provider = _provider(MCP_ALLOWED_REDIRECT_URIS=f"{CLAUDE}, https://app.example/cb")

        self.assertEqual(
            provider._allowed_client_redirect_uris, [CLAUDE, "https://app.example/cb"]
        )

    def test_unsafe_settings_are_rejected(self):
        cases = (
            {"MCP_PUBLIC_BASE_URL": "http://cargo.example.test"},
            {"MCP_PUBLIC_BASE_URL": "https://cargo.example.test/path"},
            {"OAUTH_STORAGE_BACKEND": "memory"},
            {"OAUTH_STORAGE_ENCRYPTION_KEY": "not-a-fernet-key-but-long-enough-0000"},
            {"OAUTH_JWT_SIGNING_KEY": "short"},
            {"ENTRA_TENANT_ID": "common"},
            {"ENTRA_SCOPE": "mcp access"},
            {"REDIS_URL": ""},
        )
        for override in cases:
            with self.subTest(override=override):
                with self.assertRaises(RuntimeError):
                    _provider(**override)


if __name__ == "__main__":
    unittest.main()
