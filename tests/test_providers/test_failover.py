"""Integration tests for multi-provider failover via the FastAPI proxy."""
import json
import pytest
import respx
from httpx import AsyncClient, ASGITransport, Response as HttpxResponse
from asgi_lifespan import LifespanManager

from tiresias.config import TiresiasSettings
from tests.conftest import provision_tenant_key
from tiresias.proxy.app import create_app
from tiresias.storage.engine import close_all_engines


@pytest.fixture
def multi_provider_settings(tmp_path):
    return TiresiasSettings(
        TIRESIAS_TENANT_ID="failover-test-tenant",
        TIRESIAS_KEK_PROVIDER="local",
        TIRESIAS_DATA_ROOT=tmp_path,
        TIRESIAS_UPSTREAM_URL="https://api.openai.com",
        TIRESIAS_PROVIDERS="openai,anthropic",
        TIRESIAS_KEK="45286db43824c34cf7865ae507579d754ffefd59816432d5c5513e25bb7995a4",
    )


@pytest.fixture
async def failover_client(multi_provider_settings, monkeypatch):
    # Set API keys in env so build_provider can find them
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test-key")
    api_key = await provision_tenant_key(multi_provider_settings)
    app = create_app(settings=multi_provider_settings)
    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Tiresias-Api-Key": api_key},
        ) as client:
            yield client
    await close_all_engines()


def _openai_response(content: str = "OpenAI response") -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "gpt-4o-mini",
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop", "index": 0}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _anthropic_response(content: str = "Anthropic response") -> dict:
    return {
        "id": "msg_abc",
        "model": "claude-3-haiku-20240307",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": content}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


async def test_primary_500_triggers_failover_to_secondary(failover_client):
    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=HttpxResponse(500, json={"error": "Server error"})
        )
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=HttpxResponse(200, json=_anthropic_response("Failover success"))
        )
        resp = await failover_client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["message"]["content"] == "Failover success"


async def test_failover_transparent_to_client(failover_client):
    """Client sees the same OpenAI response format regardless of which provider served the request."""
    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=HttpxResponse(500)
        )
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=HttpxResponse(200, json=_anthropic_response("Transparent failover"))
        )
        resp = await failover_client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    # Must conform to OpenAI response format
    assert "choices" in data
    assert "message" in data["choices"][0]
    assert "content" in data["choices"][0]["message"]
    assert "usage" in data


async def test_all_providers_fail_returns_502(failover_client):
    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=HttpxResponse(500)
        )
        respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=HttpxResponse(503)
        )
        resp = await failover_client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
    assert resp.status_code == 502


async def test_admin_providers_endpoint(failover_client):
    resp = await failover_client.get("/v1/admin/providers")
    assert resp.status_code == 200
    data = resp.json()
    assert "cascade" in data
    assert "providers" in data
    assert "openai" in data["cascade"]
    assert "anthropic" in data["cascade"]


async def test_admin_reload_endpoint(failover_client, monkeypatch):
    monkeypatch.setenv("TIRESIAS_PROVIDERS", "openai,anthropic")
    resp = await failover_client.post("/v1/admin/reload")
    assert resp.status_code == 200
    data = resp.json()
    assert data["reloaded"] is True
    assert "cascade" in data


async def test_primary_succeeds_no_failover(failover_client):
    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=HttpxResponse(200, json=_openai_response("Primary response"))
        )
        resp = await failover_client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["message"]["content"] == "Primary response"
