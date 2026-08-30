"""Domain layer — pure business logic, no UI toolkit, no storage I/O.

This package is the contract that a future mobile app or server can share
(or reimplement against the same document schema / DATA_VERSION).
"""
from mytodo.domain.constants import DATA_VERSION, OVERDUE_FOCUS_COOLDOWN_SEC, SAVE_DEBOUNCE_MS
from mytodo.domain.utils import (
    now_iso, safe_int, days_in_month, clamp_day, clamp_priority, as_bool, activity_kind,
    looks_iso, parse_display_timestamp,
)
from mytodo.domain.recurrence import (
    resolve_anchor_day, add_months, add_years, should_run_overdue_check, next_due_datetime,
)
from mytodo.domain.tasks import make_spawned_task
from mytodo.domain.actions import complete_or_skip, add_task, delete_task

__all__ = [
    "DATA_VERSION", "OVERDUE_FOCUS_COOLDOWN_SEC", "SAVE_DEBOUNCE_MS",
    "now_iso", "safe_int", "days_in_month", "clamp_day", "clamp_priority", "as_bool", "activity_kind",
    "looks_iso", "parse_display_timestamp",
    "resolve_anchor_day", "add_months", "add_years", "should_run_overdue_check", "next_due_datetime",
    "make_spawned_task",
]
