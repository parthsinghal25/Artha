#!/usr/bin/env python3
"""Run the UI test suite in headless Chrome and print results.

Starts the dev server with the isolated test harness enabled on a free port,
loads /tests/index.html in a temporary Chrome profile, prints PASS/FAIL lines
and exits non-zero on any failure. Standard library only.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

UI = Path(__file__).resolve().parent.parent
CHROME_CANDIDATES = [
    os.environ.get("CHROME"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser"),
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    chrome = next((c for c in CHROME_CANDIDATES if c and Path(c).exists()), None)
    if not chrome:
        print("Chrome not found; set CHROME=/path/to/chrome", file=sys.stderr)
        return 2
    subprocess.run([sys.executable, str(UI / "tests" / "make_specimens.py")], check=True, stdout=subprocess.DEVNULL)
    port = free_port()
    report = Path(tempfile.mkdtemp(prefix="ui-report-")) / "report.json"
    env = {**os.environ, "SD_PORT": str(port), "SD_SERVE_TESTS": "1", "SD_TEST_REPORT": str(report)}
    env.pop("SD_API_UPSTREAM", None)
    server = subprocess.Popen([sys.executable, str(UI / "server" / "devserver.py")], env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        profile = tempfile.mkdtemp(prefix="ui-tests-")
        browser = subprocess.Popen([
            # The suite performs hundreds of same-page history updates; without this flag Chrome
            # silently ignores pushState after ~200 calls per 10 s and a later test sees a stale URL.
            chrome, "--headless=new", "--disable-gpu", "--disable-ipc-flooding-protection", "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={profile}", "--window-size=1440,900", f"http://127.0.0.1:{port}/tests/index.html" + (f"?only={urllib.parse.quote(os.environ['TEST_ONLY'])}" if os.environ.get("TEST_ONLY") else ""),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 240
            while time.time() < deadline and not report.exists():
                time.sleep(0.5)
        finally:
            browser.terminate()
            try:
                browser.wait(timeout=10)
            except subprocess.TimeoutExpired:
                browser.kill()
            shutil.rmtree(profile, ignore_errors=True)
        if not report.exists():
            print("Suite did not report within the time budget.")
            return 1
        data = json.loads(report.read_text())
        for r in data["results"]:
            print(("PASS  " if r["ok"] else "FAIL  ") + r["name"] + ("" if r["ok"] else "\n      " + r["error"]))
        print(f"\n{data['total'] - data['failed']}/{data['total']} passed")
        return 1 if data["failed"] else 0
    finally:
        server.terminate()


def all_suites() -> int:
    browser = main()
    print()
    gateway = subprocess.run([sys.executable, str(UI / "tests" / "gateway_test.py")]).returncode
    return 1 if (browser or gateway) else 0


if __name__ == "__main__":
    sys.exit(all_suites() if not os.environ.get("TEST_ONLY") else main())
