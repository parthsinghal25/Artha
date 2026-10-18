"""Human formatting helpers shared by renderers and checks."""
import re
from datetime import date, datetime

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


def long_date(s: str) -> str:
    d = date.fromisoformat(s[:10])
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def short_date(s: str) -> str:
    d = date.fromisoformat(s[:10])
    return f"{d.day} {MONTHS[d.month - 1][:3]} {d.year}"


def clock12(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"


def date_variants(iso: str):
    """All spellings a reviewer/check should accept for one locked ISO date."""
    d = date.fromisoformat(iso[:10])
    m = MONTHS[d.month - 1]
    return {iso[:10], f"{d.day} {m} {d.year}", f"{d.day} {m[:3]} {d.year}",
            f"{d.day:02d} {m} {d.year}", f"{d.day:02d}/{d.month:02d}/{d.year}",
            f"{d.day}/{d.month}/{d.year}"}


def label(key: str, overrides=None) -> str:
    if overrides and key in overrides:
        return overrides[key]
    return key.replace("_", " ").capitalize()


def display_value(v):
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, str) and ISO_DATE.match(v):
        return long_date(v)
    if isinstance(v, str) and v[:1].islower() and len(v.split()) <= 6 and not re.search(r"[A-Z0-9]{2}", v):
        return v[:1].upper() + v[1:]
    return str(v)


def money(minor: int, cur="AUD") -> str:
    sign = "-" if minor < 0 else ""
    minor = abs(minor)
    return f"{sign}${minor // 100:,}.{minor % 100:02d}"
