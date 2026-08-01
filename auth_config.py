"""Microsoft Entra OAuth configuration for the hosted MCP server."""

from __future__ import annotations

import os
import re
import uuid
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from fastmcp.server.auth.providers.azure import AzureProvider
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper


_SCOPE_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_CLAUDE_REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"


def _required_env(name: str, *, min_length: int = 1) -> str:
    value = os.getenv(name, "").strip()
    if len(value) < min_length:
        raise RuntimeError(f"{name} is required and is not valid")
    return value


def _uuid_env(name: str) -> str:
    value = _required_env(name)
    try:
        uuid.UUID(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a UUID") from exc
    return value


def _public_base_url() -> str:
    value = os.getenv("MCP_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not value:
        railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
        if railway_domain:
            value = f"https://{railway_domain}"
    if not value:
        raise RuntimeError("MCP_PUBLIC_BASE_URL is required")

    parsed = urlsplit(value)
    is_loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback):
        raise RuntimeError("MCP_PUBLIC_BASE_URL must use HTTPS")
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RuntimeError("MCP_PUBLIC_BASE_URL must be a clean origin URL")
    return value


def _allowed_redirect_uris(base_url: str) -> list[str]:
    configured = os.getenv("MCP_ALLOWED_REDIRECT_URIS", "").strip()
    if configured:
        redirects = [item.strip() for item in configured.split(",") if item.strip()]
        if not redirects:
            raise RuntimeError("MCP_ALLOWED_REDIRECT_URIS is not valid")
        return redirects

    if urlsplit(base_url).hostname in {"localhost", "127.0.0.1", "::1"}:
        return [
            _CLAUDE_REDIRECT_URI,
            "http://localhost:*",
            "http://127.0.0.1:*",
        ]
    return [_CLAUDE_REDIRECT_URI]


def _oauth_storage(base_url: str):
    mode = os.getenv("OAUTH_STORAGE_BACKEND", "redis").strip().lower()
    is_loopback = urlsplit(base_url).hostname in {"localhost", "127.0.0.1", "::1"}

    if mode == "memory":
        if not is_loopback:
            raise RuntimeError("In-memory OAuth storage is allowed only on loopback")
        return MemoryStore()
    if mode != "redis":
        raise RuntimeError("OAUTH_STORAGE_BACKEND must be redis or memory")

    redis_url = _required_env("REDIS_URL")
    encryption_key = _required_env("OAUTH_STORAGE_ENCRYPTION_KEY", min_length=32)
    try:
        fernet = Fernet(encryption_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("OAUTH_STORAGE_ENCRYPTION_KEY is not a Fernet key") from exc

    return FernetEncryptionWrapper(
        key_value=RedisStore(
            url=redis_url,
            default_collection="cargoboard-mcp-oauth",
        ),
        fernet=fernet,
    )


def build_entra_provider() -> AzureProvider:
    """Build a single-tenant Entra provider with production-safe defaults."""
    client_id = _uuid_env("ENTRA_CLIENT_ID")
    tenant_id = _uuid_env("ENTRA_TENANT_ID")
    client_secret = _required_env("ENTRA_CLIENT_SECRET", min_length=16)
    jwt_signing_key = _required_env("OAUTH_JWT_SIGNING_KEY", min_length=32)
    scope = os.getenv("ENTRA_SCOPE", "mcp.access").strip()
    if not _SCOPE_PATTERN.fullmatch(scope):
        raise RuntimeError("ENTRA_SCOPE is not valid")

    base_url = _public_base_url()
    identifier_uri = os.getenv("ENTRA_IDENTIFIER_URI", "").strip() or None

    return AzureProvider(
        client_id=client_id,
        client_secret=client_secret,
        tenant_id=tenant_id,
        identifier_uri=identifier_uri,
        base_url=base_url,
        required_scopes=[scope],
        additional_authorize_scopes=[
            "openid",
            "profile",
            "email",
            "offline_access",
        ],
        allowed_client_redirect_uris=_allowed_redirect_uris(base_url),
        client_storage=_oauth_storage(base_url),
        jwt_signing_key=jwt_signing_key,
        require_authorization_consent=True,
    )
