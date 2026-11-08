"""Claude on the Anthropic API: reads photos, writes teacher briefs, and runs Pip's chat.

The key comes from ANTHROPIC_API_KEY in the host's environment (Railway), never from
the repository. Claude never decides whether math is right: the SymPy detector does.
"""
from __future__ import annotations

import base64
import os
from typing import List, Optional

import httpx

API = "https://api.anthropic.com/v1/messages"
FAST_MODEL = os.getenv("CLAUDE_FAST_MODEL", "claude-haiku-5-5")
VISION_MODEL = os.getenv("CLAUDE_VISION_MODEL", "claude-sonnet-5-5")
CHAT_MODEL = os.getenv("CLAUDE_CHAT_MODEL", "claude-sonnet-5-5")
TIMEOUT = float(os.getenv("CLAUDE_TIMEOUT_SECONDS", "25"))


def ready() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _headers() -> dict:
    return {
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


async def messages(body: dict, client: Optional[httpx.AsyncClient] = None) -> dict:
    """One Messages API call. Raises on HTTP errors so callers can fall back."""
    if client is None:
        async with httpx.AsyncClient(timeout=TIMEOUT) as own:
            response = await own.post(API, headers=_headers(), json=body)
    else:
        response = await client.post(API, headers=_headers(), json=body)
    response.raise_for_status()
    return response.json()


def text_of(reply: dict) -> str:
    return "".join(block.get("text", "") for block in reply.get("content", []) if block.get("type") == "text")


async def complete(system: str, prompt: str, client: httpx.AsyncClient, model: str = FAST_MODEL) -> str:
    reply = await messages({
        "model": model, "max_tokens": 400, "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }, client)
    return text_of(reply)


async def read_image(image: bytes, mime: str, prompt: str) -> str:
    reply = await messages({
        "model": VISION_MODEL, "max_tokens": 800,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime,
                                             "data": base64.b64encode(image).decode("ascii")}},
                {"type": "text", "text": prompt},
            ],
        }],
    })
    return text_of(reply)


async def chat(system: str, history: List[dict], tools: List[dict], client: httpx.AsyncClient) -> dict:
    return await messages({
        "model": CHAT_MODEL, "max_tokens": 500,
        "system": system, "tools": tools, "messages": history,
    }, client)
