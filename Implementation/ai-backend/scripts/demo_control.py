#!/usr/bin/env python3
"""Exact-ownership control of the DEMO store's API server and background intel worker.

  scripts/demo-control.sh server start|stop|restart|status
  scripts/demo-control.sh worker start --as-of 2026-06-30 [update args...] | worker stop | worker status

Only processes this script started (recorded PID + identical ps command/start time) are ever signalled; a stale or
reused PID is reported, never killed. The legacy backend (scripts/local-backend.sh, port 8080) is not touched."""
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
NAME = os.environ.get("SDB_DEMO_NAME", "p4-pilot-v1")
RUN = ROOT / "var" / "demo" / NAME / "run"
DEMO = ROOT / "scripts" / "demo.sh"
PY = str(ROOT / ".venv-intel" / "bin" / "python")


def identity(pid):
    try:
        return {"command": subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True).strip(),
                "started": subprocess.check_output(["ps", "-p", str(pid), "-o", "lstart="], text=True).strip()}
    except (OSError, subprocess.CalledProcessError):
        return None


def load(kind):
    try:
        return json.loads((RUN / f"{kind}.json").read_text())
    except (OSError, ValueError):
        return None


def owned(state, marker):
    if not state or not isinstance(state.get("pid"), int):
        return False
    actual = identity(state["pid"])
    if not actual or actual["started"] != state["identity"]["started"]:
        return False
    return PY in actual["command"] and marker in actual["command"]


def _refresh_identity(state, marker):
    """demo.sh execs into python (same PID); record the post-exec identity once it is visible."""
    for _ in range(40):
        actual = identity(state["pid"])
        if actual and PY in actual["command"] and marker in actual["command"]:
            state["identity"] = actual
            return state
        time.sleep(0.1)
    return state


def ready(state):
    if not owned(state, " -m sdbackend serve --port "):
        return False
    try:
        with urlopen(state["url"] + "/readyz", timeout=2) as r:
            body = json.load(r)
        return body.get("status") == "ready" and not body.get("unreviewed_sources_enabled")
    except (OSError, ValueError):
        return False


def stop(kind, marker):
    state = load(kind)
    if owned(state, marker):
        os.kill(state["pid"], signal.SIGTERM)
        for _ in range(300):
            if not owned(state, marker):
                break
            time.sleep(0.1)
        else:
            raise SystemExit(f"recorded {kind} pid={state['pid']} did not stop; inspect it before retrying")
        print(f"stopped {kind} pid={state['pid']}")
    else:
        print(f"{kind} not running; no foreign or reused PID was signalled")
    (RUN / f"{kind}.json").unlink(missing_ok=True)


def spawn(kind, args, marker):
    RUN.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if k not in ("SDB_ALLOW_UNREVIEWED_SOURCES", "SDB_ALLOW_SPECIMEN_ACCEPTANCE", "SDB_REVIEWER_REGISTRY")}
    log = (RUN / f"{kind}.log").open("ab")
    p = subprocess.Popen([str(DEMO), *args], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True)
    state = _refresh_identity({"pid": p.pid, "args": args, "identity": identity(p.pid) or {}}, marker)
    return p, state


def server(action):
    marker = " -m sdbackend serve --port "
    if action in ("stop", "restart"):
        stop("server", marker)
    if action == "status":
        st = load("server")
        if not ready(st):
            raise SystemExit("demo server not running or unhealthy; a stale PID is not ownership")
        print(json.dumps(st, indent=2))
        return
    if action not in ("start", "restart"):
        return
    st = load("server")
    if owned(st, marker):
        print(json.dumps(st, indent=2))
        return
    port = int(os.environ.get("SDB_PORT", "8091"))
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            raise SystemExit(f"port {port} occupied; no unrecorded process will be stopped")
    p, st = spawn("server", ["serve", "--port", str(port)], marker)
    st["url"] = f"http://127.0.0.1:{port}"
    (RUN / "server.json").write_text(json.dumps(st, indent=2) + "\n")
    for _ in range(80):
        if p.poll() is not None:
            raise SystemExit(f"demo server exited; see {RUN / 'server.log'}")
        if ready(st):
            print(json.dumps(st, indent=2))
            return
        time.sleep(0.25)
    stop("server", marker)
    raise SystemExit("demo server readiness timed out")


def worker(action, rest):
    marker = " -m sdbackend intel update "
    if action == "stop":
        stop("worker", marker)        # SIGTERM: the fenced task is re-queued on lease expiry; rerun to resume
        return
    st = load("worker")
    if action == "status":
        alive = owned(st, marker)
        print(json.dumps({"running": alive, **(st or {})}, indent=2))
        return
    if owned(st, marker):
        raise SystemExit(f"a demo worker is already running (pid={st['pid']}); stop it or wait")
    p, st = spawn("worker", ["intel", "update", *rest], marker)
    (RUN / "worker.json").write_text(json.dumps(st, indent=2) + "\n")
    print(json.dumps(st, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("server", "worker"):
        raise SystemExit(__doc__)
    (server if sys.argv[1] == "server" else lambda a: worker(a, sys.argv[3:]))(sys.argv[2])
