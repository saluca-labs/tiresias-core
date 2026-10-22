"""onprem and dedicated modes require the tenant's API key.

The auth middleware used to return early for every mode except saas, so an
onprem proxy answered anyone on every route, /v1/admin/reload included.
"""
from __future__ import annotations

import logging

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from tests.conftest import provision_tenant_key
from tiresias.config import TiresiasSettings
from tiresias.proxy.app import create_app
from tiresias.storage.engine import close_all_engines

KEK = "45286db43824c34cf7865ae507579d754ffefd59816432d5c5513e25bb7995a4"

# (method, path) pairs that must refuse an unauthenticated caller. None of them
# needs an upstream to answer 401.
PROTECTED = [
    ("POST", "/v1/chat/completions"),
    ("GET", "/v1/admin/providers"),
    ("POST", "/v1/admin/reload"),
    ("GET", "/v1/sessions/abc"),
    ("POST", "/v1/sessions/abc/tag"),
    ("GET", "/v1/analytics/unified"),
    ("GET", "/v1/models"),
    ("GET", "/api/anything"),
]


def _settings(tmp_path, mode="onprem", **extra) -> TiresiasSettings:
    return TiresiasSettings(
        TIRESIAS_MODE=mode,
        TIRESIAS_TENANT_ID="auth-test-tenant",
        TIRESIAS_KEK_PROVIDER="local",
        TIRESIAS_DATA_ROOT=tmp_path,
        TIRESIAS_KEK=KEK,
        TIRESIAS_UPSTREAM_URL="http://mock-upstream",
        **extra,
    )


async def _client(settings):
    key = await provision_tenant_key(settings)
    app = create_app(settings=settings)
    return app, key


@pytest.mark.parametrize("mode", ["onprem", "dedicated"])
@pytest.mark.parametrize("method,path", PROTECTED)
async def test_unauthenticated_request_is_refused(tmp_path, mode, method, path):
    app, _key = await _client(_settings(tmp_path, mode=mode))
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.request(method, path, json={})
    await close_all_engines()
    assert resp.status_code == 401, f"{method} {path} answered {resp.status_code} without a key"
    assert resp.json()["error"] == "missing_api_key"


async def test_wrong_key_is_refused(tmp_path):
    app, _key = await _client(_settings(tmp_path))
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r1 = await c.get("/v1/admin/providers", headers={"X-Tiresias-Api-Key": "not-the-key"})
            r2 = await c.get("/v1/admin/providers", headers={"Authorization": "Bearer not-the-key"})
    await close_all_engines()
    assert r1.status_code == 401 and r1.json()["error"] == "invalid_api_key"
    assert r2.status_code == 401 and r2.json()["error"] == "invalid_api_key"


async def test_correct_key_is_accepted_in_either_header(tmp_path):
    app, key = await _client(_settings(tmp_path))
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r1 = await c.get("/v1/admin/providers", headers={"X-Tiresias-Api-Key": key})
            r2 = await c.get("/v1/admin/providers", headers={"Authorization": f"Bearer {key}"})
    await close_all_engines()
    assert r1.status_code == 200
    assert r2.status_code == 200


async def test_health_stays_open_and_reports_auth(tmp_path):
    app, _key = await _client(_settings(tmp_path))
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
    await close_all_engines()
    assert resp.status_code == 200
    assert resp.json()["auth"] == "api_key"


async def test_explicit_opt_out_is_loud(tmp_path, caplog):
    settings = _settings(tmp_path, TIRESIAS_ALLOW_UNAUTHENTICATED=True)
    app, _key = await _client(settings)
    with caplog.at_level(logging.WARNING):
        async with LifespanManager(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.get("/v1/admin/providers")
                h = await c.get("/health")
    await close_all_engines()
    assert r.status_code == 200
    assert h.json()["auth"] == "disabled"
    assert "TIRESIAS_ALLOW_UNAUTHENTICATED" in caplog.text


def test_opt_out_is_off_by_default():
    assert TiresiasSettings().allow_unauthenticated is False
