"""
SaaS Auth Middleware for Tiresias Proxy.

In SaaS mode (TIRESIAS_MODE=saas), resolves tenant_id from the Tiresias API key
on every request. The API key is passed via:
  - Authorization: Bearer tir_<slug>_<hex32>
  - X-Tiresias-Api-Key: tir_<slug>_<hex32>

The key is SHA-256 hashed and looked up against tiresias_licenses.api_key_hash.
On match, request.state.tenant_id and request.state.tenant_tier are set.

In dedicated/onprem modes the proxy is single-tenant and the caller must
present that tenant's first-boot API key, via X-Tiresias-Api-Key or
Authorization: Bearer <key>. It used to be a no-op in those modes, so every
route (chat completions, /v1/admin/reload, /api/*) answered anyone who could
reach the port.

TIRESIAS_ALLOW_UNAUTHENTICATED=true turns that check off. It exists for one
case: a single-user proxy bound to 127.0.0.1 on a developer machine, driven by
a client that cannot send a second credential. It is logged as a WARNING on
every boot and reported by /health. Never set it on a proxy other machines can
reach.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiresias.proxy.headers import CREDENTIAL_HEADER_STATE_ATTR, CREDENTIAL_VALUE_STATE_ATTR
from tiresias.storage.schema import TiresiasLicense

if TYPE_CHECKING:
    from tiresias.config import TiresiasSettings

logger = logging.getLogger(__name__)

# Paths that bypass tenant auth (health, docs, OpenAPI)
_AUTH_EXEMPT_PATHS = frozenset({
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
})

# Dashboard routes enforce their own auth (tiresias.dashboard.auth, which also
# accepts X-SoulKey); the single-tenant check below leaves them to it.
_SELF_AUTHENTICATED_PREFIXES = ("/dash/",)

# In-memory cache: api_key_hash -> (tenant_id, tier, cached_at)
_tenant_cache: dict[str, tuple[str, str, float]] = {}
# Single-tenant modes: (tenant_id, data_root) -> (api_key_hash, cached_at)
_single_tenant_cache: dict[tuple[str, str], tuple[str, float]] = {}
_CACHE_TTL_SECONDS = 60


def _hash_api_key(raw_key: str) -> str:
    """SHA-256 hash of the raw API key, matching the stored api_key_hash."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _extract_api_key(request: Request) -> str | None:
    """Extract Tiresias API key from Authorization header or X-Tiresias-Api-Key."""
    # Check custom header first (preferred for passthrough scenarios)
    key = request.headers.get("x-tiresias-api-key")
    if key:
        setattr(request.state, CREDENTIAL_HEADER_STATE_ATTR, "x-tiresias-api-key")
        setattr(request.state, CREDENTIAL_VALUE_STATE_ATTR, key.strip())
        return key.strip()

    # Fall back to Authorization: Bearer
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and auth[7:].strip().startswith("tir_"):
        setattr(request.state, CREDENTIAL_HEADER_STATE_ATTR, "authorization")
        setattr(request.state, CREDENTIAL_VALUE_STATE_ATTR, auth[7:].strip())
        return auth[7:].strip()

    return None


def _extract_single_tenant_candidates(request: Request) -> list[tuple[str, str]]:
    """(header, value) pairs that may carry the single-tenant key, in order.

    The first-boot key is not tir_-prefixed, so a bare Bearer value is a
    candidate too. It is treated as the Tiresias credential (and so stripped
    before forwarding) only if it actually matches.
    """
    out: list[tuple[str, str]] = []
    key = request.headers.get("x-tiresias-api-key")
    if key and key.strip():
        out.append(("x-tiresias-api-key", key.strip()))
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and auth[7:].strip():
        out.append(("authorization", auth[7:].strip()))
    return out


def clear_tenant_cache() -> None:
    """Clear the in-memory tenant cache (useful for testing and key rotation)."""
    _tenant_cache.clear()
    _single_tenant_cache.clear()


class SaaSAuthMiddleware(BaseHTTPMiddleware):
    """Resolves tenant from Tiresias API key in SaaS mode.

    Injects request.state.tenant_id and request.state.tenant_tier.
    Returns 401 if no valid key is found.
    """

    def __init__(self, app, settings: "TiresiasSettings", engine_factory):
        super().__init__(app)
        self.settings = settings
        self.engine_factory = engine_factory

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Single-tenant modes (dedicated, onprem): require the tenant's key.
        if self.settings.mode != "saas":
            request.state.tenant_id = self.settings.tenant_id
            if self.settings.allow_unauthenticated:
                return await call_next(request)
            path = request.url.path
            if path in _AUTH_EXEMPT_PATHS or path.startswith(_SELF_AUTHENTICATED_PREFIXES):
                return await call_next(request)
            return await self._single_tenant_auth(request, call_next)

        # Skip exempt paths
        if request.url.path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)

        # Extract API key
        raw_key = _extract_api_key(request)
        if not raw_key:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "missing_api_key",
                    "detail": "Tiresias API key required. Pass via Authorization: Bearer tir_... or X-Tiresias-Api-Key header.",
                },
            )

        key_hash = _hash_api_key(raw_key)

        # Check cache first
        now = time.monotonic()
        cached = _tenant_cache.get(key_hash)
        if cached and (now - cached[2]) < _CACHE_TTL_SECONDS:
            request.state.tenant_id = cached[0]
            request.state.tenant_tier = cached[1]
            return await call_next(request)

        # Cache miss — look up in DB
        try:
            engine = await self.engine_factory()
            async with AsyncSession(engine) as session:
                result = await session.execute(
                    select(TiresiasLicense.tenant_id, TiresiasLicense.tier).where(
                        TiresiasLicense.api_key_hash == key_hash
                    )
                )
                row = result.first()
        except Exception:
            logger.exception("SaaS auth DB lookup failed")
            return JSONResponse(
                status_code=503,
                content={"error": "auth_unavailable", "detail": "Authentication service temporarily unavailable."},
            )

        if not row:
            return JSONResponse(
                status_code=401,
                content={"error": "invalid_api_key", "detail": "Invalid Tiresias API key."},
            )

        tenant_id, tier = row[0], row[1]

        # Populate cache
        _tenant_cache[key_hash] = (tenant_id, tier, now)

        # Inject into request state
        request.state.tenant_id = tenant_id
        request.state.tenant_tier = tier

        return await call_next(request)

    async def _single_tenant_auth(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        candidates = _extract_single_tenant_candidates(request)
        if not candidates:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "missing_api_key",
                    "detail": "Tiresias API key required. Pass via X-Tiresias-Api-Key "
                              "or Authorization: Bearer <key>.",
                },
            )
        try:
            stored_hash = await self._single_tenant_key_hash()
        except Exception:
            logger.exception("Single-tenant auth lookup failed")
            stored_hash = None
        if not stored_hash:
            # Cannot tell is never a pass: no stored key means refuse.
            return JSONResponse(
                status_code=503,
                content={"error": "auth_unavailable",
                         "detail": "Tenant key is not initialised."},
            )
        for header, value in candidates:
            if hmac.compare_digest(_hash_api_key(value), stored_hash):
                setattr(request.state, CREDENTIAL_HEADER_STATE_ATTR, header)
                setattr(request.state, CREDENTIAL_VALUE_STATE_ATTR, value)
                return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={"error": "invalid_api_key", "detail": "Invalid Tiresias API key."},
        )

    async def _single_tenant_key_hash(self) -> str | None:
        tenant_id = self.settings.tenant_id
        now = time.monotonic()
        cache_key = (tenant_id, str(self.settings.data_root))
        cached = _single_tenant_cache.get(cache_key)
        if cached and (now - cached[1]) < _CACHE_TTL_SECONDS:
            return cached[0]
        from tiresias.storage.engine import get_engine

        engine = await get_engine(tenant_id, self.settings.data_root)
        async with AsyncSession(engine) as session:
            result = await session.execute(
                select(TiresiasLicense.api_key_hash).where(
                    TiresiasLicense.tenant_id == tenant_id
                )
            )
            row = result.first()
        stored = row[0] if row else None
        if stored:
            _single_tenant_cache[cache_key] = (stored, now)
        return stored

