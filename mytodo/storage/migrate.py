"""Document migration / normalization. UI-free.

Keeps the on-disk and future API document shape consistent (DATA_VERSION).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from mytodo.domain.constants import DATA_VERSION
from mytodo.domain.utils import clamp_priority, looks_iso, now_iso, parse_display_timestamp


def default_data() -> dict:
    return {
        "version": DATA_VERSION,
        "active": [],
        "history": [],
        "categories": {},
        "category_manager_geometry": "420x520",
        "series_history_geometry": "520x480",
        "completion_rules_geometry": "560x520",
        "completion_rule_edit_geometry": "480x360",
        "mind_map_geometry": "1000x700",
        "mind_map_root_positions": {},
        "mind_map_view": {"zoom": 1.0, "xview": 0.0, "yview": 0.0},
        "window_geometry": "780x820",
        "window_maximized": False,
        "mind_map_maximized": False,
        "completion_rules": [],
        "materials": {"me": "", "others": ""},
        "materials_geometry": "900x560",
        "materials_maximized": False,
        "add_calendar_geometry": "360x220",
        "filters": {
            "header": "All",
            "sub": "All",
            "type": "All",
            "show_due": True,
            "show_not_due": True,
            "show_completed": False,
            "show_skipped": False,
        },
    }


def migrate_data(data):
    """Quietly upgrade older tasks.json shapes."""
    if not isinstance(data, dict):
        return default_data()

    # Very old shape: bare list of tasks
    if isinstance(data, list):
        data = {"active": data, "history": [], "categories": {}}

    data.setdefault("active", [])
    data.setdefault("history", [])
    data.setdefault("categories", {})
    data.setdefault("category_manager_geometry", "420x520")
    data.setdefault("series_history_geometry", "520x480")
    data.setdefault("completion_rules_geometry", "560x520")
    data.setdefault("completion_rule_edit_geometry", "480x360")
    data.setdefault("edit_task_geometry", "440x620")
    data.setdefault("notes_geometry", "480x360")
    data.setdefault("mind_map_geometry", "1000x700")
    data.setdefault("mind_map_view", {"zoom": 1.0, "xview": 0.0, "yview": 0.0})
    data.setdefault("mind_map_root_positions", {})
    data.setdefault("window_geometry", "780x820")
    data.setdefault("completion_rules", [])
    data.setdefault("materials", {"me": "", "others": ""})
    if not isinstance(data.get("materials"), dict):
        data["materials"] = {"me": "", "others": ""}
    else:
        data["materials"].setdefault("me", "")
        data["materials"].setdefault("others", "")
    data.setdefault("materials_geometry", "900x560")
    data.setdefault("materials_maximized", False)
    data.setdefault("add_calendar_geometry", "360x220")
    if not isinstance(data.get("completion_rules"), list):
        data["completion_rules"] = []
    for rule in data["completion_rules"]:
        rule.setdefault("id", str(uuid.uuid4()))
        rule.setdefault("source_ref", "")
        rule.setdefault("every_n", 1)
        rule.setdefault("spawn_text", "")
        rule.setdefault("spawn_category", "")
        rule.setdefault("last_fired_at_count", 0)
    data.setdefault(
        "filters",
        {
            "header": "All",
            "sub": "All",
            "type": "All",
            "show_due": True,
            "show_not_due": True,
            "show_completed": False,
            "show_skipped": False,
        },
    )
    # Old single "category" filter key → header
    if "category" in data.get("filters", {}) and "header" not in data["filters"]:
        data["filters"]["header"] = data["filters"].pop("category", "All")
    data["filters"].setdefault("sub", "All")

    data["categories"] = migrate_categories_tree(data.get("categories"), data)

    # Normalize active tasks
    for task in data["active"]:
        task.setdefault("id", str(uuid.uuid4()))
        task.setdefault("text", "")
        task.setdefault("type", "one-off")
        task.setdefault("category", "")
        task.setdefault("due", None)
        task.setdefault("created", now_iso())
        task.setdefault("prerequisites", [])
        task.setdefault("show_on_calendar", False)
        task.setdefault("notes", "")
        task.setdefault("passive", False)
        task.setdefault("priority", 0)
        try:
            task["priority"] = clamp_priority(task.get("priority", 0))
        except (TypeError, ValueError):
            task["priority"] = 0
        if not isinstance(task.get("prerequisites"), list):
            task["prerequisites"] = []
        if task.get("type") == "recurring":
            task.setdefault("series_id", task.get("id") or str(uuid.uuid4()))
            task.setdefault("frequency", "daily")
            task.setdefault("interval", 1)
            task.setdefault("due_anchor", "sticky")
            if task.get("frequency") == "weekly" and not isinstance(task.get("weekdays"), list):
                try:
                    wd = datetime.fromisoformat(task["due"]).weekday() if task.get("due") else 0
                except Exception:
                    wd = 0
                task["weekdays"] = [wd]
            if task.get("frequency") in ("monthly", "annually"):
                try:
                    n = int(task.get("anchor_day"))
                    if not (1 <= n <= 31):
                        raise ValueError("out of range")
                except (TypeError, ValueError):
                    try:
                        task["anchor_day"] = datetime.fromisoformat(task["due"]).day
                    except Exception:
                        task["anchor_day"] = 1
            else:
                task.pop("anchor_day", None)

    # Rewrite occurrence-id prerequisites → series_id where possible
    id_to_series = {}
    for t in data["active"]:
        if t.get("type") == "recurring" and t.get("series_id"):
            id_to_series[t["id"]] = t["series_id"]
    for h in data["history"]:
        if h.get("series_id") and h.get("id"):
            id_to_series.setdefault(h["id"], h["series_id"])
    for t in data["active"]:
        prereqs = t.get("prerequisites") or []
        rewritten = []
        for p in prereqs:
            rewritten.append(id_to_series.get(p, p))
        # de-dupe while preserving order
        seen = set()
        unique = []
        for p in rewritten:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        t["prerequisites"] = unique

    # Normalize history rows + convert old dd/mm/yy stamps to ISO when possible
    for h in data["history"]:
        h.setdefault("id", str(uuid.uuid4()))
        h.setdefault("text", "")
        h.setdefault("type", "one-off")
        h.setdefault("category", "")
        h.setdefault("status", "completed")
        h.setdefault("original_due", None)
        h.setdefault("series_id", None)
        completed_at = h.get("completed_at")
        if completed_at and not looks_iso(completed_at):
            parsed = parse_display_timestamp(completed_at)
            h["completed_at"] = parsed.isoformat() if parsed else now_iso()
        elif not completed_at:
            h["completed_at"] = now_iso()

    data["version"] = DATA_VERSION
    return data


def migrate_categories_tree(categories, data):
    """
    Normalize categories to:
      { id: { name, parent_id|None, color|None } }
    Old shapes supported:
      - list of names
      - { name: { color } }  (flat coloured headers)
    Also rewrite task/history category name strings → ids.
    """
    if isinstance(categories, list):
        categories = {name: {"color": "#6b7280"} for name in categories}

    if not isinstance(categories, dict):
        return {}

    # Already id-based?
    sample = next(iter(categories.values()), None) if categories else None
    already_tree = (
        isinstance(sample, dict)
        and "name" in sample
        and ("parent_id" in sample or "parent_id" in (sample or {}))
    )
    # Detect old name-keyed format: keys are names, values have color but no name field
    name_keyed = False
    if categories and isinstance(sample, dict) and "name" not in sample:
        name_keyed = True

    name_to_id = {}
    if name_keyed or (categories and not already_tree and all(
        isinstance(v, dict) and "name" not in v for v in categories.values()
    )):
        new_cats = {}
        for name, meta in categories.items():
            if not isinstance(meta, dict):
                meta = {"color": "#6b7280"}
            cid = str(uuid.uuid4())
            new_cats[cid] = {
                "name": str(name),
                "parent_id": None,
                "color": meta.get("color") or "#6b7280",
            }
            name_to_id[str(name)] = cid
        categories = new_cats
        # Rewrite task category names → ids
        for t in data.get("active", []):
            c = t.get("category") or ""
            if c and c in name_to_id:
                t["category"] = name_to_id[c]
        for h in data.get("history", []):
            c = h.get("category") or ""
            if c and c in name_to_id:
                h["category"] = name_to_id[c]
        for rule in data.get("completion_rules", []):
            c = rule.get("spawn_category") or ""
            if c and c in name_to_id:
                rule["spawn_category"] = name_to_id[c]
    else:
        # Ensure required fields on id-based entries
        for cid, meta in list(categories.items()):
            if not isinstance(meta, dict):
                categories[cid] = {"name": str(cid), "parent_id": None, "color": "#6b7280"}
                continue
            meta.setdefault("name", str(cid))
            meta.setdefault("parent_id", None)
            meta.setdefault("sort_order", 0)
            meta.setdefault("collapsed_default", False)
            meta.setdefault("notes", "")
            if meta.get("parent_id") is None and meta.get("color"):
                pass  # coloured root = header
            elif meta.get("parent_id") is None:
                meta.setdefault("color", None)  # uncoloured root allowed
            else:
                meta["color"] = None

    return categories

