"""System B: writes the teacher's brief. Tries each configured provider in order.

Thinking Machines (Tinker) serves a fine-tuned checkpoint through its
OpenAI-compatible API. Google Cloud serves Gemini through Vertex AI.
If neither responds, the teacher still gets the verified catalog text.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import List, Optional

import httpx

from . import claude

SYSTEM = (
    "You help a busy math teacher during class. The math was already checked by a "
    "computer algebra system, so do not re-check it and do not change its verdict. "
    "Reply with JSON only: "
    '{"diagnosis": "<under 20 words, what went wrong>", '
    '"say_this": "<under 25 words, one question the teacher can ask that does not give the answer>"}'
)
TIMEOUT = float(os.getenv("AI_TIMEOUT_SECONDS", "10"))
TINKER_DEFAULT_URL = "https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1"


def tinker_ready() -> bool:
    return bool(os.getenv("TINKER_API_KEY") and os.getenv("TINKER_MODEL"))


def gemini_ready() -> bool:
    return bool(os.getenv("GOOGLE_CLOUD_PROJECT"))


def status() -> dict:
    return {
        "claude": {"configured": claude.ready(), "model": claude.FAST_MODEL, "last_ok": _last_ok.get("claude")},
        "tinker": {"configured": tinker_ready(), "last_ok": _last_ok.get("tinker")},
        "gemini": {
            "configured": gemini_ready(),
            "model": os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
            "last_ok": _last_ok.get("gemini"),
        },
    }


_last_ok: dict = {}
_cache: dict = {}
_limit = asyncio.Semaphore(3)


def _user_prompt(course: str, problem: str, lines: List[str], bad_line: Optional[int], label: str) -> str:
    work = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines)) or "(no work submitted)"
    where = f"First invalid line: {bad_line}." if bad_line else "No invalid line was found."
    return f"Course: {course}\nProblem: {problem}\nStudent work:\n{work}\n{where}\nDetected pattern: {label}"


def _parse(text: str) -> Optional[dict]:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    diagnosis, say = data.get("diagnosis"), data.get("say_this")
    if not isinstance(diagnosis, str) or not isinstance(say, str) or not diagnosis or not say:
        return None
    return {"diagnosis": diagnosis[:240], "say": say[:280]}


async def _tinker(client: httpx.AsyncClient, prompt: str) -> Optional[dict]:
    base = os.getenv("TINKER_BASE_URL", TINKER_DEFAULT_URL).rstrip("/")
    response = await client.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['TINKER_API_KEY']}"},
        json={
            "model": os.environ["TINKER_MODEL"],
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "max_tokens": 300,
            "temperature": 0.2,
        },
    )
    response.raise_for_status()
    return _parse(response.json()["choices"][0]["message"]["content"])


def _google_token() -> str:
    import google.auth
    from google.auth.transport.requests import Request

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(Request())
    return credentials.token


async def _gemini(client: httpx.AsyncClient, prompt: str) -> Optional[dict]:
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    token = await asyncio.get_event_loop().run_in_executor(None, _google_token)
    response = await client.post(
        f"https://{host}/v1/projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        },
    )
    response.raise_for_status()
    parts = response.json()["candidates"][0]["content"]["parts"]
    return _parse("".join(p.get("text", "") for p in parts))


async def _claude(client: httpx.AsyncClient, prompt: str) -> Optional[dict]:
    return _parse(await claude.complete(SYSTEM, prompt, client))


async def write_brief(course: str, problem: str, lines: List[str], bad_line: Optional[int], label: str) -> Optional[dict]:
    """Returns {diagnosis, say, provider, latency_ms}, or None to keep the catalog text."""
    key = (problem, tuple(lines), label)
    if key in _cache:
        return _cache[key]

    prompt = _user_prompt(course, problem, lines, bad_line, label)
    providers = []
    if claude.ready():
        providers.append(("claude", "Claude", _claude))
    if tinker_ready():
        providers.append(("tinker", "Thinking Machines", _tinker))
    if gemini_ready():
        providers.append(("gemini", "Google Cloud Gemini", _gemini))

    async with _limit:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            for key_name, display, call in providers:
                started = time.time()
                try:
                    result = await call(client, prompt)
                except Exception as exc:
                    # Log the class only. Error bodies can echo request data.
                    print(f"[ai] {key_name} failed: {type(exc).__name__}")
                    _last_ok[key_name] = False
                    continue
                if result:
                    _last_ok[key_name] = True
                    result.update(provider=display, latency_ms=int((time.time() - started) * 1000))
                    _cache[key] = result
                    return result
                _last_ok[key_name] = False
    return None
