"""Short-lived voice configuration. Private provider keys never reach browsers."""
from __future__ import annotations

import os
import time
from collections import deque

import httpx
from fastapi import HTTPException

TOKEN_TTL_SECONDS = 30
TOKEN_REQUESTS_PER_MINUTE = 20
_recent_token_requests: deque[float] = deque()


def status() -> dict:
    return {
        "deepgram": {"configured": bool(os.getenv("DEEPGRAM_API_KEY"))},
        "vapi": {
            "configured": bool(os.getenv("VAPI_PUBLIC_API_KEY") and os.getenv("VAPI_ASSISTANT_ID")),
        },
    }


def vapi_config() -> dict:
    public_key = os.getenv("VAPI_PUBLIC_API_KEY", "")
    assistant_id = os.getenv("VAPI_ASSISTANT_ID", "")
    return {
        "available": bool(public_key and assistant_id),
        "public_key": public_key or None,
        "assistant_id": assistant_id or None,
    }


def limit_token_requests() -> None:
    now = time.monotonic()
    while _recent_token_requests and now - _recent_token_requests[0] > 60:
        _recent_token_requests.popleft()
    if len(_recent_token_requests) >= TOKEN_REQUESTS_PER_MINUTE:
        raise HTTPException(429, "Too many voice connection attempts")
    _recent_token_requests.append(now)


async def deepgram_token() -> dict:
    key = os.getenv("DEEPGRAM_API_KEY", "")
    if not key:
        raise HTTPException(503, "Voice transcription is not configured")
    limit_token_requests()
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.post(
                "https://api.deepgram.com/v1/auth/grant",
                headers={"Authorization": f"Token {key}"},
                json={"ttl_seconds": TOKEN_TTL_SECONDS},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        print(f"[voice] deepgram token failed: {type(exc).__name__}")
        raise HTTPException(502, "Voice transcription is temporarily unavailable") from None
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise HTTPException(502, "Voice transcription returned an invalid session")
    return {"access_token": token, "expires_in": data.get("expires_in", TOKEN_TTL_SECONDS)}
