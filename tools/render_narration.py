"""Renders the Magic Seesaw narration to MP3 with Deepgram Aura.

Run once after editing app/static/lesson/lines.json:
    DEEPGRAM_API_KEY=... python3 tools/render_narration.py
The MP3s are committed so the lesson plays even with no keys set.
"""
import json
import os
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "lesson"
spec = json.loads((ROOT / "lines.json").read_text())
key = os.environ.get("DEEPGRAM_API_KEY") or sys.exit("Set DEEPGRAM_API_KEY")
out = ROOT / "audio"
out.mkdir(exist_ok=True)
force = "--force" in sys.argv
for name, text in spec["lines"].items():
    target = out / f"{name}.mp3"
    if target.exists() and not force:
        continue
    req = urllib.request.Request(
        f"https://api.deepgram.com/v1/speak?model={spec['voice']}",
        data=json.dumps({"text": text}).encode(),
        headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
    )
    target.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    print("rendered", name)
