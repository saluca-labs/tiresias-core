import json
import pytest
from pathlib import Path

from asgi_lifespan import LifespanManager
from httpx import AsyncClient, ASGITransport

from tiresias.config import TiresiasSettings
from tests.conftest import provision_tenant_key
from tiresias.proxy.app import create_app, _assemble_sse_response, _detect_provider
from tiresias.storage.engine import close_all_engines

import pytest


@pytest.fixture
def tmp_settings(tmp_path):
    return TiresiasSettings(
        TIRESIAS_TENANT_ID='proxy-test-tenant',
        TIRESIAS_KEK_PROVIDER='local',
        TIRESIAS_DATA_ROOT=tmp_path,
        TIRESIAS_UPSTREAM_URL='http://mock-upstream',
        TIRESIAS_KEK='45286db43824c34cf7865ae507579d754ffefd59816432d5c5513e25bb7995a4',
    )


@pytest.fixture
async def test_app_client(tmp_settings):
    api_key = await provision_tenant_key(tmp_settings)
    app = create_app(settings=tmp_settings)
    async with LifespanManager(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-Tiresias-Api-Key": api_key},
        ) as client:
            yield client
    await close_all_engines()


def test_detect_provider_openai():
    assert _detect_provider('https://api.openai.com') == 'openai'


def test_detect_provider_anthropic():
    assert _detect_provider('https://api.anthropic.com') == 'anthropic'


def test_detect_provider_groq():
    assert _detect_provider('https://api.groq.com') == 'groq'


def test_detect_provider_gemini():
    assert _detect_provider('https://generativelanguage.googleapis.com') == 'gemini'


def test_assemble_sse_response_basic():
    sse_data = (
        'data: {\"id\":\"c1\",\"choices\":[{\"delta\":{\"content\":\"Hello\"},\"finish_reason\":null}]}\n'
        'data: {\"id\":\"c1\",\"choices\":[{\"delta\":{\"content\":\" World\"},\"finish_reason\":\"stop\"}]}\n'
        'data: [DONE]\n'
    )
    result = _assemble_sse_response(sse_data, 'gpt-4o-mini')
    assert result['id'] == 'c1'
    assert result['choices'][0]['message']['content'] == 'Hello World'
    assert result['choices'][0]['finish_reason'] == 'stop'
    assert result['usage']['completion_tokens'] > 0


def test_assemble_sse_response_empty():
    result = _assemble_sse_response('', 'gpt-4o-mini')
    assert result['choices'][0]['message']['content'] == ''


async def test_health_endpoint(test_app_client):
    resp = await test_app_client.get('/health')
    assert resp.status_code == 200
    data = resp.json()
    assert data['status'] == 'ok'



async def test_chat_completions_forwarded(test_app_client, tmp_settings):
    import respx
    from httpx import Response as HttpxResponse

    upstream_response = {
        'id': 'chatcmpl-test',
        'object': 'chat.completion',
        'model': 'gpt-4o-mini',
        'choices': [{
            'message': {'role': 'assistant', 'content': 'Hello!'},
            'finish_reason': 'stop',
            'index': 0,
        }],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
    }

    with respx.mock:
        respx.post('http://mock-upstream/v1/chat/completions').mock(
            return_value=HttpxResponse(200, json=upstream_response)
        )
        resp = await test_app_client.post(
            '/v1/chat/completions',
            json={
                'model': 'gpt-4o-mini',
                'messages': [{'role': 'user', 'content': 'Hello'}],
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data['id'] == 'chatcmpl-test'
    assert data['choices'][0]['message']['content'] == 'Hello!'


async def test_session_id_header_captured(test_app_client, tmp_settings):
    import respx
    from httpx import Response as HttpxResponse

    upstream_response = {
        'id': 'chatcmpl-sess',
        'object': 'chat.completion',
        'model': 'gpt-4o-mini',
        'choices': [{'message': {'role': 'assistant', 'content': 'Hi'}, 'finish_reason': 'stop', 'index': 0}],
        'usage': {'prompt_tokens': 5, 'completion_tokens': 3, 'total_tokens': 8},
    }

    with respx.mock:
        respx.post('http://mock-upstream/v1/chat/completions').mock(
            return_value=HttpxResponse(200, json=upstream_response)
        )
        resp = await test_app_client.post(
            '/v1/chat/completions',
            headers={'x-tiresias-session-id': 'test-session-123'},
            json={
                'model': 'gpt-4o-mini',
                'messages': [{'role': 'user', 'content': 'Hello'}],
            },
        )

    assert resp.status_code == 200

    stats = await test_app_client.get('/v1/sessions/test-session-123')
    assert stats.status_code == 200
    data = stats.json()
    assert data['session_id'] == 'test-session-123'
    assert data['request_count'] == 1
    assert data['total_tokens'] == 8
