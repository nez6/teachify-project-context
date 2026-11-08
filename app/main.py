"""Teachify API. Holds class state in memory, so run a single instance."""
from __future__ import annotations

import asyncio
import csv
import hmac
import io
import json
import os
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai, vision
from .detector import MISCONCEPTIONS, PROBLEMS, analyze, safe_lines_for_model

STATIC = Path(__file__).parent / "static"
CLUSTER_MIN = 3
RESOLVED_GLOW_SECONDS = 4
HARDWARE_ONLINE_SECONDS = 8
DEMO_DEVICE = "desk-07"
MAX_EVENTS = 5000
MAX_CAPTURE_BYTES = 6 * 1024 * 1024
PHOTOS_PER_MINUTE = 30
IMAGE_SIGNATURES = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"))
ROSTER = (
    "Amara Ben Chloe Diego Elif Farah Gus Hana Ivan Jada Kofi Maya "
    "Nico Omar Priya Quinn Rosa Sam Tess Uma Vik Wren Yara Zane"
).split()

app = FastAPI(title="Teachify")
subscribers: List[asyncio.Queue] = []
recent_photos: deque = deque()


def fresh_state() -> dict:
    return {
        "students": {
            str(i + 1): {
                "id": str(i + 1), "name": name, "seat": i + 1, "status": "working",
                "xp": 0, "streak": 0, "simulated": False,
                "last_attempt": {},
            }
            for i, name in enumerate(ROSTER)
        },
        "alerts": {},
        "next_alert": 1,
        "self_corrected": 0,
        "devices": {
            DEMO_DEVICE: {"id": DEMO_DEVICE, "student_id": "12", "led": "idle", "resolved_at": 0.0,
                          "last_seen": 0.0, "last_source": None},
        },
        "feedback": [],
        "events": [],
    }


state = fresh_state()


class Attempt(BaseModel):
    student_id: str
    problem_id: str
    lines: List[str] = Field(default_factory=list, max_length=24)


class Press(BaseModel):
    source: str = "hardware"
    problem_id: Optional[str] = None


class Feedback(BaseModel):
    helpful: bool


def led_for(device: dict) -> str:
    if device["led"] == "resolved" and time.time() - device["resolved_at"] > RESOLVED_GLOW_SECONDS:
        device["led"] = "idle"
    return device["led"]


def clusters() -> List[dict]:
    counts = Counter(
        a["misconception"] for a in state["alerts"].values()
        if a["status"] != "resolved" and a["misconception"]
    )
    return [
        {
            "misconception": key, "label": MISCONCEPTIONS[key]["label"], "count": count,
            "reteach": MISCONCEPTIONS[key]["reteach"],
        }
        for key, count in counts.most_common() if count >= CLUSTER_MIN
    ]


def snapshot() -> dict:
    now = time.time()
    devices = {}
    for key, device in state["devices"].items():
        devices[key] = {
            **device, "led": led_for(device),
            "hardware_online": now - device["last_seen"] < HARDWARE_ONLINE_SECONDS,
        }
    alerts = sorted(
        state["alerts"].values(),
        key=lambda a: (a["status"] == "resolved", not a["help_requested"], a["simulated"], -a["id"]),
    )
    return {
        "students": list(state["students"].values()),
        "alerts": alerts,
        "clusters": clusters(),
        "self_corrected": state["self_corrected"],
        "devices": devices,
        "ai": ai.status(),
    }


def publish() -> None:
    message = json.dumps(snapshot())
    for queue in list(subscribers):
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            pass


def get_student(student_id: str) -> dict:
    student = state["students"].get(student_id)
    if not student:
        raise HTTPException(404, "Unknown student")
    return student


def open_alert_for(student_id: str) -> Optional[dict]:
    for alert in state["alerts"].values():
        if alert["student_id"] == student_id and alert["status"] != "resolved":
            return alert
    return None


def device_for(student_id: str) -> Optional[dict]:
    for device in state["devices"].values():
        if device["student_id"] == student_id:
            return device
    return None


def set_led(student_id: str, value: str) -> None:
    device = device_for(student_id)
    if device:
        device["led"] = value
        if value == "resolved":
            device["resolved_at"] = time.time()


def catalog_brief(misconception: Optional[str]) -> dict:
    if not misconception:
        return {
            "diagnosis": "Asked for help. No invalid line has been detected yet.",
            "say": "Ask them to show you where they got stuck.",
            "practice": None, "provider": "Verified catalog", "latency_ms": None,
        }
    entry = MISCONCEPTIONS[misconception]
    return {
        "diagnosis": entry["diagnosis"], "say": entry["say"], "practice": entry["practice"],
        "provider": "Verified catalog", "latency_ms": None,
    }


def new_alert(student: dict, problem: dict, analysis: Optional[dict], kind: str, source: str) -> dict:
    alert_id = state["next_alert"]
    state["next_alert"] += 1
    misconception = analysis["misconception"] if analysis else None
    alert = {
        "id": alert_id, "student_id": student["id"], "student": student["name"], "seat": student["seat"],
        "kind": kind, "source": source, "help_requested": kind == "help",
        "course": problem["course"], "problem": problem["text"], "prompt": problem["prompt"],
        "rows": analysis["rows"] if analysis else [],
        "misconception": misconception,
        "label": analysis["label"] if analysis else "Asked for help",
        "brief": catalog_brief(misconception),
        "status": "open", "created": time.time(), "simulated": student["simulated"], "feedback": None,
    }
    state["alerts"][alert_id] = alert
    return alert


async def improve_brief(alert: dict) -> None:
    lines, bad_line = safe_lines_for_model(alert["rows"])
    result = await ai.write_brief(alert["course"], alert["problem"], lines, bad_line, alert["label"])
    if result and alert["id"] in state["alerts"]:
        alert["brief"].update(result)
    publish()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def log_event(student: dict, problem: dict, problem_id: str, result: dict, previous: Optional[str]) -> None:
    """One row per checked attempt, for reporting. Uses seat numbers, never names."""
    first_error = result["first_error"]
    state["events"].append({
        "attempt_id": len(state["events"]) + 1,
        "checked_at": iso(time.time()),
        "student_ref": f"seat-{student['seat']:02d}",
        "course": problem["course"],
        "problem_id": problem_id,
        "verdict": result["verdict"],
        "first_error_line": first_error + 1 if first_error is not None else None,
        "misconception": result["misconception"],
        "misconception_label": result["label"],
        "self_corrected": result["verdict"] == "correct" and previous == "error",
        "simulated": student["simulated"],
    })
    del state["events"][:-MAX_EVENTS]


async def handle_attempt(body: Attempt, simulated: bool = False) -> dict:
    student = get_student(body.student_id)
    problem = PROBLEMS.get(body.problem_id)
    if not problem:
        raise HTTPException(404, "Unknown problem")

    result = await run_in_threadpool(analyze, body.problem_id, body.lines)
    student["simulated"] = simulated
    previous = student["last_attempt"].get(body.problem_id)
    student["last_attempt"][body.problem_id] = result["verdict"]
    existing = open_alert_for(student["id"])
    reward = None

    if result["verdict"] == "error":
        student["status"] = "stuck"
        student["streak"] = 0
        if existing and existing["problem"] == problem["text"]:
            help_requested = existing["help_requested"]
            source = existing["source"]
            status = existing["status"]
            del state["alerts"][existing["id"]]
            alert = new_alert(student, problem, result, "error", source)
            alert["help_requested"], alert["status"] = help_requested, status
        else:
            alert = new_alert(student, problem, result, "error", "app")
        asyncio.ensure_future(improve_brief(alert))
    elif result["verdict"] == "correct":
        comeback = previous == "error"
        points = 25 if comeback else 10
        student["xp"] += points
        student["streak"] += 1
        student["status"] = "done"
        reward = {"points": points, "comeback": comeback, "streak": student["streak"], "xp": student["xp"]}
        if comeback:
            state["self_corrected"] += 1
        if existing and existing["problem"] == problem["text"]:
            existing["status"] = "resolved"
            set_led(student["id"], "resolved")
    log_event(student, problem, body.problem_id, result, previous)
    publish()
    return {**result, "reward": reward, "xp": student["xp"], "streak": student["streak"]}


@app.get("/api/problems")
async def problems():
    return list(PROBLEMS.values())


@app.get("/api/state")
async def get_state():
    return snapshot()


@app.get("/api/health")
async def health():
    return {"ok": True, "ai": ai.status()}


@app.post("/api/attempt")
async def attempt(body: Attempt):
    return await handle_attempt(body)


def require_device_token(authorization: Optional[str]) -> None:
    """Browsers use /api/photo instead, so this route stays off until DEVICE_TOKEN is set."""
    token = os.environ.get("DEVICE_TOKEN", "")
    if not token:
        raise HTTPException(503, "Camera upload is off. Set DEVICE_TOKEN to turn it on.")
    auth = authorization or ""
    supplied = auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""
    if not hmac.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(401, "Missing or wrong token", headers={"WWW-Authenticate": "Bearer"})


async def read_capture_body(request: Request) -> bytes:
    """Stops reading at the size limit, since Content-Length can be absent or false."""
    too_large = HTTPException(413, f"Image must be {MAX_CAPTURE_BYTES // (1024 * 1024)} MB or smaller")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_CAPTURE_BYTES:
        raise too_large
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_CAPTURE_BYTES:
            raise too_large
    if not body:
        raise HTTPException(413, "Empty body. Send the raw JPEG or PNG bytes.")
    return bytes(body)


@app.post("/api/capture")
async def capture(
    request: Request, student_id: str, problem_id: str, device_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    require_device_token(authorization)
    # Reject unknown ids here so a bad request never costs a Gemini call.
    get_student(student_id)
    if problem_id not in PROBLEMS:
        raise HTTPException(404, "Unknown problem")

    image = await read_capture_body(request)
    mime = next((m for signature, m in IMAGE_SIGNATURES if image.startswith(signature)), None)
    if not mime:
        raise HTTPException(415, "Body must be a JPEG or PNG image")

    lines = await vision.transcribe(image, mime)
    if lines is None:
        raise HTTPException(503, "Could not read the photo right now. Please use typed input instead.")

    result = await handle_attempt(Attempt(student_id=student_id, problem_id=problem_id, lines=lines))

    device = state["devices"].get(device_id) if device_id else None
    if device:
        device["last_seen"] = time.time()
        if result["verdict"] == "error":
            set_led(student_id, "waiting")
        publish()
    return {**result, "lines": lines}


async def read_jpeg(request: Request) -> bytes:
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "image/jpeg":
        raise HTTPException(415, "Send the photo as image/jpeg")
    image = await read_capture_body(request)
    if not image.startswith(b"\xff\xd8\xff"):
        raise HTTPException(400, "That is not a JPEG")
    return image


async def check_photo(student_id: str, problem_id: str, request: Request, source: str) -> dict:
    """Gemini copies the page, then the algebra engine checks it like typed work."""
    if problem_id not in PROBLEMS:
        raise HTTPException(404, "Unknown problem")
    image = await read_jpeg(request)
    if not vision.ready():
        raise HTTPException(503, "Photo reading is off. Set ANTHROPIC_API_KEY to turn it on.")
    lines = await vision.transcribe(image, "image/jpeg")
    if lines is None:
        raise HTTPException(502, "Could not read the photo")
    device = device_for(student_id)
    if not lines:
        return {"verdict": "unreadable", "first_error": None, "rows": [], "nudge": None, "reward": None,
                "lines": [], "led": led_for(device) if device else "idle"}
    result = await handle_attempt(Attempt(student_id=student_id, problem_id=problem_id, lines=lines))
    alert = open_alert_for(student_id)
    if result["verdict"] == "error" and alert:
        alert["source"] = source
        set_led(student_id, "coming" if alert["status"] == "coming" else "waiting")
    publish()
    return {**result, "lines": lines, "led": led_for(device) if device else "idle"}


@app.post("/api/capture/{device_id}")
async def capture_from_device(
    device_id: str, request: Request, problem_id: str = "dist", x_device_token: Optional[str] = Header(None),
):
    """Desk camera upload: a raw JPEG body plus the shared device token."""
    token = os.environ.get("DEVICE_TOKEN", "")
    if not token:
        raise HTTPException(503, "Camera upload is off. Set DEVICE_TOKEN to turn it on.")
    if not hmac.compare_digest((x_device_token or "").encode(), token.encode()):
        raise HTTPException(401, "Missing or wrong device token")
    device = state["devices"].get(device_id)
    if not device:
        raise HTTPException(404, "Unknown device")
    device["last_seen"] = time.time()
    device["last_source"] = "hardware"
    result = await check_photo(device["student_id"], problem_id, request, "camera")
    return {"verdict": result["verdict"], "first_error": result["first_error"], "led": result["led"]}


def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin", "")
    if not origin or urlsplit(origin).netloc != request.headers.get("host", ""):
        raise HTTPException(403, "Photos can only be sent from this site")


def limit_photo_rate() -> None:
    now = time.time()
    while recent_photos and now - recent_photos[0] > 60:
        recent_photos.popleft()
    if len(recent_photos) >= PHOTOS_PER_MINUTE:
        raise HTTPException(429, "Too many photos. Try again in a minute.")
    recent_photos.append(now)


@app.post("/api/photo/{student_id}")
async def photo_from_browser(student_id: str, request: Request, problem_id: str = "dist"):
    """Student page upload. Uses no device token, so the token never reaches a browser."""
    require_same_origin(request)
    get_student(student_id)
    limit_photo_rate()
    return await check_photo(student_id, problem_id, request, "phone")


@app.post("/api/device/{device_id}/press")
async def press(device_id: str, body: Press):
    device = state["devices"].get(device_id)
    if not device:
        raise HTTPException(404, "Unknown device")
    source = "hardware" if body.source == "hardware" else "virtual"
    device["last_source"] = source
    if source == "hardware":
        device["last_seen"] = time.time()
    student = get_student(device["student_id"])
    student["status"] = "stuck"
    alert = open_alert_for(student["id"])
    if alert:
        alert["help_requested"] = True
        alert["source"] = source
    else:
        problem = PROBLEMS.get(body.problem_id or "") or PROBLEMS["dist"]
        new_alert(student, problem, None, "help", source)
    device["led"] = "waiting"
    publish()
    return {"led": device["led"]}


@app.get("/api/device/{device_id}")
async def device_state(device_id: str, poll: bool = False):
    device = state["devices"].get(device_id)
    if not device:
        raise HTTPException(404, "Unknown device")
    if poll:
        was_online = time.time() - device["last_seen"] < HARDWARE_ONLINE_SECONDS
        device["last_seen"] = time.time()
        if not was_online:
            publish()
    return {"led": led_for(device)}


def get_alert(alert_id: int) -> dict:
    alert = state["alerts"].get(alert_id)
    if not alert:
        raise HTTPException(404, "Unknown alert")
    return alert


@app.post("/api/alert/{alert_id}/ack")
async def acknowledge(alert_id: int):
    alert = get_alert(alert_id)
    alert["status"] = "coming"
    set_led(alert["student_id"], "coming")
    publish()
    return {"ok": True}


@app.post("/api/alert/{alert_id}/resolve")
async def resolve(alert_id: int):
    alert = get_alert(alert_id)
    alert["status"] = "resolved"
    state["students"][alert["student_id"]]["status"] = "working"
    set_led(alert["student_id"], "resolved")
    publish()
    return {"ok": True}


@app.post("/api/alert/{alert_id}/feedback")
async def feedback(alert_id: int, body: Feedback):
    alert = get_alert(alert_id)
    alert["feedback"] = body.helpful
    publish()
    return {"ok": True}


@app.get("/api/export/training.jsonl")
async def export_training():
    """Teacher-rated briefs as chat examples for fine-tuning. Contains no names."""
    rows = []
    for alert in state["alerts"].values():
        if alert["feedback"] is not True or alert["simulated"]:
            continue
        lines, bad_line = safe_lines_for_model(alert["rows"])
        rows.append(json.dumps({
            "messages": [
                {"role": "system", "content": ai.SYSTEM},
                {"role": "user", "content": ai._user_prompt(alert["course"], alert["problem"], lines, bad_line, alert["label"])},
                {"role": "assistant", "content": json.dumps({"diagnosis": alert["brief"]["diagnosis"], "say_this": alert["brief"]["say"]})},
            ]
        }))
    return Response("\n".join(rows), media_type="application/x-ndjson")


def informer_rows(dataset: str) -> List[dict]:
    if dataset == "attempts":
        return list(state["events"])
    if dataset == "alerts":
        return [
            {
                "alert_id": a["id"], "created_at": iso(a["created"]), "student_ref": f"seat-{a['seat']:02d}",
                "course": a["course"], "kind": a["kind"], "source": a["source"],
                "help_requested": a["help_requested"], "misconception": a["misconception"],
                "misconception_label": a["label"], "status": a["status"],
                "teacher_rated_helpful": a["feedback"], "simulated": a["simulated"],
            }
            for a in sorted(state["alerts"].values(), key=lambda a: a["id"])
        ]
    if dataset == "misconceptions":
        counts = Counter(e["misconception"] for e in state["events"] if e["misconception"])
        students = {
            key: len({e["student_ref"] for e in state["events"] if e["misconception"] == key})
            for key in counts
        }
        return [
            {
                "misconception": key, "label": MISCONCEPTIONS[key]["label"], "attempts": count,
                "students": students[key], "reteach": MISCONCEPTIONS[key]["reteach"],
            }
            for key, count in counts.most_common()
        ]
    raise HTTPException(404, "Unknown dataset. Use attempts, alerts or misconceptions.")


@app.get("/api/informer/{dataset}")
async def informer_feed(dataset: str, format: str = "json", authorization: Optional[str] = Header(None)):
    """Read-only reporting feed for Entrinsik Informer. Requires INFORMER_TOKEN as a Bearer token."""
    token = os.environ.get("INFORMER_TOKEN", "")
    if not token:
        raise HTTPException(503, "Reporting feed is off. Set INFORMER_TOKEN to turn it on.")
    auth = authorization or ""
    supplied = auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""
    if not hmac.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(401, "Missing or wrong token", headers={"WWW-Authenticate": "Bearer"})
    rows = informer_rows(dataset)
    if format == "csv":
        out = io.StringIO()
        fields = list(rows[0].keys()) if rows else []
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        return Response(out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="teachify-{dataset}.csv"'})
    return rows


@app.post("/api/demo/simulate")
async def simulate():
    """Runs labeled, synthetic student work through the real detector."""
    script = [
        ("3", "dist", ["3x+2=2x+11"]), ("9", "dist", ["3x+2=2x+11", "x+2=11", "x=9"]),
        ("15", "dist", ["3x+2=2x+11"]), ("20", "dist", ["3x+2=2x+11", "x=9"]),
        ("6", "move", ["5x-2x=11-4", "3x=7"]),
        ("1", "dist", PROBLEMS["dist"]["sample_right"]), ("18", "dist", PROBLEMS["dist"]["sample_right"]),
    ]

    async def run():
        for student_id, problem_id, lines in script:
            await handle_attempt(Attempt(student_id=student_id, problem_id=problem_id, lines=lines), simulated=True)
            await asyncio.sleep(0.5)

    asyncio.ensure_future(run())
    return {"ok": True, "students": len(script)}


@app.post("/api/demo/reset")
async def reset():
    global state
    state = fresh_state()
    publish()
    return {"ok": True}


@app.get("/api/stream")
async def stream():
    queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    subscribers.append(queue)

    async def events():
        try:
            yield f"data: {json.dumps(snapshot())}\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=3)
                    yield f"data: {message}\n\n"
                except asyncio.TimeoutError:
                    # A full snapshot refreshes LED timers and hardware status.
                    yield f"data: {json.dumps(snapshot())}\n\n"
        finally:
            subscribers.remove(queue)

    return StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


from .buddy import router as buddy_router  # noqa: E402  (needs handle_attempt defined above)

app.include_router(buddy_router)


def page(name: str):
    async def handler():
        return FileResponse(STATIC / name, headers={"Cache-Control": "no-cache"})
    return handler


app.get("/", include_in_schema=False)(page("home.html"))
app.get("/classroom", include_in_schema=False)(page("stage.html"))
app.get("/buddy", include_in_schema=False)(page("buddy.html"))
app.get("/lesson", include_in_schema=False)(page("lesson.html"))
app.get("/about", include_in_schema=False)(page("about.html"))
app.get("/student", include_in_schema=False)(page("student.html"))
app.get("/teacher", include_in_schema=False)(page("teacher.html"))
app.get("/node", include_in_schema=False)(page("node.html"))
app.get("/pricing", include_in_schema=False)(page("pricing.html"))
app.mount("/static", StaticFiles(directory=STATIC), name="static")
