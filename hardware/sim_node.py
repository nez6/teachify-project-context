"""Virtual desk node. Speaks the same HTTP protocol as the ESP32 firmware.

Press Enter to press the button. Ctrl-C to quit.
  python hardware/sim_node.py --server http://localhost:8080
"""
import argparse
import json
import threading
import time
import urllib.request


def call(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://localhost:8080")
    parser.add_argument("--device", default="desk-07")
    parser.add_argument("--press-once", action="store_true", help="Press once, then report the LED and exit")
    args = parser.parse_args()
    base = f"{args.server.rstrip('/')}/api/device/{args.device}"

    if args.press_once:
        print("LED:", call(f"{base}/press", {"source": "hardware"})["led"])
        return

    def poll():
        last = None
        while True:
            try:
                led = call(f"{base}?poll=true")["led"]
                if led != last:
                    print(f"LED -> {led}", flush=True)
                    last = led
            except OSError as error:
                print(f"Poll failed: {error}", flush=True)
            time.sleep(1)

    threading.Thread(target=poll, daemon=True).start()
    print("Virtual node running. Press Enter to press the button.")
    try:
        while True:
            input()
            call(f"{base}/press", {"source": "hardware"})
    except (KeyboardInterrupt, EOFError):
        pass


if __name__ == "__main__":
    main()
