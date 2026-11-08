#!/usr/bin/env python3
"""Stands in for the desk camera: uploads a local JPEG, then follows the status light.

Uses the same protocol as hardware/teachify_camera. Needs only the standard library.

  python3 hardware/sim_camera.py --image page.jpg --server http://localhost:8080
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def request(url, data=None, headers=None, timeout=30):
    """Returns (status, body text). HTTP error statuses are returned, not raised."""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", required=True, help="path to a JPEG")
    parser.add_argument("--server", default="http://localhost:8080")
    parser.add_argument("--device", default="desk-07")
    parser.add_argument("--token", default=os.environ.get("TEACHIFY_DEVICE_TOKEN", ""),
                        help="device token; defaults to $TEACHIFY_DEVICE_TOKEN")
    parser.add_argument("--poll-seconds", type=float, default=30,
                        help="how long to follow the light after uploading; 0 to skip")
    args = parser.parse_args()

    with open(args.image, "rb") as f:
        image = f.read()
    if image[:2] != b"\xff\xd8":
        sys.exit(f"{args.image} is not a JPEG")

    server = args.server.rstrip("/")
    device = urllib.parse.quote(args.device, safe="")
    try:
        status, body = request(f"{server}/api/capture/{device}", data=image, headers={
            "Content-Type": "image/jpeg", "X-Device-Token": args.token,
        })
        print(f"upload: {len(image)} bytes, HTTP {status}\n{body}")
        if not 200 <= status < 300:
            return 1

        led = None
        deadline = time.monotonic() + args.poll_seconds
        while time.monotonic() < deadline:
            status, body = request(f"{server}/api/device/{device}?poll=true", timeout=4)
            if status == 200:
                current = json.loads(body).get("led")
                if current != led:
                    led = current
                    print(f"light: {led}")
            else:
                print(f"poll: HTTP {status}")
            time.sleep(1)
    except (urllib.error.URLError, OSError) as error:
        sys.exit(f"could not reach {server}: {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
