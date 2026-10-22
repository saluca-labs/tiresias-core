"""Regressions found by the independent review of PR #6.

1. The onprem key sent in BOTH X-Tiresias-Api-Key and Authorization: the
   second copy was forwarded upstream.
2. Router failover sent the client's own provider credential to a different
   provider.
3. A failure while showing the first-boot key left a stored hash for a key
   nobody saw.
4. A key inside a traceback was not redacted.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest
import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from httpx import Response as HttpxResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import tiresias.bootstrap as bootstrap
from tests.conftest import provision_tenant_key
from tiresias.config import TiresiasSettings
from tiresias.proxy.app import create_app
from tiresias.storage.engine import close_all_engines, get_engine
from tiresias.storage.schema import TiresiasLicense

KEK = "45286db43824c34cf7865ae507579d754ffefd59816432d5c5513e25bb7995a4"


def _settings(tmp_path, **extra) -> TiresiasSettings:
    return TiresiasSettings(
        TIRESIAS_TENANT_ID="review-tenant",
        TIRESIAS_KEK_PROVIDER="local",
        TIRESIAS_DATA_ROOT=tmp_path,
        TIRESIAS_KEK=KEK,
        **extra,
    )


def _sent(route):
    assert route.called, "upstream never called; the test checked nothing"
    return [(k.lower(), v) for k, v in route.calls.last.request.headers.multi_items()]


@pytest.fixture
async def legacy(tmp_path):
    settings = _settings(tmp_path, TIRESIAS_UPSTREAM_URL="http://mock-upstream")
    key = await provision_tenant_key(settings)
    app = create_app(settings=settings)
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c, key
    await close_all_engines()


async def test_key_in_both_headers_never_forwarded(legacy):
    client, key = legacy
    with respx.mock:
        route = respx.get("http://mock-upstream/v1/models").mock(return_value=HttpxResponse(200, json={}))
        resp = await client.get("/v1/models", headers={
            "X-Tiresias-Api-Key": key, "Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    assert not any(key in v for _, v in _sent(route))


async def test_key_in_duplicate_authorization_never_forwarded(legacy):
    client, key = legacy
    with respx.mock:
        route = respx.get("http://mock-upstream/v1/models").mock(return_value=HttpxResponse(200, json={}))
        resp = await client.get("/v1/models", headers=[
            ("x-tiresias-api-key", key),
            ("authorization", "Bearer sk-user"),
            ("authorization", f"Bearer {key}"),
        ])
    assert resp.status_code == 200
    assert not any(key in v for _, v in _sent(route))


async def test_failover_never_sends_client_credential_to_another_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-provider-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-provider")
    settings = _settings(tmp_path, TIRESIAS_UPSTREAM_URL="https://api.openai.com",
                         TIRESIAS_PROVIDERS="openai,anthropic")
    key = await provision_tenant_key(settings)
    app = create_app(settings=settings)
    anthropic_ok = {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude",
        "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            with respx.mock:
                respx.post("https://api.openai.com/v1/chat/completions").mock(return_value=HttpxResponse(500))
                route = respx.post("https://api.anthropic.com/v1/messages").mock(
                    return_value=HttpxResponse(200, json=anthropic_ok))
                resp = await c.post(
                    "/v1/chat/completions",
                    json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "x"}]},
                    headers={"X-Tiresias-Api-Key": key,
                             "Authorization": "Bearer sk-user-openai-key",
                             "X-Api-Key": "sk-user-other"},
                )
    await close_all_engines()
    assert resp.status_code == 200
    sent = _sent(route)
    assert not any("sk-user" in v for _, v in sent), "client credential reached the failover provider"
    assert [v for k, v in sent if k == "x-api-key"] == ["ant-provider"]
    assert not any(key in v for _, v in sent)


class _BrokenTTY(io.StringIO):
    def isatty(self) -> bool:
        return True

    def write(self, s):
        raise OSError("terminal went away")


async def test_failed_key_display_stores_nothing(tmp_path):
    settings = _settings(tmp_path)
    engine = await get_engine(settings.tenant_id, settings.data_root)
    async with AsyncSession(engine) as session:
        with pytest.raises(OSError):
            await bootstrap.first_boot(settings.tenant_id, settings, session, out=_BrokenTTY())
    async with AsyncSession(engine) as session:
        row = (await session.execute(select(TiresiasLicense).where(
            TiresiasLicense.tenant_id == settings.tenant_id))).scalar_one_or_none()
    # Nothing persisted, so the next boot generates and shows a fresh key.
    assert row is None
    async with AsyncSession(engine) as session:
        key = await provision_tenant_key(settings)
    assert key
    await close_all_engines()


def test_key_inside_traceback_is_redacted(caplog):
    secret = "tb-secret-0123456789abcdefghij"
    bootstrap.redact_in_logs(secret)
    with caplog.at_level(logging.DEBUG):
        try:
            raise RuntimeError(f"upstream rejected {secret}")
        except RuntimeError:
            logging.getLogger("tiresias.x").exception("call failed")
    assert caplog.records
    text = caplog.text + "".join((r.exc_text or "") for r in caplog.records)
    assert secret not in text
    assert "[REDACTED]" in text
