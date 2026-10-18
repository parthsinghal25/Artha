"""Scenario clock handling. Every supplied scenario timestamp carries an offset; the zone name and
abbreviation come from Australia/Melbourne rules for that instant, never a hard-coded AEDT."""
from datetime import datetime
from zoneinfo import ZoneInfo

MELBOURNE = ZoneInfo("Australia/Melbourne")


class OffsetMismatch(ValueError):
    pass


def melbourne_abbr(iso: str) -> str:
    """AEDT/AEST for the instant, verifying the string's own offset is what Melbourne actually had."""
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        raise OffsetMismatch(f"scenario timestamp without offset: {iso}")
    local = dt.astimezone(MELBOURNE)
    if local.utcoffset() != dt.utcoffset():
        raise OffsetMismatch(f"{iso} carries offset {dt.utcoffset()} but Melbourne was {local.utcoffset()} then")
    return local.tzname()
