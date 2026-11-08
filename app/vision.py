"""Reads handwritten math from a photo with Claude, or Gemini on Vertex AI.

Only transcribes. The verdict still comes from detector.analyze, which treats
these lines like any other untrusted student input. Images are never stored.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from typing import List, Optional

import httpx

from . import claude
from .ai import _google_token, gemini_ready
from .detector import MAX_LINES

TIMEOUT = float(os.getenv("VISION_TIMEOUT_SECONDS", "30"))
PROMPT = (
    "This is a photo of a student's handwritten math work. Transcribe each handwritten "
    "line exactly as written, from top to bottom. Never correct mistakes, never solve "
    "anything, and never add steps, even when the work is wrong. Use plain keyboard math "
    "such as x^2 and 3x+2=11, with no LaTeX. "
    'Reply with JSON only: {"lines": ["<first line>", "<second line>"]}. '
    'If there is no handwriting, reply {"lines": []}.'
)


def _parse_lines(text: str) -> List[str]:
    """Raises ValueError on anything other than a list of strings."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ValueError
    lines = json.loads(match.group(0)).get("lines")
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise ValueError
    return [line.strip() for line in lines if line.strip()][:MAX_LINES]


async def _call_gemini(image: bytes, mime: str) -> List[str]:
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
    token = await asyncio.get_running_loop().run_in_executor(None, _google_token)
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(
            f"https://{host}/v1/projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "contents": [{
                    "role": "user",
                    "parts": [
                        {"inlineData": {"mimeType": mime, "data": base64.b64encode(image).decode("ascii")}},
                        {"text": PROMPT},
                    ],
                }],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
            },
        )
    response.raise_for_status()
    parts = response.json()["candidates"][0]["content"]["parts"]
    return _parse_lines("".join(p.get("text", "") for p in parts))


def ready() -> bool:
    return claude.ready() or gemini_ready()


async def _call_claude(image: bytes, mime: str) -> List[str]:
    return _parse_lines(await claude.read_image(image, mime, PROMPT))


async def transcribe(image: bytes, mime: str) -> Optional[List[str]]:
    """Returns the handwritten lines, or None if no reader is on or every call failed."""
    if claude.ready():
        try:
            return await asyncio.wait_for(_call_claude(image, mime), TIMEOUT)
        except Exception as exc:
            print(f"[vision] claude failed: {type(exc).__name__}")
    if not gemini_ready():
        return None
    try:
        # wait_for also bounds the token refresh, which httpx's timeout does not cover.
        return await asyncio.wait_for(_call_gemini(image, mime), TIMEOUT)
    except Exception as exc:
        # Log the class only. Error bodies can echo request data.
        print(f"[vision] gemini failed: {type(exc).__name__}")
        return None
