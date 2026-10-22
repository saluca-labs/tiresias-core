"""What reaches the upstream: Tiresias credentials never do.

Each test captures the request the upstream actually received (respx) and
checks its headers. The /v1/{path} catch-all used to forward every client
header except host, content-length and transfer-encoding, so the caller's
X-Tiresias-Api-Key went to the provider.
"""
from __future__ import annotations

import pytest
import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from httpx import Response as HttpxResponse

from tests.conftest import provision_tenant_key
from tiresias.config import TiresiasSettings
from tiresias.proxy.app import create_app
from tiresias.proxy.headers import llm_upstream_headers
from tiresias.storage.engine import close_all_engines

KEK = "45286db43824c34cf7865ae507579d754ffefd59816432d5c5513e25bb7995a4"

CHAT_OK = {
    "id": "chatcmpl-h",
    "object": "chat.completion",
    "model": "gpt-4o-mini",
    "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop", "index": 0}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}

# Headers a caller might send that must never leave the proxy.
NEVER_FORWARDED = ("x-tiresias-api-key", "x-tiresias-session-id", "x-soulkey", "cookie", "x-internal-thing")


def _sent(route) -> list[tuple[str, str]]:
    assert route.called, "upstream was never called, so this test checked nothing"
    return [(k.lower(), v) for k, v in route.calls.last.request.headers.multi_items()]


def _values(sent, name):
    return [v for k, v in sent if k == name]


def _settings(tmp_path, **extra) -> TiresiasSettings:
    return TiresiasSettings(
        TIRESIAS_TENANT_ID="hdr-test-tenant",
        TIRESIAS_KEK_PROVIDER="local",
        TIRESIAS_DATA_ROOT=tmp_path,
        TIRESIAS_KEK=KEK,
        **extra,
    )


@pytest.fixture
async def legacy(tmp_path):
    """Single upstream, client passes its own provider key through."""
    settings = _settings(tmp_path, TIRESIAS_UPSTREAM_URL="http://mock-upstream")
    key = await provision_tenant_key(settings)
    app = create_app(settings=settings)
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c, key
    await close_all_engines()


@pytest.fixture
async def routed(tmp_path, monkeypatch):
    """Multi-provider router: the provider key comes from the environment."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-provider-env")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-provider-env")
    settings = _settings(tmp_path, TIRESIAS_UPSTREAM_URL="https://api.openai.com",
                         TIRESIAS_PROVIDERS="openai,anthropic")
    key = await provision_tenant_key(settings)
    app = create_app(settings=settings)
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c, key
    await close_all_engines()


def _hostile_headers(key: str) -> dict:
    return {
        "X-Tiresias-Api-Key": key,
        "X-Tiresias-Session-Id": "sess-1",
        "X-SoulKey": "soul-secret",
        "Cookie": "session=abc",
        "X-Internal-Thing": "do-not-forward",
        "OpenAI-Organization": "org-1",
        "Authorization": "Bearer sk-client-own",
    }


async def test_catch_all_never_forwards_tiresias_key(legacy):
    client, key = legacy
    with respx.mock:
        route = respx.get("http://mock-upstream/v1/models").mock(return_value=HttpxResponse(200, json={"data": []}))
        resp = await client.get("/v1/models", headers=_hostile_headers(key))
    assert resp.status_code == 200
    sent = _sent(route)
    names = {k for k, _ in sent}
    for h in NEVER_FORWARDED:
        assert h not in names, f"{h} reached the upstream"
    assert not any(key in v for _, v in sent), "the Tiresias key value reached the upstream"
    # Allowlisted headers still arrive.
    assert _values(sent, "openai-organization") == ["org-1"]
    assert _values(sent, "authorization") == ["Bearer sk-client-own"]


async def test_catch_all_strips_authorization_when_it_carried_the_tiresias_key(legacy):
    client, key = legacy
    with respx.mock:
        route = respx.get("http://mock-upstream/v1/models").mock(return_value=HttpxResponse(200, json={"data": []}))
        resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    sent = _sent(route)
    assert _values(sent, "authorization") == []
    assert not any(key in v for _, v in sent)


async def test_streaming_chat_never_forwards_tiresias_key(legacy):
    client, key = legacy
    sse = b'data: {"id":"c","choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
    with respx.mock:
        route = respx.post("http://mock-upstream/v1/chat/completions").mock(
            return_value=HttpxResponse(200, content=sse, headers={"content-type": "text/event-stream"}))
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o-mini", "stream": True, "messages": [{"role": "user", "content": "x"}]},
            headers=_hostile_headers(key),
        )
        await resp.aread()
    sent = _sent(route)
    names = {k for k, _ in sent}
    for h in NEVER_FORWARDED:
        assert h not in names, f"{h} reached the upstream"
    assert not any(key in v for _, v in sent)


async def test_routed_chat_sends_only_the_provider_credential(routed):
    client, key = routed
    with respx.mock:
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=HttpxResponse(200, json=CHAT_OK))
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "x"}]},
            headers=_hostile_headers(key),
        )
    assert resp.status_code == 200
    sent = _sent(route)
    names = {k for k, _ in sent}
    for h in NEVER_FORWARDED:
        assert h not in names, f"{h} reached the upstream"
    assert not any(key in v for _, v in sent)
    # Exactly one Authorization, the provider's. A case-sensitive dict merge
    # used to send the client's lower-cased one as well.
    assert _values(sent, "authorization") == ["Bearer sk-provider-env"]


@pytest.fixture
async def generic(tmp_path):
    settings = _settings(tmp_path, TIRESIAS_UPSTREAM_URL="http://mock-stripe",
                         TIRESIAS_GENERIC_PROXY_MODE=True, TIRESIAS_API_SERVICE="stripe")
    key = await provision_tenant_key(settings)
    app = create_app(settings=settings)
    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c, key
    await close_all_engines()


async def test_generic_proxy_strips_tiresias_headers_keeps_api_headers(generic):
    client, key = generic
    with respx.mock:
        route = respx.get("http://mock-stripe/v1/charges").mock(return_value=HttpxResponse(200, json={}))
        resp = await client.get(
            "/api/v1/charges",
            headers={
                "X-Tiresias-Api-Key": key,
                "X-Tiresias-Session-Id": "s",
                "X-SoulKey": "soul-secret",
                "Stripe-Version": "2024-06-20",
                "Authorization": "Bearer sk_live_customer",
            },
        )
    assert resp.status_code == 200
    sent = _sent(route)
    names = {k for k, _ in sent}
    for h in ("x-tiresias-api-key", "x-tiresias-session-id", "x-soulkey"):
        assert h not in names, f"{h} reached the upstream"
    assert not any(key in v for _, v in sent)
    assert _values(sent, "stripe-version") == ["2024-06-20"]
    assert _values(sent, "authorization") == ["Bearer sk_live_customer"]


def test_saas_bearer_key_is_never_forwarded():
    """A Bearer tir_... value is the SaaS key format, whoever accepted it."""
    out = llm_upstream_headers({"authorization": "Bearer tir_acme_0123456789abcdef", "accept": "x"})
    assert "authorization" not in out
    assert out == {"accept": "x"}
