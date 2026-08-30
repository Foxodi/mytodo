"""Recurrence / due-date math. Pure — no UI, no I/O."""
from __future__ import annotations

from datetime import datetime, timedelta, date

from mytodo.domain.constants import OVERDUE_FOCUS_COOLDOWN_SEC
from mytodo.domain.utils import safe_int, clamp_day

def resolve_anchor_day(task, fallback_dt=None) -> int:
    """Original day-of-month for monthly/annual roll-forward (1–31)."""
    raw = task.get("anchor_day") if isinstance(task, dict) else None
    try:
        n = int(raw)
        if 1 <= n <= 31:
            return n
    except (TypeError, ValueError):
        pass
    if fallback_dt is not None:
        return fallback_dt.day
    due = task.get("due") if isinstance(task, dict) else None
    if due:
        try:
            return datetime.fromisoformat(due).day
        except Exception:
            pass
    return 1


def add_months(dt: datetime, months: int, anchor_day: int = None) -> datetime:
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = dt.day if anchor_day is None else anchor_day
    return dt.replace(year=year, month=month, day=clamp_day(year, month, day))


def add_years(dt: datetime, years: int, anchor_day: int = None) -> datetime:
    year = dt.year + years
    day = dt.day if anchor_day is None else anchor_day
    return dt.replace(year=year, day=clamp_day(year, dt.month, day))



def should_run_overdue_check(last, now, cooldown_sec: int = OVERDUE_FOCUS_COOLDOWN_SEC) -> bool:
    """True if we should scan overdue tasks (date changed, or cooldown elapsed)."""
    if last is None:
        return True
    if last.date() != now.date():
        return True
    return (now - last).total_seconds() >= cooldown_sec


def next_due_datetime(task, from_dt=None) -> datetime:
    """
    Compute the next due datetime.
    from_dt: optional anchor (used for Flexi mode = complete/skip moment).
    Sticky (default) uses the task's current due.
    Monthly/annual rolls keep the original day-of-month (clamped per month).
    """
    if from_dt is not None:
        current = from_dt
    else:
        current = datetime.fromisoformat(task["due"])
    try:
        due_src = datetime.fromisoformat(task["due"])
        current = current.replace(
            hour=due_src.hour, minute=due_src.minute, second=0, microsecond=0
        )
    except Exception:
        current = current.replace(hour=0, minute=0, second=0, microsecond=0)

    freq = task.get("frequency", "daily")
    interval = safe_int(task.get("interval", 1), default=1)
    if freq in ("daily", "every X days"):
        return current + timedelta(days=interval)
    if freq == "weekly":
        raw = task.get("weekdays") or []
        allowed = set()
        for d in raw:
            try:
                di = int(d)
                if 0 <= di <= 6:
                    allowed.add(di)
            except (TypeError, ValueError):
                pass
        if allowed:
            for i in range(1, 15):
                cand = current + timedelta(days=i)
                if cand.weekday() in allowed:
                    return cand.replace(
                        hour=current.hour, minute=current.minute,
                        second=0, microsecond=0,
                    )
        return current + timedelta(weeks=interval)
    if freq == "monthly":
        return add_months(current, interval, resolve_anchor_day(task, current))
    if freq == "annually":
        return add_years(current, interval, resolve_anchor_day(task, current))
    return current + timedelta(days=1)

