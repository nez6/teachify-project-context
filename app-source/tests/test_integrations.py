import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from app import ai, main, voice


def test_worksheets_page_has_required_content():
    with TestClient(main.app) as client:
        text = client.get("/worksheets").text
    assert "Helping you maintain your classroom one step at a time" in text
    assert "Arithmetic &amp;" in text and "Fractions &amp;" in text
    assert text.count('class="question"') == 12


def test_health_reports_provider_configuration(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-secret")
    with TestClient(main.app) as client:
        data = client.get("/api/health").json()
    assert data["ai"]["anthropic"]["configured"] is True
    assert data["voice"]["deepgram"]["configured"] is True
    assert "test-secret" not in json.dumps(data)


def test_anthropic_words_hint_without_receiving_unsafe_text(monkeypatch):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"content": [{"type": "text", "text": '{"diagnosis":"Missed one factor","say_this":"What must 3 multiply?"}'}]})

    real_client = httpx.AsyncClient
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")
    monkeypatch.delenv("TINKER_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda timeout: real_client(transport=httpx.MockTransport(handler)))
    ai._cache.clear()
    result = asyncio.run(ai.write_brief("Algebra 1", "3(x+2)=2x+11", ["3x+2=2x+11"], 1, "Partial distribution"))
    assert result["provider"] == "Anthropic Claude"
    assert result["say"].endswith("?")
    assert seen["model"] == "test-model"


def test_voice_config_never_exposes_private_key(monkeypatch):
    monkeypatch.setenv("VAPI_PUBLIC_API_KEY", "public-test")
    monkeypatch.setenv("VAPI_PRIVATE_API_KEY", "private-test")
    monkeypatch.setenv("VAPI_ASSISTANT_ID", "assistant-test")
    with TestClient(main.app) as client:
        data = client.get("/api/voice/config").json()
    encoded = json.dumps(data)
    assert "public-test" in encoded and "assistant-test" in encoded
    assert "private-test" not in encoded


def test_deepgram_token_requires_same_origin(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-secret")
    with TestClient(main.app) as client:
        response = client.post("/api/voice/deepgram-token", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_deepgram_token_missing_key_is_honest(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    with TestClient(main.app) as client:
        response = client.post("/api/voice/deepgram-token", headers={"Origin": "http://testserver"})
    assert response.status_code == 503
