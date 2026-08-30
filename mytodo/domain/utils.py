"""Pure helpers — no UI, no I/O."""
from __future__ import annotations

import calendar
from datetime import datetime


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def safe_int(value, default: int = 1) -> int:
    try:
        n = int(value)
        return n if n >= 1 else default
    except (TypeError, ValueError):
        return default


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def clamp_day(year: int, month: int, day: int) -> int:
    return min(max(1, int(day)), days_in_month(year, month))


def clamp_priority(value) -> int:
    try:
        return max(0, min(3, int(value if value is not None else 0)))
    except (TypeError, ValueError):
        return 0


def as_bool(value, default=False) -> bool:
    """Stable bool for JSON/UI values (True/False, 1/0, 'true'/'false')."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("1", "true", "yes", "on", "y"):
            return True
        if s in ("0", "false", "no", "off", "n", ""):
            return False
    return bool(value)


def activity_kind(task) -> str:
    """Exclusive activity class: calendar | passive | active."""
    if not isinstance(task, dict):
        return "active"
    if as_bool(task.get("show_on_calendar")):
        return "calendar"
    if as_bool(task.get("passive")):
        return "passive"
    return "active"


def looks_iso(value: str) -> bool:
    try:
        datetime.fromisoformat(value)
        return True
    except Exception:
        return False


def parse_display_timestamp(value: str):
    for fmt in ("%d/%m/%y %H:%M", "%d/%m/%Y %H:%M", "%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None
