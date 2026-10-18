"""Vendor-style call summary (TXT)."""
from datetime import datetime

from .textfmt import MONTHS, clock12
from .tz import melbourne_abbr


def render_call_summary(card, art, authored, plan) -> bytes:
    start = datetime.fromisoformat(art["event_at"])
    gen = datetime.fromisoformat(art["recorded_at"])
    parts = authored["participants"]
    lines = [
        authored["service_banner"],
        "=" * len(authored["service_banner"]),
        f"Case:               {plan.case_id}",
        f"Request reference:  {plan.request_reference}",
        f"Call date:          {start.strftime('%A')} {start.day} {MONTHS[start.month - 1]} {start.year}",
        f"Call start:         {clock12(start)} {melbourne_abbr(art['event_at'])}",
        f"Summary generated:  {gen.day} {MONTHS[gen.month - 1][:3]} {gen.year}, {clock12(gen)} {melbourne_abbr(art['recorded_at'])}",
        "Participants:",
    ] + [f"  - {p}" for p in parts] + ["", "SUMMARY", authored["summary"], "", "FOLLOW-UP ITEMS"] + \
        [f"  - {i}" for i in authored["follow_up_items"]] + [""]
    return "\n".join(lines).encode("utf-8")
