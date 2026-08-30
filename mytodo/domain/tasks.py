"""Task factory helpers. Pure dict shapes — shared by desktop / future API."""
from __future__ import annotations

import uuid
from datetime import datetime

from mytodo.domain.utils import as_bool, clamp_priority, now_iso

def make_spawned_task(text, category="", source=None, rule_id=None, spawned_from_count=None) -> dict:
    """One-off spawned by a completion rule — always has the full active-task field set."""
    src = source or {}
    try:
        priority = clamp_priority(src.get("priority", 0))
    except (TypeError, ValueError):
        priority = 0
    return {
        "id": str(uuid.uuid4()),
        "text": text,
        "type": "one-off",
        "category": category or "",
        "due": datetime.now().replace(second=0, microsecond=0).isoformat(),
        "created": now_iso(),
        "prerequisites": [],
        "show_on_calendar": as_bool(src.get("show_on_calendar")),
        "notes": "",
        "passive": as_bool(src.get("passive")),
        "priority": priority,
        "spawned_by_rule": rule_id,
        "spawned_from_count": spawned_from_count,
    }
