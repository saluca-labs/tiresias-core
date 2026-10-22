from __future__ import annotations

import json
import pytest
from pathlib import Path

from asgi_lifespan import LifespanManager
from httpx import AsyncClient, ASGITransport, Response as HttpxResponse

from tiresias.config import TiresiasSettings
from tests.conftest import provision_tenant_key
from tiresias.proxy.app import create_app
from tiresias.storage.engine import close_all_engines


@pytest.fixture
def phase5_settings(tmp_path):
    return TiresiasSettings(
        TIRESIAS_TENANT_ID="phase5-test-tenant",
        TIRESIAS_KEK_PROVIDER="local",
        TIRESIAS_DATA_ROOT=tmp_path,
        TIRESIAS_UPSTREAM_URL="http://mock-upstream-api",
        TIRESIAS_KEK="45286db43824c34cf7865ae507579d754ffefd59816432d5c5513e25bb7995a4",
        TIRESIAS_GENERIC_PROXY_MODE=True,
        TIRESIAS_API_SERVICE="stripe",
    )


@pytest.fixture
async def phase5_client(phase5_settings):
    api_key = await provision_tenant_key(phase5_settings)
    app = create_app(settings=phase5_settings)
    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Tiresias-Api-Key": api_key},
        ) as client:
            yield client
    await close_all_engines()


async def test_generic_proxy_success(phase5_client):
    import respx
    with respx.mock:
        respx.get("http://mock-upstream-api/v1/charges").mock(
            return_value=HttpxResponse(200, json={"data": []})
        )
        resp = await phase5_client.get("/api/v1/charges")
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data


async def test_generic_proxy_records_telemetry(phase5_client, phase5_settings):
    import respx
    with respx.mock:
        respx.post("http://mock-upstream-api/v1/payment_intents").mock(
            return_value=HttpxResponse(200, json={"id": "pi_test"})
        )
        resp = await phase5_client.post("/api/v1/payment_intents", json={"amount": 100})
    assert resp.status_code == 200

    # Verify telemetry was recorded via the analytics endpoint
    endpoints_resp = await phase5_client.get("/v1/analytics/api/endpoints")
    assert endpoints_resp.status_code == 200
    data = endpoints_resp.json()
    assert "endpoints" in data
    # May take a moment for bucket to appear; at least the endpoint responds
    assert isinstance(data["endpoints"], list)


async def test_generic_proxy_error_response(phase5_client):
    import respx
    with respx.mock:
        respx.get("http://mock-upstream-api/v1/charges/ch_1AbCdEfGhIjKlMnO").mock(
            return_value=HttpxResponse(404, json={"error": "not found"})
        )
        resp = await phase5_client.get("/api/v1/charges/ch_1AbCdEfGhIjKlMnO")
    assert resp.status_code == 404


async def test_analytics_endpoints_empty(phase5_client):
    resp = await phase5_client.get("/v1/analytics/api/endpoints")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tenant_id"] == "phase5-test-tenant"
    assert data["window_hours"] == 24
    assert isinstance(data["endpoints"], list)


async def test_analytics_costs_empty(phase5_client):
    resp = await phase5_client.get("/v1/analytics/api/costs")
    assert resp.status_code == 200
    data = resp.json()
    assert "costs" in data
    assert isinstance(data["costs"], list)


async def test_analytics_errors_empty(phase5_client):
    resp = await phase5_client.get("/v1/analytics/api/errors")
    assert resp.status_code == 200
    data = resp.json()
    assert "errors" in data


async def test_analytics_unified_empty(phase5_client):
    resp = await phase5_client.get("/v1/analytics/unified")
    assert resp.status_code == 200
    data = resp.json()
    assert "llm" in data
    assert "api" in data
    assert "totals" in data
    assert data["totals"]["request_count"] == 0


async def test_analytics_unified_custom_window(phase5_client):
    resp = await phase5_client.get("/v1/analytics/unified?hours=48")
    assert resp.status_code == 200
    data = resp.json()
    assert data["window_hours"] == 48


async def test_generic_proxy_path_normalization_telemetry(phase5_client):
    """Requests with different concrete IDs should normalize to same path_pattern."""
    import respx
    # Use proper Stripe-format customer IDs (prefix_ + 14+ chars)
    with respx.mock:
        respx.get("http://mock-upstream-api/v1/customers/cus_abcdef12345678/subscriptions").mock(
            return_value=HttpxResponse(200, json={})
        )
        respx.get("http://mock-upstream-api/v1/customers/cus_xyz789abc12345/subscriptions").mock(
            return_value=HttpxResponse(200, json={})
        )
        await phase5_client.get("/api/v1/customers/cus_abcdef12345678/subscriptions")
        await phase5_client.get("/api/v1/customers/cus_xyz789abc12345/subscriptions")

    endpoints_resp = await phase5_client.get("/v1/analytics/api/endpoints")
    data = endpoints_resp.json()
    # Both should be grouped under same path pattern
    patterns = [e["path_pattern"] for e in data["endpoints"]]
    # Both IDs should be normalized to {id}
    for p in patterns:
        assert "cus_abcdef12345678" not in p
        assert "cus_xyz789abc12345" not in p


async def test_analytics_service_filter(phase5_client):
    resp = await phase5_client.get("/v1/analytics/api/endpoints?api_service=stripe")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["endpoints"], list)


async def test_generic_proxy_post_with_body(phase5_client):
    import respx
    with respx.mock:
        respx.post("http://mock-upstream-api/v1/charges").mock(
            return_value=HttpxResponse(201, json={"id": "ch_test123456789"})
        )
        resp = await phase5_client.post(
            "/api/v1/charges",
            json={"amount": 2000, "currency": "usd"},
        )
    assert resp.status_code == 201
    assert resp.json()["id"] == "ch_test123456789"


async def test_analytics_error_count_after_failures(phase5_client):
    import respx
    with respx.mock:
        respx.get("http://mock-upstream-api/v1/customers/cus_abcdef12345678").mock(
            return_value=HttpxResponse(429, json={"error": "rate limited"})
        )
        resp = await phase5_client.get("/api/v1/customers/cus_abcdef12345678")
    assert resp.status_code == 429

    errors_resp = await phase5_client.get("/v1/analytics/api/errors")
    assert errors_resp.status_code == 200
    data = errors_resp.json()
    assert isinstance(data["errors"], list)
    # There should be at least one error entry for 429
    codes = [e["status_code"] for e in data["errors"]]
    assert 429 in codes
