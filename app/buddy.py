"""Pip, the talking buddy: chat with Claude, voice with Deepgram, live calls with Vapi.

Every key lives in the host's environment. The browser only ever sees the Vapi
public key, which Vapi designs to be public. Pip's tools run the same SymPy
detector as the typed student app, so a hint is never an LLM's guess about math.
"""
from __future__ import annotations

import hmac
import json
import os
import time
from collections import deque
from typing import List, Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import claude

router = APIRouter()
STUDENT = "12"
DEVICE = "desk-07"
DEEPGRAM = "https://api.deepgram.com/v1"
TTS_VOICE = os.getenv("DEEPGRAM_VOICE", "aura-2-thalia-en")
MAX_AUDIO_BYTES = 4 * 1024 * 1024
LIMITS = {"chat": 40, "tts": 80, "stt": 40}
_hits = {name: deque() for name in LIMITS}

SYSTEM = """You are Pip, a warm, funny, patient math buddy for kids and teens.
You talk out loud, so keep every reply to one to three short sentences a 10 year old understands.
Golden rule: never give the final answer and never write the corrected line. Point to WHERE the
mistake is and ask ONE question that helps them see it. Celebrate effort, especially fixing their own mistake.
Use your tools to do things instead of describing them:
- check_work whenever the kid shares steps (each step one line, like 3x+6=2x+11). Trust its verdict completely;
  it comes from a math engine, not from you. Never re-solve the problem yourself.
- list_problems to see what is on today's sheet.
- call_teacher when the kid says they are stuck, upset, or asks for a real person.
- open_lesson when they want to learn, play, or seem confused about what solving even means.
If they ask about something unsafe or unrelated to learning, gently steer back to math.
Never ask for their full name, address, school or any personal details."""

TOOLS = [
    {
        "name": "check_work",
        "description": "Check a student's math steps with the algebra engine. Returns the verdict, the first wrong line, and a hint that does not reveal the answer.",
        "input_schema": {
            "type": "object",
            "properties": {
                "problem_id": {"type": "string", "description": "dist, move, factor or deriv"},
                "lines": {"type": "array", "items": {"type": "string"}, "description": "One math step per item, in order."},
            },
            "required": ["problem_id", "lines"],
        },
    },
    {
        "name": "list_problems",
        "description": "List today's practice problems with their ids.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "call_teacher",
        "description": "Raise the student's hand: the teacher dashboard gets an alert and the desk light turns on.",
        "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}},
    },
    {
        "name": "open_lesson",
        "description": "Open the 5 minute animated Magic Seesaw lesson about balancing equations.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def limit(name: str) -> None:
    now = time.time()
    hits = _hits[name]
    while hits and now - hits[0] > 60:
        hits.popleft()
    if len(hits) >= LIMITS[name]:
        raise HTTPException(429, "Pip needs a tiny break. Try again in a minute.")
    hits.append(now)


def problem_text(problem_id: str) -> str:
    from .main import PROBLEMS
    p = PROBLEMS.get(problem_id)
    return f"{p['prompt']}: {p['text']}" if p else "unknown problem"


async def run_tool(name: str, args: dict) -> dict:
    """Returns {result: str for the model, action: dict for the page}."""
    from .main import PROBLEMS, Attempt, handle_attempt, press, Press

    if name == "list_problems":
        items = [{"id": p["id"], "course": p["course"], "problem": f"{p['prompt']}: {p['text']}"} for p in PROBLEMS.values()]
        return {"result": json.dumps(items), "action": {"type": "problems", "items": items}}

    if name == "check_work":
        problem_id = str(args.get("problem_id") or "dist")
        if problem_id not in PROBLEMS:
            problem_id = "dist"
        lines = [str(line)[:80] for line in (args.get("lines") or []) if str(line).strip()][:12]
        if not lines:
            return {"result": "No steps were given. Ask the student to say their first step.", "action": None}
        outcome = await handle_attempt(Attempt(student_id=STUDENT, problem_id=problem_id, lines=lines))
        verdict = outcome["verdict"]
        summary = {"verdict": verdict, "problem": problem_text(problem_id)}
        if verdict == "error":
            summary.update(first_wrong_line=outcome["first_error"] + 1, hint=outcome.get("nudge"),
                           rule="Do not reveal the right line or the answer.")
        elif verdict == "correct":
            summary.update(xp_earned=(outcome.get("reward") or {}).get("points"),
                           comeback=(outcome.get("reward") or {}).get("comeback"))
        elif verdict == "in_progress":
            summary.update(note="Every step so far is valid. Encourage the next step.")
        return {"result": json.dumps(summary), "action": {
            "type": "work", "problem_id": problem_id, "problem": problem_text(problem_id),
            "rows": outcome["rows"], "verdict": verdict, "first_error": outcome["first_error"],
            "nudge": outcome.get("nudge"), "reward": outcome.get("reward"),
        }}

    if name == "call_teacher":
        await press(DEVICE, Press(source="virtual", problem_id="dist"))
        return {"result": "Done. The teacher can see the raised hand now.", "action": {"type": "teacher"}}

    if name == "open_lesson":
        return {"result": "The lesson is opening on the student's screen.", "action": {"type": "lesson"}}

    return {"result": f"Unknown tool {name}", "action": None}


class ChatTurn(BaseModel):
    role: str
    content: str = Field(max_length=2000)


class ChatBody(BaseModel):
    messages: List[ChatTurn] = Field(max_length=30)


@router.post("/api/buddy/chat")
async def buddy_chat(body: ChatBody):
    """Text or push-to-talk chat. Claude may call tools up to four times per turn."""
    if not claude.ready():
        raise HTTPException(503, "Pip's brain is off. Set ANTHROPIC_API_KEY.")
    limit("chat")
    history = [{"role": "assistant" if t.role == "assistant" else "user", "content": t.content}
               for t in body.messages if t.content.strip()][-20:]
    while history and history[0]["role"] != "user":
        history.pop(0)
    if not history:
        raise HTTPException(400, "Say something to Pip first.")
    actions = []
    async with httpx.AsyncClient(timeout=claude.TIMEOUT) as client:
        for _ in range(4):
            try:
                reply = await claude.chat(SYSTEM, history, TOOLS, client)
            except httpx.HTTPError as exc:
                print(f"[buddy] claude failed: {type(exc).__name__}")
                raise HTTPException(502, "Pip lost his train of thought. Try again.")
            uses = [b for b in reply["content"] if b.get("type") == "tool_use"]
            if not uses:
                return {"reply": claude.text_of(reply).strip(), "actions": actions}
            history.append({"role": "assistant", "content": reply["content"]})
            results = []
            for use in uses:
                ran = await run_tool(use["name"], use.get("input") or {})
                if ran["action"]:
                    actions.append(ran["action"])
                results.append({"type": "tool_result", "tool_use_id": use["id"], "content": ran["result"]})
            history.append({"role": "user", "content": results})
    return {"reply": "Let's take that one step at a time. What's your first line?", "actions": actions}


class Speak(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    voice: Optional[str] = None


@router.post("/api/tts")
async def tts(body: Speak):
    """Deepgram Aura voice for Pip. Returns MP3 audio."""
    key = os.getenv("DEEPGRAM_API_KEY", "")
    if not key:
        raise HTTPException(503, "Voice is off. Set DEEPGRAM_API_KEY.")
    limit("tts")
    voice = body.voice if body.voice and body.voice.startswith("aura-") and len(body.voice) < 40 else TTS_VOICE
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{DEEPGRAM}/speak", params={"model": voice},
                              headers={"Authorization": f"Token {key}"}, json={"text": body.text})
    if r.status_code != 200:
        print(f"[tts] deepgram {r.status_code}")
        raise HTTPException(502, "Pip lost his voice for a second.")
    return Response(r.content, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@router.post("/api/stt")
async def stt(request: Request):
    """Push-to-talk: the page records a short clip, Deepgram writes it down."""
    key = os.getenv("DEEPGRAM_API_KEY", "")
    if not key:
        raise HTTPException(503, "Listening is off. Set DEEPGRAM_API_KEY.")
    limit("stt")
    mime = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if not mime.startswith("audio/"):
        raise HTTPException(415, "Send audio")
    audio = bytearray()
    async for chunk in request.stream():
        audio.extend(chunk)
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "That clip is too long")
    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.post(
            f"{DEEPGRAM}/listen",
            params={"model": "nova-3", "smart_format": "true", "language": "en",
                    "keyterm": ["x", "equals", "plus", "minus", "times"]},
            headers={"Authorization": f"Token {key}", "Content-Type": mime}, content=bytes(audio))
    if r.status_code != 200:
        print(f"[stt] deepgram {r.status_code}")
        raise HTTPException(502, "Pip couldn't hear that.")
    alt = r.json()["results"]["channels"][0]["alternatives"][0]
    return {"text": alt.get("transcript", ""), "confidence": alt.get("confidence")}


@router.get("/api/buddy/config")
async def buddy_config():
    return {
        "vapi_public_key": os.getenv("VAPI_PUBLIC_KEY") or None,
        "vapi_assistant_id": os.getenv("VAPI_ASSISTANT_ID") or None,
        "phone": os.getenv("PIP_PHONE_NUMBER") or None,
        "chat": claude.ready(),
        "voice": bool(os.getenv("DEEPGRAM_API_KEY")),
    }


@router.post("/api/vapi/tools")
async def vapi_tools(request: Request, x_vapi_secret: Optional[str] = Header(None)):
    """Vapi calls this when Pip uses a tool during a live web or phone call."""
    secret = os.getenv("VAPI_SERVER_SECRET", "")
    if not secret:
        raise HTTPException(503, "Tool hook is off. Set VAPI_SERVER_SECRET.")
    if not hmac.compare_digest((x_vapi_secret or "").encode(), secret.encode()):
        raise HTTPException(401, "Wrong secret")
    payload = await request.json()
    message = payload.get("message") or {}
    calls = message.get("toolCallList") or message.get("toolCalls") or []
    results = []
    for call in calls:
        fn = call.get("function") or {}
        name = call.get("name") or fn.get("name") or ""
        args = call.get("arguments") or fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        ran = await run_tool(name, args)
        results.append({"toolCallId": call.get("id"), "result": ran["result"]})
    return {"results": results}


class Peek(BaseModel):
    problem_id: str
    lines: List[str] = Field(default_factory=list, max_length=12)


@router.post("/api/buddy/peek")
async def peek(body: Peek):
    """Shows checked lines on screen during a live call. Read-only: logs nothing."""
    from fastapi.concurrency import run_in_threadpool
    from .main import PROBLEMS, analyze
    if body.problem_id not in PROBLEMS:
        raise HTTPException(404, "Unknown problem")
    result = await run_in_threadpool(analyze, body.problem_id, [line[:80] for line in body.lines])
    return {**result, "problem": problem_text(body.problem_id)}
