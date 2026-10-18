"""Append-only effort log. Active minutes and API usage are recorded only when actually known; otherwise null."""
import json
from datetime import datetime, timezone
from pathlib import Path

from .paths import TOOL_ROOT

LOG = TOOL_ROOT / "effort" / "effort-log.jsonl"


def log_entry(role, note, active_minutes=None, wall_clock_minutes=None, api_input_tokens=None, api_output_tokens=None,
              api_cost_usd=None, batch=None):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    e = {"logged_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "role": role, "batch": batch, "note": note,
         "active_minutes": active_minutes, "wall_clock_minutes": wall_clock_minutes,
         "api_input_tokens": api_input_tokens, "api_output_tokens": api_output_tokens, "api_cost_usd": api_cost_usd}
    with LOG.open("a") as f:
        f.write(json.dumps(e) + "\n")
    return e


def read_log():
    return [json.loads(l) for l in LOG.read_text().splitlines()] if LOG.exists() else []
