"""Document mutations used by the server (and future clients). UI-free."""
from __future__ import annotations

import uuid
from datetime import datetime

from mytodo.domain.recurrence import next_due_datetime, resolve_anchor_day
from mytodo.domain.utils import as_bool, clamp_priority, now_iso


def find_active(data: dict, task_id: str):
    return next((t for t in data.get("active") or [] if t.get("id") == task_id), None)


def next_occurrence(task: dict, next_due: datetime, series_id: str) -> dict:
    priority = clamp_priority(task.get("priority", 0))
    nxt = {
        "id": str(uuid.uuid4()),
        "series_id": series_id or task.get("id"),
        "text": task.get("text") or "",
        "type": "recurring",
        "category": task.get("category") or "",
        "due": next_due.isoformat(),
        "frequency": task.get("frequency") or "daily",
        "interval": task.get("interval", 1),
        "weekdays": list(task.get("weekdays") or []),
        "due_anchor": task.get("due_anchor") or "sticky",
        "created": now_iso(),
        "prerequisites": list(task.get("prerequisites") or []),
        "show_on_calendar": as_bool(task.get("show_on_calendar")),
        "priority": priority,
        "notes": task.get("notes") or "",
        "passive": as_bool(task.get("passive")),
    }
    if task.get("frequency") in ("monthly", "annually"):
        nxt["anchor_day"] = resolve_anchor_day(task)
    so = task.get("star_order", task.get("p3_order"))
    if priority >= 1 and so is not None:
        nxt["star_order"] = so
    return nxt


def complete_or_skip(data: dict, task_id: str, status: str) -> bool:
    """Move an active task to history; roll recurring forward. Returns True if changed."""
    if status not in ("completed", "skipped"):
        raise ValueError("status must be completed or skipped")
    task = find_active(data, task_id)
    if not task:
        return False
    series_id = task.get("series_id") or (task["id"] if task.get("type") == "recurring" else None)
    data.setdefault("history", []).append(
        {
            "id": task["id"],
            "series_id": series_id,
            "text": task.get("text") or "",
            "type": task.get("type") or "one-off",
            "category": task.get("category") or "",
            "status": status,
            "original_due": task.get("due"),
            "completed_at": now_iso(),
            "frequency": task.get("frequency"),
            "interval": task.get("interval"),
            "passive": as_bool(task.get("passive")),
            "show_on_calendar": as_bool(task.get("show_on_calendar")),
            "priority": clamp_priority(task.get("priority", 0)),
        }
    )
    data["active"] = [t for t in data.get("active") or [] if t.get("id") != task_id]
    if task.get("type") == "recurring":
        anchor = (task.get("due_anchor") or "sticky").lower()
        from_dt = datetime.now() if anchor == "flexi" else None
        nxt_due = next_due_datetime(task, from_dt=from_dt)
        data["active"].append(next_occurrence(task, nxt_due, series_id or task["id"]))
    return True


def add_task(data: dict, fields: dict) -> dict:
    text = (fields.get("text") or "").strip()
    if not text:
        raise ValueError("Task name is required")
    show_cal = bool(fields.get("show_on_calendar"))
    passive = bool(fields.get("passive")) and not show_cal
    due = fields.get("due") or None
    if due == "":
        due = None
    task = {
        "id": str(uuid.uuid4()),
        "text": text,
        "type": "one-off" if fields.get("type") != "recurring" else "recurring",
        "category": fields.get("category") or "",
        "due": due,
        "created": now_iso(),
        "prerequisites": list(fields.get("prerequisites") or []),
        "show_on_calendar": show_cal and bool(due),
        "priority": clamp_priority(fields.get("priority", 0)),
        "passive": passive,
        "notes": fields.get("notes") or "",
    }
    if task["type"] == "recurring":
        task["frequency"] = fields.get("frequency") or "daily"
        task["interval"] = fields.get("interval") or 1
        task["series_id"] = task["id"]
        if not due:
            raise ValueError("Recurring tasks need a due date")
    data.setdefault("active", []).append(task)
    return task


def delete_task(data: dict, task_id: str) -> bool:
    before = len(data.get("active") or [])
    data["active"] = [t for t in data.get("active") or [] if t.get("id") != task_id]
    return len(data["active"]) != before
