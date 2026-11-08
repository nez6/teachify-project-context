"""Photo capture routes, with the Gemini call stubbed."""
import asyncio
import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import ai, main, vision

JPEG = b"\xff\xd8\xff\xe0" + b"photo"
HEADERS = {"Content-Type": "image/jpeg", "X-Device-Token": "secret"}
SAME_ORIGIN = {"Content-Type": "image/jpeg", "Origin": "http://testserver"}
WRONG = ["3x+2=2x+11", "x+2=11", "x=9"]
RIGHT = ["3x+6=2x+11", "x+6=11", "x=5"]


@pytest.fixture
def client(monkeypatch):
    async def no_brief(*args):
        return None

    monkeypatch.setenv("DEVICE_TOKEN", "secret")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr(ai, "write_brief", no_brief)
    main.recent_photos.clear()
    with TestClient(main.app) as test_client:
        test_client.post("/api/demo/reset")
        yield test_client


def read_as(monkeypatch, lines):
    async def fake(image, mime):
        assert image == JPEG and mime == "image/jpeg"
        return lines

    monkeypatch.setattr(vision, "transcribe", fake)


def test_camera_flags_error_and_lights_desk(client, monkeypatch):
    read_as(monkeypatch, WRONG)
    response = client.post("/api/capture/desk-07", content=JPEG, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"verdict": "error", "first_error": 0, "led": "waiting"}
    alert = client.get("/api/state").json()["alerts"][0]
    assert alert["student_id"] == "12" and alert["source"] == "camera"


def test_camera_fix_resolves_alert(client, monkeypatch):
    read_as(monkeypatch, WRONG)
    client.post("/api/capture/desk-07", content=JPEG, headers=HEADERS)
    read_as(monkeypatch, RIGHT)
    body = client.post("/api/capture/desk-07", content=JPEG, headers=HEADERS).json()
    assert body == {"verdict": "correct", "first_error": None, "led": "resolved"}


def test_acknowledged_alert_keeps_blue_light(client, monkeypatch):
    read_as(monkeypatch, WRONG)
    client.post("/api/capture/desk-07", content=JPEG, headers=HEADERS)
    alert = client.get("/api/state").json()["alerts"][0]
    client.post(f"/api/alert/{alert['id']}/ack")
    read_as(monkeypatch, ["3x+2=2x+11", "x=9"])
    body = client.post("/api/capture/desk-07", content=JPEG, headers=HEADERS).json()
    assert body["led"] == "coming"


def test_camera_is_off_without_token(client, monkeypatch):
    monkeypatch.delenv("DEVICE_TOKEN")
    assert client.post("/api/capture/desk-07", content=JPEG, headers=HEADERS).status_code == 503


@pytest.mark.parametrize("token", [None, "wrong"])
def test_camera_rejects_bad_token(client, token):
    headers = {"Content-Type": "image/jpeg"}
    if token:
        headers["X-Device-Token"] = token
    assert client.post("/api/capture/desk-07", content=JPEG, headers=headers).status_code == 401


def test_unknown_device_and_problem(client):
    assert client.post("/api/capture/nope", content=JPEG, headers=HEADERS).status_code == 404
    url = "/api/capture/desk-07?problem_id=nope"
    assert client.post(url, content=JPEG, headers=HEADERS).status_code == 404


def test_rejects_wrong_type_non_jpeg_and_oversize(client, monkeypatch):
    read_as(monkeypatch, WRONG)
    plain = {**HEADERS, "Content-Type": "text/plain"}
    assert client.post("/api/capture/desk-07", content=JPEG, headers=plain).status_code == 415
    assert client.post("/api/capture/desk-07", content=b"not a jpeg", headers=HEADERS).status_code == 400
    monkeypatch.setattr(main, "MAX_CAPTURE_BYTES", 4)
    assert client.post("/api/capture/desk-07", content=JPEG, headers=HEADERS).status_code == 413


def test_failed_reading_and_blank_page(client, monkeypatch):
    read_as(monkeypatch, None)
    assert client.post("/api/capture/desk-07", content=JPEG, headers=HEADERS).status_code == 502
    read_as(monkeypatch, [])
    body = client.post("/api/capture/desk-07", content=JPEG, headers=HEADERS).json()
    assert body == {"verdict": "unreadable", "first_error": None, "led": "idle"}


def test_photo_off_without_google_project(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT")
    assert client.post("/api/capture/desk-07", content=JPEG, headers=HEADERS).status_code == 503


def test_transcribed_text_must_pass_whitelist(client, monkeypatch):
    read_as(monkeypatch, ["__import__('os').system('touch /tmp/pwn')", "3x+6=2x+11"])
    rows = client.post("/api/photo/12", content=JPEG, headers=SAME_ORIGIN).json()["rows"]
    assert rows[0]["status"] == "unread"
    assert rows[1]["status"] == "ok"


def test_student_photo_needs_no_token_but_must_be_same_origin(client, monkeypatch):
    monkeypatch.delenv("DEVICE_TOKEN")
    read_as(monkeypatch, WRONG)
    response = client.post("/api/photo/12", content=JPEG, headers=SAME_ORIGIN)
    assert response.status_code == 200
    assert response.json()["lines"] == WRONG and response.json()["verdict"] == "error"
    other = {**SAME_ORIGIN, "Origin": "https://evil.example"}
    assert client.post("/api/photo/12", content=JPEG, headers=other).status_code == 403
    assert client.post("/api/photo/12", content=JPEG, headers={"Content-Type": "image/jpeg"}).status_code == 403


def test_student_photo_is_rate_limited(client, monkeypatch):
    read_as(monkeypatch, WRONG)
    monkeypatch.setattr(main, "PHOTOS_PER_MINUTE", 1)
    assert client.post("/api/photo/12", content=JPEG, headers=SAME_ORIGIN).status_code == 200
    assert client.post("/api/photo/12", content=JPEG, headers=SAME_ORIGIN).status_code == 429


def test_transcribe_sends_image_and_only_asks_for_a_copy(monkeypatch):
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        text = json.dumps({"lines": [" 3x+2=2x+11 ", ""]})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})

    real_client = httpx.AsyncClient
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr(vision, "_google_token", lambda: "token")
    monkeypatch.setattr(vision.httpx, "AsyncClient", lambda timeout: real_client(transport=httpx.MockTransport(handler)))

    assert asyncio.run(vision.transcribe(JPEG, "image/jpeg")) == ["3x+2=2x+11"]
    parts = seen["contents"][0]["parts"]
    assert parts[0]["inlineData"] == {"mimeType": "image/jpeg", "data": base64.b64encode(JPEG).decode("ascii")}
    assert "Never correct mistakes, never solve" in parts[1]["text"]
