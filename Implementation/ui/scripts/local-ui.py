#!/usr/bin/env python3
"""Manage only the local gateway started by this helper; use the backend's accepted current release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "var" / "run"
STATE = RUN / "ui.json"


def read_json(url):
    with urlopen(url, timeout=5) as response:
        return json.load(response)


def alive(state):
    if not state:
        return False
    try:
        os.kill(state["pid"], 0)
        command = subprocess.check_output(["ps", "-p", str(state["pid"]), "-o", "command="], text=True)
        return str(ROOT / "server" / "devserver.py") in command
    except (ProcessLookupError, subprocess.CalledProcessError):
        return False


def stop(state):
    if alive(state):
        os.kill(state["pid"], signal.SIGTERM)
        for _ in range(50):
            if not alive(state):
                break
            time.sleep(.1)
        else:
            raise SystemExit("Gateway did not stop; inspect the recorded PID before retrying.")
        print(f"Stopped gateway pid={state['pid']}")
    else:
        print("Gateway not running")
    STATE.unlink(missing_ok=True)


def start(state):
    if alive(state):
        print(json.dumps(state, indent=2))
        return
    upstream = os.environ.get("SD_API_UPSTREAM", "http://127.0.0.1:8080").rstrip("/")
    port = int(os.environ.get("SD_PORT", "15174"))
    try:
        ready = read_json(upstream + "/readyz")
    except OSError:
        raise SystemExit(f"Backend unavailable at {upstream}; start the canonical backend first.")
    release = ready.get("release") or {}
    if ready.get("unreviewed_sources_enabled") or release.get("status") != "accepted":
        raise SystemExit("Backend must serve an accepted release with unreviewed-source access disabled.")
    requested = os.environ.get("SD_RELEASE_ID", release["release_id"])
    if requested != release["release_id"]:
        raise SystemExit("SD_RELEASE_ID differs from backend current; use its accepted current release.")
    with socket.socket() as probe:
        # Match HTTPServer: closed browser connections may leave this port in TIME_WAIT.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            raise SystemExit(f"Port {port} is occupied; this helper will not stop an unrecorded process.")
    env = dict(os.environ, SD_HOST="127.0.0.1", SD_PORT=str(port), SD_API_UPSTREAM=upstream,
               SD_RELEASE_ID=requested, SD_ASSET_REDEEM_PATH="/v1/assets/{token}",
               SD_SIGNIN_URL=upstream + "/v1/session/signin")
    env.pop("SD_SERVE_TESTS", None)
    env.pop("SD_CONTRACT_ROOT", None)
    RUN.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    for directory in ("src", "public", "server"):
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes())
    with (RUN / "ui.log").open("ab") as log:
        process = subprocess.Popen([sys.executable, str(ROOT / "server" / "devserver.py")],
                                   cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=True)
    state = {"pid": process.pid, "url": f"http://127.0.0.1:{port}", "backend": upstream,
             "release_id": requested, "source_set_version": release["source_set_version"],
             "ui_content_sha256": digest.hexdigest(), "started_at_epoch": time.time()}
    STATE.write_text(json.dumps(state, indent=2) + "\n")
    for _ in range(40):
        if process.poll() is not None:
            STATE.unlink(missing_ok=True)
            raise SystemExit(f"Gateway exited; see {RUN / 'ui.log'}")
        try:
            config = read_json(state["url"] + "/config.json")
            if config.get("release_id") == requested:
                print(json.dumps(state, indent=2))
                return
        except OSError:
            pass
        time.sleep(.25)
    stop(state)
    raise SystemExit("Gateway readiness timed out")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "status", "restart"))
    args = parser.parse_args()
    state = json.loads(STATE.read_text()) if STATE.exists() else None
    if args.action in ("stop", "restart"):
        stop(state)
        state = None
    if args.action in ("start", "restart"):
        start(state)
    elif args.action == "status":
        if not alive(state):
            raise SystemExit("Gateway not running")
        print(json.dumps(state, indent=2))
