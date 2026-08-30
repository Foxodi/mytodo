"""Mind map window (canvas graph, zoom/pan, root positions)."""
from __future__ import annotations

import customtkinter as ctk
from tkinter import messagebox, Menu, colorchooser, Canvas, ALL, font as tkfont
import tkinter as tk
import json
import os
import uuid
from datetime import datetime, timedelta, date, time as dtime

from mytodo.domain import (
    DATA_VERSION,
    now_iso,
    safe_int,
    clamp_priority,
    as_bool,
    next_due_datetime,
    make_spawned_task,
)

class MindMapMixin:
    def open_mind_map(self):
        """2D mind map of categories, active tasks, prerequisites, and completion rules."""
        # Close any previous mind-map window so we don't stack orphans
        prev = getattr(self, "_mind_map_win", None)
        if prev is not None:
            try:
                if prev.winfo_exists():
                    prev.destroy()
            except Exception:
                pass
            self._mind_map_win = None

        win = ctk.CTkToplevel(self)
        self._mind_map_win = win
        win.title("Mind Map")
        try:
            win.configure(fg_color="#1a1b1e")
        except Exception:
            pass
        geo = (self.data.get("mind_map_geometry") or "1000x700").strip() or "1000x700"
        want_max = bool(self.data.get("mind_map_maximized"))
        try:
            win.geometry(geo)
        except Exception:
            win.geometry("1000x700")
            geo = "1000x700"
        win.minsize(640, 480)
        win.transient(self)
        win._mm_last_normal_geometry = geo
        win._mm_geo_lock = True
        # Start fully transparent — avoids white flash without withdraw() (which was
        # making the window vanish again on some Windows/CTk setups).
        try:
            win.attributes("-alpha", 0.0)
        except Exception:
            pass

        def _mm_is_zoomed():
            try:
                if str(win.state()) == "zoomed":
                    return True
            except Exception:
                pass
            try:
                if bool(win.attributes("-zoomed")):
                    return True
            except Exception:
                pass
            return False

        def _mm_set_zoomed(flag):
            try:
                win.state("zoomed" if flag else "normal")
                return
            except Exception:
                pass
            try:
                win.attributes("-zoomed", bool(flag))
            except Exception:
                pass

        def _mm_track_normal(_event=None):
            # Debounced — Configure fires very often during open/maximize; never call
            # update_idletasks here (that was the launch lag).
            if getattr(win, "_mm_geo_lock", False):
                return
            if _mm_is_zoomed():
                return
            aid = getattr(win, "_mm_track_after", None)
            if aid is not None:
                try:
                    win.after_cancel(aid)
                except Exception:
                    pass

            def _commit():
                win._mm_track_after = None
                if getattr(win, "_mm_geo_lock", False) or _mm_is_zoomed():
                    return
                try:
                    g = win.geometry()
                    if g and "x" in g:
                        win._mm_last_normal_geometry = g
                except Exception:
                    pass

            win._mm_track_after = win.after(250, _commit)

        def _mm_reapply():
            g = (self.data.get("mind_map_geometry") or geo).strip() or geo
            try:
                win.geometry(g)
            except Exception:
                pass
            if want_max:
                try:
                    _mm_set_zoomed(True)
                except Exception:
                    pass
            win._mm_geo_lock = False

        # Geometry re-apply is scheduled after deiconify (see _mm_show)
        win.bind("<Configure>", _mm_track_normal, add="+")

        def persist_and_close():
            try:
                win.update_idletasks()
                zoomed = _mm_is_zoomed()
                self.data["mind_map_maximized"] = bool(zoomed)
                if zoomed:
                    g = getattr(win, "_mm_last_normal_geometry", None) or self.data.get("mind_map_geometry")
                    if g:
                        self.data["mind_map_geometry"] = g
                else:
                    self.data["mind_map_geometry"] = win.geometry()
                # Zoom + scroll (set by _mm_capture_view during session)
                view = getattr(win, "_mm_view_snapshot", None)
                if isinstance(view, dict):
                    self.data["mind_map_view"] = view
                self.save_data()
            except Exception:
                pass
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", persist_and_close)

        toolbar = ctk.CTkFrame(win, fg_color="transparent")
        toolbar.pack(fill="x", padx=10, pady=(8, 4))
        ctk.CTkLabel(
            toolbar,
            text="Drag nodes to reparent · drag Create* onto map to add · empty space pans · scroll zooms",
            text_color="gray",
        ).pack(side="left", padx=4)
        ctk.CTkButton(toolbar, text="Reset layout", width=110, command=lambda: rebuild(True)).pack(
            side="right", padx=4
        )
        ctk.CTkButton(toolbar, text="Expand all", width=100, command=lambda: set_all_collapsed(False)).pack(
            side="right", padx=4
        )
        ctk.CTkButton(toolbar, text="Collapse all", width=100, command=lambda: set_all_collapsed(True)).pack(
            side="right", padx=4
        )
        create_task_btn = ctk.CTkButton(toolbar, text="Create Task", width=110)
        create_task_btn.pack(side="right", padx=4)
        create_cat_btn = ctk.CTkButton(toolbar, text="Create Category", width=130)
        create_cat_btn.pack(side="right", padx=4)
        self.enable_double_click_maximize(win, toolbar)

        # Canvas — no scrollbars; pan + zoom instead
        canvas_host = ctk.CTkFrame(win, fg_color="#1a1b1e")
        canvas_host.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        canvas = Canvas(
            canvas_host,
            bg="#1a1b1e",
            highlightthickness=0,
        )
        canvas.pack(fill="both", expand=True)

        _saved_view = self.data.get("mind_map_view") or {}
        try:
            _init_zoom = float(_saved_view.get("zoom", 1.0) or 1.0)
        except (TypeError, ValueError):
            _init_zoom = 1.0
        _init_zoom = max(0.35, min(2.5, _init_zoom))
        try:
            _init_xview = float(_saved_view.get("xview", 0.0) or 0.0)
        except (TypeError, ValueError):
            _init_xview = 0.0
        try:
            _init_yview = float(_saved_view.get("yview", 0.0) or 0.0)
        except (TypeError, ValueError):
            _init_yview = 0.0
        _init_xview = max(0.0, min(1.0, _init_xview))
        _init_yview = max(0.0, min(1.0, _init_yview))
        # Only restore if user has actually panned/zoomed in a prior session
        _have_saved_view = bool(_saved_view.get("user"))

        state = {
            "nodes": {},       # nid -> node dict
            "collapsed": set(),  # category nids that are collapsed
            "drag": None,      # {mode: 'node'|'pan', ...}
            "tooltip": None,
            "tooltip_nid": None,
            "tooltip_after": None,
            "pending_tip_nid": None,
            "zoom": _init_zoom,
            "font": tkfont.Font(
                family="Segoe UI",
                size=max(6, int(round(10 * _init_zoom))),
            ),
            "font_small": tkfont.Font(
                family="Segoe UI",
                size=max(5, int(round(9 * _init_zoom))),
            ),
            "base_font_size": 10,
            "base_font_small": 9,
        }

        NODE_W = {
            "header": 160,
            "category": 150,
            "task": 170,
            "rule": 160,
            "group": 140,
        }
        NODE_H = 36
        H_GAP = 48
        V_GAP = 14

        COLORS = {
            "header_text": "#ffffff",
            "category_fill": "#374151",
            "category_text": "#f3f4f6",
            "task_fill": "#1e3a5f",
            "task_text": "#e0f2fe",
            "task_blocked": "#78350f",
            "rule_fill": "#4c1d95",
            "rule_text": "#ede9fe",
            "group_fill": "#27272a",
            "group_text": "#a1a1aa",
            "line": "#4b5563",
            "outline": "#6b7280",
        }

        def measure(text, font):
            return font.measure(text)

        def node_size(kind, label, extra=""):
            font = state["font_small"] if kind == "rule" else state["font"]
            w = max(NODE_W.get(kind, 150), measure(label, font) + 28)
            if extra:
                w = max(w, measure(extra, state["font_small"]) + 28)
            h = NODE_H + (12 if extra else 0)
            return w, h

        def build_graph():
            """
            Build a forest of nodes:
              categories (tree) → tasks in that category
              tasks with prereqs are ALSO linked under the prereq task (layout parent prefers prereq)
              completion rules hang under their source task
            """
            nodes = {}
            children = {}  # nid -> [child_nids]

            def add_node(nid, kind, label, meta=None, fill=None):
                nodes[nid] = {
                    "id": nid,
                    "kind": kind,
                    "label": label,
                    "meta": meta or {},
                    "fill": fill,
                    "x": 0,
                    "y": 0,
                    "w": 0,
                    "h": 0,
                    "parent": None,
                }
                children.setdefault(nid, [])
                return nid

            # --- Categories ---
            cat_nid = {}  # category id -> node id

            def walk_cat(cid, parent_nid=None):
                meta = self.get_category(cid) or {}
                is_header = self.is_header_category(cid)
                kind = "header" if is_header else "category"
                fill = meta.get("color") if is_header else COLORS["category_fill"]
                nid = f"cat:{cid}"
                label = meta.get("name") or "?"
                note_tip = (meta.get("notes") or "").strip() or None
                if note_tip:
                    label = "📝 " + label
                if meta.get("collapsed_default"):
                    label = "▸ " + label
                add_node(nid, kind, label, meta={"cat_id": cid, "note_tip": note_tip}, fill=fill)
                cat_nid[cid] = nid
                if parent_nid:
                    children[parent_nid].append(nid)
                    nodes[nid]["parent"] = parent_nid
                for kid in self.category_children(cid):
                    walk_cat(kid, nid)
                return nid

            roots = []
            for cid in self.category_children(None):
                roots.append(walk_cat(cid, None))

            # Uncategorised bucket for tasks with no category
            uncat = add_node("group:uncategorised", "group", "Uncategorised")
            has_uncat = False

            # --- Active tasks ---
            task_nid = {}  # task id / series ref -> node id
            for t in self.data.get("active", []):
                tid = t["id"]
                name = t.get("text") or "(task)"
                stars, icons = self.task_prefix_icons(t)
                prefix = f"{stars}{icons}".strip()
                due_tip = None
                if t.get("due") and self.is_future(t):
                    prefix = f"{prefix} 🕒".strip() if prefix else "🕒"
                    due_tip = self.format_dt(t.get("due")) or t.get("due")
                label = f"{prefix} {name}" if prefix else name
                note_tip = (t.get("notes") or "").strip() or None
                pr = clamp_priority(t.get("priority", 0))
                blocked = self.is_blocked(t)
                fill = COLORS["task_blocked"] if blocked else COLORS["task_fill"]
                nid = f"task:{tid}"
                add_node(
                    nid,
                    "task",
                    label,
                    meta={
                        "task_id": tid,
                        "series_id": t.get("series_id"),
                        "prereqs": list(t.get("prerequisites") or []),
                        "due_tip": due_tip,
                        "note_tip": note_tip,
                        "priority": pr,
                        "status": self.task_status(t),
                    },
                    fill=fill,
                )
                task_nid[tid] = nid
                if t.get("series_id"):
                    task_nid[t["series_id"]] = nid

            # Attach tasks: prefer first unsatisfied/any prereq as parent; else category; else uncategorised
            for t in self.data.get("active", []):
                nid = task_nid[t["id"]]
                parent = None
                for pref in t.get("prerequisites") or []:
                    # resolve to an active task node
                    if pref in task_nid:
                        parent = task_nid[pref]
                        break
                    # series match
                    for ot in self.data.get("active", []):
                        if ot.get("series_id") == pref or ot.get("id") == pref:
                            parent = task_nid.get(ot["id"])
                            break
                    if parent:
                        break
                if parent is None:
                    cat = t.get("category") or ""
                    if cat and cat in cat_nid:
                        parent = cat_nid[cat]
                    else:
                        parent = uncat
                        has_uncat = True
                children[parent].append(nid)
                nodes[nid]["parent"] = parent

            if has_uncat:
                roots.append(uncat)
            else:
                nodes.pop(uncat, None)
                children.pop(uncat, None)

            # --- Completion rules under source task ---
            for rule in self.data.get("completion_rules") or []:
                ref = rule.get("source_ref") or ""
                parent = task_nid.get(ref)
                if parent is None:
                    # try series / active match
                    for t in self.data.get("active", []):
                        if t.get("id") == ref or t.get("series_id") == ref:
                            parent = task_nid.get(t["id"])
                            break
                if parent is None:
                    # hang under uncategorised / skip if no source visible
                    if "group:uncategorised" in nodes:
                        parent = "group:uncategorised"
                        has_uncat = True
                    else:
                        continue
                progress, every_n, _total, _last = self.rule_progress(rule)
                spawn = rule.get("spawn_text") or "?"
                label = f"Rule: every {every_n}× → {spawn}"
                extra = f"{progress}/{every_n}"
                rid = f"rule:{rule.get('id')}"
                add_node(
                    rid,
                    "rule",
                    label,
                    meta={"rule_id": rule.get("id"), "extra": extra},
                    fill=COLORS["rule_fill"],
                )
                children[parent].append(rid)
                nodes[rid]["parent"] = parent

            # Store adjacency
            for nid, node in nodes.items():
                node["children"] = children.get(nid, [])

            state["nodes"] = nodes
            return roots

        def visible_children(nid):
            node = state["nodes"].get(nid)
            if not node:
                return []
            if nid in state["collapsed"] and node["kind"] in ("header", "category", "group"):
                return []
            return list(node.get("children") or [])

        def subtree_height(nid):
            kids = visible_children(nid)
            node = state["nodes"][nid]
            extra = (node.get("meta") or {}).get("extra", "")
            _, h = node_size(node["kind"], node["label"], extra)
            if not kids:
                return h
            return max(h, sum(subtree_height(k) for k in kids) + V_GAP * (len(kids) - 1))

        def layout(nid, x, y_center):
            node = state["nodes"][nid]
            extra = (node.get("meta") or {}).get("extra", "")
            w, h = node_size(node["kind"], node["label"], extra)
            node["w"], node["h"] = w, h
            node["x"] = x
            node["y"] = y_center - h / 2

            kids = visible_children(nid)
            if not kids:
                return
            total_h = sum(subtree_height(k) for k in kids) + V_GAP * (len(kids) - 1)
            cy = y_center - total_h / 2
            for k in kids:
                kh = subtree_height(k)
                layout(k, x + w + H_GAP, cy + kh / 2)
                cy += kh + V_GAP

        def root_position_key(nid):
            """Stable key for persisted default positions of root columns."""
            node = state["nodes"].get(nid) or {}
            kind = node.get("kind")
            if kind == "header":
                cid = (node.get("meta") or {}).get("cat_id")
                return f"cat:{cid}" if cid else None
            if nid == "group:uncategorised" or kind == "group":
                return "__uncategorised__"
            # Root non-header categories (rare) can also pin
            if kind == "category" and node.get("parent") is None:
                cid = (node.get("meta") or {}).get("cat_id")
                return f"cat:{cid}" if cid else None
            return None

        def shift_subtree(nid, dx, dy):
            stack = [nid]
            while stack:
                cur = stack.pop()
                n = state["nodes"].get(cur)
                if not n:
                    continue
                n["x"] = n.get("x", 0) + dx
                n["y"] = n.get("y", 0) + dy
                if cur not in state["collapsed"]:
                    stack.extend(list(n.get("children") or []))

        def place_root_at(nid, x, y):
            """Layout subtree, then move so root top-left sits at (x, y)."""
            rh = subtree_height(nid)
            layout(nid, 0, rh / 2)
            node = state["nodes"][nid]
            shift_subtree(nid, x - node.get("x", 0), y - node.get("y", 0))

        def auto_layout(roots):
            # Reset geometry so collapsed branches are not drawn
            for node in state["nodes"].values():
                node["x"] = node["y"] = 0
                node["w"] = node["h"] = 0
            if not roots:
                return
            positions = self.data.get("mind_map_root_positions") or {}
            pinned = []
            free = []
            for r in roots:
                key = root_position_key(r)
                pos = positions.get(key) if key else None
                if isinstance(pos, dict) and "x" in pos and "y" in pos:
                    pinned.append((r, float(pos["x"]), float(pos["y"])))
                else:
                    free.append(r)
            for r, x, y in pinned:
                place_root_at(r, x, y)
            if free:
                # Stack unpinned roots down the left, starting below any pinned block if needed
                cursor = 40.0
                if pinned:
                    # place free column to the right of leftmost pinned if many pinned at x~40
                    pass
                x = 40.0
                for r in free:
                    rh = subtree_height(r)
                    layout(r, x, cursor + rh / 2)
                    cursor += rh + 40

        def descendants(nid):
            out = []
            stack = list(state["nodes"].get(nid, {}).get("children") or [])
            while stack:
                c = stack.pop()
                out.append(c)
                if c not in state["collapsed"]:
                    stack.extend(state["nodes"].get(c, {}).get("children") or [])
            return out

        def draw():
            canvas.delete(ALL)
            nodes = state["nodes"]
            z = state.get("zoom") or 1.0

            def zx(v):
                return v * z

            def zy(v):
                return v * z

            # edges first
            for nid, node in nodes.items():
                if node.get("parent") and node["parent"] in nodes:
                    parent = nodes[node["parent"]]
                    if node["w"] == 0 or parent["w"] == 0:
                        continue
                    x1 = zx(parent["x"] + parent["w"])
                    y1 = zy(parent["y"] + parent["h"] / 2)
                    x2 = zx(node["x"])
                    y2 = zy(node["y"] + node["h"] / 2)
                    mx = (x1 + x2) / 2
                    canvas.create_line(
                        x1, y1, mx, y1, mx, y2, x2, y2,
                        fill=COLORS["line"], width=max(1, int(round(2 * z))),
                        smooth=True,
                    )

            # nodes
            for nid, node in nodes.items():
                if node["w"] == 0:
                    continue
                x, y = zx(node["x"]), zy(node["y"])
                w, h = zx(node["w"]), zy(node["h"])
                fill = node.get("fill") or COLORS.get(f"{node['kind']}_fill", "#333")
                text_color = COLORS.get(f"{node['kind']}_text", "#fff")
                if node["kind"] == "header":
                    text_color = COLORS["header_text"]
                elif node["kind"] == "task" and fill == COLORS["task_blocked"]:
                    text_color = "#fef3c7"
                elif (
                    node["kind"] == "task"
                    and int((node.get("meta") or {}).get("priority") or 0) >= 3
                    and (node.get("meta") or {}).get("status") == "due"
                ):
                    text_color = "#ef4444"  # ★★★ + Due only

                label = node["label"]
                if node["kind"] in ("header", "category", "group"):
                    if nid in state["collapsed"]:
                        label = "▸ " + label
                    elif node.get("children"):
                        label = "▾ " + label

                canvas.create_rectangle(
                    x, y, x + w, y + h,
                    fill=fill, outline=COLORS["outline"], width=1,
                    tags=("node", nid),
                )
                canvas.create_text(
                    x + w / 2, y + h / 2,
                    text=label, fill=text_color,
                    font=state["font_small"] if node["kind"] == "rule" else state["font"],
                    tags=("node", nid),
                )
                extra = (node.get("meta") or {}).get("extra")
                if extra:
                    canvas.create_text(
                        x + w / 2, y + h - max(6, 8 * z),
                        text=extra, fill="#c4b5fd",
                        font=state["font_small"],
                        tags=("node", nid),
                    )

            # Generous scrollregion so pan never hits a wall
            bbox = canvas.bbox(ALL)
            if bbox:
                pad = 4000
                canvas.config(
                    scrollregion=(
                        bbox[0] - pad,
                        bbox[1] - pad,
                        bbox[2] + pad,
                        bbox[3] + pad,
                    )
                )

        def frame_content_top_left(margin=28):
            """Pan so the top-left of drawn content sits near the top-left of the viewport."""
            canvas.update_idletasks()
            bbox = canvas.bbox(ALL)
            if not bbox:
                return
            pad = 4000
            sr_left = bbox[0] - pad
            sr_top = bbox[1] - pad
            sr_right = bbox[2] + pad
            sr_bottom = bbox[3] + pad
            canvas.config(scrollregion=(sr_left, sr_top, sr_right, sr_bottom))
            width = sr_right - sr_left
            height = sr_bottom - sr_top
            if width > 0:
                # Place content left edge (minus margin) at the left of the view
                canvas.xview_moveto(max(0.0, min(1.0, (bbox[0] - margin - sr_left) / width)))
            if height > 0:
                canvas.yview_moveto(max(0.0, min(1.0, (bbox[1] - margin - sr_top) / height)))

        def reset_view():
            """Zoom 1.0 + default fonts. Pan is applied after draw via frame_content_top_left."""
            canvas.delete(ALL)
            try:
                canvas.config(scrollregion=(0, 0, 1, 1))
                canvas.xview_moveto(0)
                canvas.yview_moveto(0)
            except Exception:
                pass
            state["zoom"] = 1.0
            state["font"].configure(size=state["base_font_size"])
            state["font_small"].configure(size=state["base_font_small"])

        def apply_default_collapsed():
            """Collapse categories flagged as minimized-by-default."""
            for nid, n in state["nodes"].items():
                if n.get("kind") not in ("header", "category"):
                    continue
                cid = (n.get("meta") or {}).get("cat_id")
                if not cid:
                    continue
                meta = self.get_category(cid) or {}
                if meta.get("collapsed_default"):
                    state["collapsed"].add(nid)

        def capture_view():
            """Snapshot zoom + scroll fractions for persistence."""
            try:
                xv = canvas.xview()
                yv = canvas.yview()
                win._mm_view_snapshot = {
                    "zoom": float(state.get("zoom") or 1.0),
                    "xview": float(xv[0]) if xv else 0.0,
                    "yview": float(yv[0]) if yv else 0.0,
                    "user": True,
                }
            except Exception:
                pass

        def apply_saved_view():
            try:
                canvas.xview_moveto(max(0.0, min(1.0, _init_xview)))
                canvas.yview_moveto(max(0.0, min(1.0, _init_yview)))
            except Exception:
                pass
            capture_view()

        def rebuild(reset_positions=False, restore_saved_view=False):
            if reset_positions and not restore_saved_view:
                reset_view()
            roots = build_graph()
            if reset_positions:
                state["collapsed"] = set()
                apply_default_collapsed()
            else:
                # keep collapsed ids that still exist
                state["collapsed"] = {c for c in state["collapsed"] if c in state["nodes"]}
            auto_layout(roots)
            # Keep font sizes aligned with current zoom before draw
            try:
                z = float(state.get("zoom") or 1.0)
                state["font"].configure(size=max(6, int(round(state["base_font_size"] * z))))
                state["font_small"].configure(size=max(5, int(round(state["base_font_small"] * z))))
            except Exception:
                pass
            draw()
            if restore_saved_view and _have_saved_view:
                apply_saved_view()
            elif reset_positions:
                # After layout + draw, pin top-left content into the viewport
                frame_content_top_left()
            capture_view()

        def set_all_collapsed(flag):
            if flag:
                state["collapsed"] = {
                    nid for nid, n in state["nodes"].items()
                    if n["kind"] in ("header", "category", "group") and n.get("children")
                }
            else:
                state["collapsed"].clear()
            # re-layout
            roots = [
                nid for nid, n in state["nodes"].items()
                if n.get("parent") is None
            ]
            auto_layout(roots)
            draw()

        def node_at(event):
            # Convert widget coords → canvas (accounts for pan via canvasx/y;
            # zoom is applied by scaling item coords so canvasx still works).
            cx = canvas.canvasx(event.x)
            cy = canvas.canvasy(event.y)
            items = canvas.find_overlapping(cx - 3, cy - 3, cx + 3, cy + 3)
            for it in items:
                tags = canvas.gettags(it)
                for t in tags:
                    if t in state["nodes"]:
                        return t
            return None

        def on_press(event):
            canvas.focus_set()
            nid = node_at(event)
            if nid:
                state["drag"] = {
                    "mode": "node",
                    "nid": nid,
                    "last_x": canvas.canvasx(event.x),
                    "last_y": canvas.canvasy(event.y),
                    "start_x": canvas.canvasx(event.x),
                    "start_y": canvas.canvasy(event.y),
                    "moved": False,
                }
            else:
                # Background drag → pan the viewport
                state["drag"] = {
                    "mode": "pan",
                    "last_x": event.x,
                    "last_y": event.y,
                }
                canvas.configure(cursor="fleur")

        def on_motion(event):
            drag = state["drag"]
            if not drag:
                return
            if drag.get("mode") == "create":
                return
            if drag["mode"] == "pan":
                dx = event.x - drag["last_x"]
                dy = event.y - drag["last_y"]
                drag["last_x"] = event.x
                drag["last_y"] = event.y
                canvas.scan_mark(event.x - dx, event.y - dy)
                canvas.scan_dragto(event.x, event.y, gain=1)
                return

            nid = drag["nid"]
            cx = canvas.canvasx(event.x)
            cy = canvas.canvasy(event.y)
            dx = cx - drag["last_x"]
            dy = cy - drag["last_y"]
            z = state["zoom"] or 1.0
            dx_logical = dx / z
            dy_logical = dy / z
            drag["last_x"] = cx
            drag["last_y"] = cy
            if abs(cx - drag.get("start_x", cx)) > 4 or abs(cy - drag.get("start_y", cy)) > 4:
                drag["moved"] = True
            to_move = [nid] + descendants(nid)
            for mid in to_move:
                node = state["nodes"].get(mid)
                if not node:
                    continue
                node["x"] = node.get("x", 0) + dx_logical
                node["y"] = node.get("y", 0) + dy_logical
                # Move existing canvas items tagged with this node id (no full redraw)
                try:
                    canvas.move(mid, dx, dy)
                except Exception:
                    pass

        def apply_drop_reparent(src_nid, target_nid) -> bool:
            """
            Persist hierarchy change when dropping a node onto another.
            Returns True if data changed.
            """
            src = state["nodes"].get(src_nid)
            tgt = state["nodes"].get(target_nid)
            if not src or not tgt or src_nid == target_nid:
                return False

            # Cannot drop onto own descendant (would orphan cycle in visual tree)
            if target_nid in descendants(src_nid):
                return False

            src_kind = src.get("kind")
            tgt_kind = tgt.get("kind")
            src_meta = src.get("meta") or {}
            tgt_meta = tgt.get("meta") or {}

            # --- Categories / headers onto categories / headers / uncategorised ---
            if src_kind in ("header", "category"):
                src_cid = src_meta.get("cat_id")
                if not src_cid or not self.get_category(src_cid):
                    return False

                if tgt_kind in ("header", "category"):
                    tgt_cid = tgt_meta.get("cat_id")
                    if not tgt_cid or not self.get_category(tgt_cid):
                        return False
                    if not self.set_category_parent(src_cid, tgt_cid):
                        return False
                    self.save_data()
                    self.update_all_category_menus()
                    self.refresh_list()
                    return True

                if tgt_kind == "group" and target_nid == "group:uncategorised":
                    # Promote to top-level (no parent)
                    if not self.set_category_parent(src_cid, None):
                        return False
                    self.save_data()
                    self.update_all_category_menus()
                    self.refresh_list()
                    return True

                return False

            # --- Tasks onto categories / uncategorised ---
            if src_kind == "task":
                task_id = src_meta.get("task_id")
                task = next((t for t in self.data["active"] if t.get("id") == task_id), None)
                if not task:
                    return False

                if tgt_kind in ("header", "category"):
                    tgt_cid = tgt_meta.get("cat_id")
                    if not tgt_cid or not self.get_category(tgt_cid):
                        return False
                    if task.get("category") == tgt_cid:
                        return False
                    task["category"] = tgt_cid
                    self.save_data()
                    self.refresh_list()
                    return True

                if tgt_kind == "group" and target_nid == "group:uncategorised":
                    if not task.get("category"):
                        return False
                    task["category"] = ""
                    self.save_data()
                    self.refresh_list()
                    return True

                # Drop task onto another task → that task becomes a prerequisite
                if tgt_kind == "task":
                    other_id = tgt_meta.get("task_id")
                    other = next((t for t in self.data["active"] if t.get("id") == other_id), None)
                    if not other:
                        return False
                    # Also inherit category from the target task
                    new_cat = other.get("category") or ""
                    if new_cat:
                        task["category"] = new_cat
                    ref = self.prereq_ref_for_task(other)
                    prereqs = list(task.get("prerequisites") or [])
                    # Avoid self-dependency / duplicate
                    self_ref = self.prereq_ref_for_task(task)
                    if ref and ref != self_ref and ref not in prereqs:
                        prereqs.append(ref)
                        task["prerequisites"] = prereqs
                        self.save_data()
                        self.refresh_list()
                        return True
                    if new_cat:
                        self.save_data()
                        self.refresh_list()
                        return True
                    return False

                return False

            return False

        def on_release(event):
            drag = state.get("drag")
            if drag and drag.get("mode") == "pan":
                canvas.configure(cursor="")
                state["drag"] = None
                capture_view()
                return

            if drag and drag.get("mode") == "node" and drag.get("moved"):
                src_nid = drag.get("nid")
                # Find drop target under cursor, ignoring the dragged node itself
                target_nid = None
                cx = canvas.canvasx(event.x)
                cy = canvas.canvasy(event.y)
                items = canvas.find_overlapping(cx - 4, cy - 4, cx + 4, cy + 4)
                for it in items:
                    tags = canvas.gettags(it)
                    for t in tags:
                        if t in state["nodes"] and t != src_nid and t not in descendants(src_nid):
                            target_nid = t
                            break
                    if target_nid:
                        break

                if target_nid and apply_drop_reparent(src_nid, target_nid):
                    # Rebuild tree from data so hierarchy edges match new parents
                    rebuild(False)
                else:
                    # Free-position drag moved node rects via canvas.move; redraw edges once
                    draw()

            state["drag"] = None

        def on_double(event):
            nid = node_at(event)
            if not nid:
                return
            node = state["nodes"].get(nid)
            if not node or node["kind"] not in ("header", "category", "group"):
                return
            if not node.get("children"):
                return
            if nid in state["collapsed"]:
                state["collapsed"].discard(nid)
            else:
                state["collapsed"].add(nid)
            roots = [n for n, nd in state["nodes"].items() if nd.get("parent") is None]
            auto_layout(roots)
            draw()

        def refresh_after_edit():
            self.update_all_category_menus()
            self.refresh_list()
            rebuild(False)

        def on_right_click(event):
            nid = node_at(event)
            if not nid:
                return
            node = state["nodes"].get(nid)
            if not node:
                return
            kind = node.get("kind")
            meta = node.get("meta") or {}
            menu = Menu(win, tearoff=0)

            if kind == "task":
                task_id = meta.get("task_id")
                task = next((t for t in self.data["active"] if t.get("id") == task_id), None)
                if not task:
                    return

                def do_complete_or_skip(tid, status):
                    self.complete_or_skip(tid, status)
                    refresh_after_edit()

                menu.add_command(
                    label="Complete",
                    command=lambda tid=task_id: do_complete_or_skip(tid, "completed"),
                )
                menu.add_command(
                    label="Skip",
                    command=lambda tid=task_id: do_complete_or_skip(tid, "skipped"),
                )
                menu.add_separator()
                pri_menu = Menu(menu, tearoff=0)
                for stars, label in ((0, "☆"), (1, "★"), (2, "★★"), (3, "★★★")):
                    pri_menu.add_command(
                        label=label,
                        command=lambda tid=task_id, p=stars: (
                            self.set_task_priority(tid, p),
                            refresh_after_edit(),
                        ),
                    )
                menu.add_cascade(label="Priority", menu=pri_menu)
                menu.add_separator()
                menu.add_command(
                    label="Edit",
                    command=lambda tid=task_id: self.open_edit_window(
                        tid, on_saved=refresh_after_edit
                    ),
                )
                menu.add_command(
                    label="Notes",
                    command=lambda tid=task_id: self.open_notes_editor(
                        "task", tid, title_hint=(task.get("text") or ""),
                        on_saved=refresh_after_edit,
                    ),
                )
                if task.get("type") == "recurring":
                    series_id = task.get("series_id") or task["id"]
                    title = task.get("text", "")
                    menu.add_command(
                        label="Series History",
                        command=lambda: self.open_series_history_window(
                            series_id, title_hint=title
                        ),
                    )
                menu.add_separator()
                menu.add_command(
                    label="Delete",
                    command=lambda tid=task_id: (
                        self.delete_task(tid),
                        refresh_after_edit(),
                    ),
                )

            elif kind in ("header", "category"):
                cat_id = meta.get("cat_id")
                if not cat_id or not self.get_category(cat_id):
                    return
                is_header = self.is_header_category(cat_id)
                menu.add_command(
                    label="Rename",
                    command=lambda i=cat_id: (
                        self.rename_category_dialog(i),
                        refresh_after_edit(),
                    ),
                )
                menu.add_command(
                    label="Notes",
                    command=lambda i=cat_id: self.open_notes_editor(
                        "category",
                        i,
                        title_hint=(self.category_name(i) or ""),
                        on_saved=refresh_after_edit,
                    ),
                )
                if is_header:
                    menu.add_command(
                        label="Colour",
                        command=lambda i=cat_id: (
                            self.recolour_category_dialog(i),
                            refresh_after_edit(),
                        ),
                    )
                else:
                    menu.add_command(
                        label="Make Header Category",
                        command=lambda i=cat_id: (
                            self.promote_to_header_dialog(i),
                            refresh_after_edit(),
                        ),
                    )

                def toggle_collapsed_default(cid=cat_id, nid=nid):
                    meta = self.get_category(cid)
                    if not meta:
                        return
                    meta["collapsed_default"] = not bool(meta.get("collapsed_default"))
                    self.save_data()
                    if meta["collapsed_default"]:
                        state["collapsed"].add(nid)
                    else:
                        state["collapsed"].discard(nid)
                    # re-layout so children hide/show
                    roots = [n for n, nd in state["nodes"].items() if nd.get("parent") is None]
                    auto_layout(roots)
                    draw()

                min_default = bool((self.get_category(cat_id) or {}).get("collapsed_default"))
                menu.add_command(
                    label="Expand by default" if min_default else "Minimize by default",
                    command=toggle_collapsed_default,
                )
                menu.add_command(
                    label="+ Sub",
                    command=lambda i=cat_id: (
                        self.add_sub_category_dialog(i),
                        refresh_after_edit(),
                    ),
                )

                # Header roots (and root categories): pin default mind-map column position
                if node.get("parent") is None and kind in ("header", "category"):
                    def set_default_pos(n=nid):
                        key = root_position_key(n)
                        if not key:
                            return
                        nd = state["nodes"].get(n) or {}
                        positions = self.data.setdefault("mind_map_root_positions", {})
                        positions[key] = {
                            "x": float(nd.get("x") or 0),
                            "y": float(nd.get("y") or 0),
                        }
                        self.save_data()

                    def clear_default_pos(n=nid):
                        key = root_position_key(n)
                        if not key:
                            return
                        positions = self.data.setdefault("mind_map_root_positions", {})
                        if key in positions:
                            positions.pop(key, None)
                            self.save_data()

                    key = root_position_key(nid)
                    has_pin = bool(key and (self.data.get("mind_map_root_positions") or {}).get(key))
                    menu.add_separator()
                    menu.add_command(
                        label="Set as Default Mindmap Position",
                        command=set_default_pos,
                    )
                    if has_pin:
                        menu.add_command(
                            label="Clear Default Mindmap Position",
                            command=clear_default_pos,
                        )

                menu.add_separator()
                menu.add_command(
                    label="Delete",
                    command=lambda i=cat_id: (
                        self.delete_category_dialog(i),
                        refresh_after_edit(),
                    ),
                )

            elif kind == "group" and nid == "group:uncategorised":
                def set_uncat_default(n=nid):
                    nd = state["nodes"].get(n) or {}
                    positions = self.data.setdefault("mind_map_root_positions", {})
                    positions["__uncategorised__"] = {
                        "x": float(nd.get("x") or 0),
                        "y": float(nd.get("y") or 0),
                    }
                    self.save_data()

                def clear_uncat_default():
                    positions = self.data.setdefault("mind_map_root_positions", {})
                    if "__uncategorised__" in positions:
                        positions.pop("__uncategorised__", None)
                        self.save_data()

                has_pin = bool((self.data.get("mind_map_root_positions") or {}).get("__uncategorised__"))
                menu.add_command(
                    label="Set as Default Mindmap Position",
                    command=set_uncat_default,
                )
                if has_pin:
                    menu.add_command(
                        label="Clear Default Mindmap Position",
                        command=clear_uncat_default,
                    )

            elif kind == "rule":
                rule_id = meta.get("rule_id")
                if not rule_id:
                    return
                menu.add_command(
                    label="Edit",
                    command=lambda rid=rule_id: self.open_edit_completion_rule(
                        rid, on_saved=refresh_after_edit
                    ),
                )
                menu.add_separator()
                menu.add_command(
                    label="Delete",
                    command=lambda rid=rule_id: (
                        self.delete_completion_rule(rid),
                        refresh_after_edit(),
                    ),
                )
            else:
                return

            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        def on_wheel(event):
            # Zoom toward cursor via canvas.scale (no full redraw — much smoother)
            if getattr(event, "delta", 0):
                direction = 1 if event.delta > 0 else -1
            elif getattr(event, "num", None) == 4:
                direction = 1
            elif getattr(event, "num", None) == 5:
                direction = -1
            else:
                return

            old_zoom = state["zoom"] or 1.0
            new_zoom = max(0.35, min(2.5, old_zoom * (1.12 if direction > 0 else 1 / 1.12)))
            if abs(new_zoom - old_zoom) < 1e-9:
                return
            factor = new_zoom / old_zoom

            cx = canvas.canvasx(event.x)
            cy = canvas.canvasy(event.y)

            # Scale every drawn item around the cursor — O(items) matrix op, not recreate
            canvas.scale(ALL, cx, cy, factor, factor)
            state["zoom"] = new_zoom
            # Shared Font objects: existing text items pick up the new size automatically
            state["font"].configure(
                size=max(6, int(round(state["base_font_size"] * new_zoom)))
            )
            state["font_small"].configure(
                size=max(5, int(round(state["base_font_small"] * new_zoom)))
            )

            bbox = canvas.bbox(ALL)
            if bbox:
                pad = 4000
                canvas.config(
                    scrollregion=(
                        bbox[0] - pad,
                        bbox[1] - pad,
                        bbox[2] + pad,
                        bbox[3] + pad,
                    )
                )
            capture_view()


        def _cancel_tooltip_timer():
            aid = state.get("tooltip_after")
            if aid is not None:
                try:
                    win.after_cancel(aid)
                except Exception:
                    pass
                state["tooltip_after"] = None
            state["pending_tip_nid"] = None

        def hide_node_tooltip():
            _cancel_tooltip_timer()
            tip = state.get("tooltip")
            if tip is not None:
                try:
                    tip.destroy()
                except Exception:
                    pass
                state["tooltip"] = None
            state["tooltip_nid"] = None

        def _place_node_tooltip(nid):
            """Show due/notes tooltip once for a node (anchored near the node)."""
            node = state["nodes"].get(nid) or {}
            meta = node.get("meta") or {}
            lines = []
            if meta.get("due_tip"):
                lines.append(f"Due: {meta['due_tip']}")
            if meta.get("note_tip"):
                note = meta["note_tip"]
                if len(note) > 400:
                    note = note[:400] + "…"
                lines.append(f"Note: {note}")
            tip_text = "\n".join(lines)
            if not tip_text:
                return
            if state.get("tooltip_nid") == nid and state.get("tooltip") is not None:
                return
            # Destroy any previous tip without cancelling a new timer
            tip = state.get("tooltip")
            if tip is not None:
                try:
                    tip.destroy()
                except Exception:
                    pass
                state["tooltip"] = None

            tip = ctk.CTkToplevel(win)
            tip.overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except Exception:
                pass
            # Ignore mouse so hovering the tip doesn't spam Leave/Enter on the canvas
            try:
                tip.attributes("-transparentcolor", "")
            except Exception:
                pass
            frame = ctk.CTkFrame(tip, border_width=1, corner_radius=6)
            frame.pack(fill="both", expand=True)
            ctk.CTkLabel(frame, text=tip_text, anchor="w", justify="left").pack(padx=10, pady=8)
            tip.update_idletasks()

            # Position below the node in screen coords
            z = state.get("zoom") or 1.0
            try:
                canvas_x = canvas.winfo_rootx()
                canvas_y = canvas.winfo_rooty()
                # Convert logical node box → canvas → screen
                nx = node.get("x", 0) * z
                ny = node.get("y", 0) * z
                nh = node.get("h", 0) * z
                # Account for current scroll offset
                sx = canvas.canvasx(0)
                sy = canvas.canvasy(0)
                screen_x = int(canvas_x + (nx - sx))
                screen_y = int(canvas_y + (ny + nh - sy) + 6)
                tip.geometry(f"+{screen_x}+{screen_y}")
            except Exception:
                pass

            state["tooltip"] = tip
            state["tooltip_nid"] = nid

        def on_hover_motion(event):
            if state.get("drag"):
                hide_node_tooltip()
                return
            nid = node_at(event)
            has_tip = False
            if nid:
                node = state["nodes"].get(nid) or {}
                meta = node.get("meta") or {}
                has_tip = bool(meta.get("due_tip") or meta.get("note_tip"))

            if has_tip:
                # Already showing this node — do nothing
                if state.get("tooltip_nid") == nid and state.get("tooltip") is not None:
                    return
                # Already scheduled for this node — do nothing
                if state.get("pending_tip_nid") == nid:
                    return
                # Switch target: cancel old timer, schedule new (short delay kills spam)
                _cancel_tooltip_timer()
                if state.get("tooltip_nid") and state.get("tooltip_nid") != nid:
                    tip = state.get("tooltip")
                    if tip is not None:
                        try:
                            tip.destroy()
                        except Exception:
                            pass
                        state["tooltip"] = None
                    state["tooltip_nid"] = None
                state["pending_tip_nid"] = nid
                state["tooltip_after"] = win.after(100, lambda n=nid: _place_node_tooltip(n))
            else:
                hide_node_tooltip()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_motion)
        canvas.bind("<ButtonRelease-1>", on_release)
        canvas.bind("<Double-Button-1>", on_double)
        canvas.bind("<Button-3>", on_right_click)
        canvas.bind("<Control-Button-1>", on_right_click)
        canvas.bind("<MouseWheel>", on_wheel)
        canvas.bind("<Button-4>", on_wheel)
        canvas.bind("<Button-5>", on_wheel)
        canvas.bind("<Motion>", on_hover_motion)
        canvas.bind("<Leave>", lambda _e: hide_node_tooltip())

        def place_new_item(kind, event):
            """After dragging Create Category/Task onto the map."""
            z = state.get("zoom") or 1.0
            cx = canvas.canvasx(event.x)
            cy = canvas.canvasy(event.y)
            logical_x = cx / z
            logical_y = cy / z
            target_nid = node_at(event)

            if kind == "category":
                dialog = ctk.CTkInputDialog(text="Category name:", title="Create Category")
                name = (dialog.get_input() or "").strip()
                if not name:
                    return
                parent_id = None
                if target_nid:
                    tn = state["nodes"].get(target_nid) or {}
                    if tn.get("kind") in ("header", "category"):
                        parent_id = (tn.get("meta") or {}).get("cat_id")
                cid = str(uuid.uuid4())
                self.data.setdefault("categories", {})[cid] = {
                    "name": name,
                    "parent_id": parent_id,
                    "color": None,
                    "sort_order": len(self.category_children(parent_id)),
                }
                self.save_data()
                self.update_all_category_menus()
                self.refresh_list()
                rebuild(False)
                return

            # kind == "task"
            dialog = ctk.CTkInputDialog(text="Task name:", title="Create Task")
            name = (dialog.get_input() or "").strip()
            if not name:
                return
            category = ""
            prereqs = []
            if target_nid:
                tn = state["nodes"].get(target_nid) or {}
                meta = tn.get("meta") or {}
                if tn.get("kind") in ("header", "category"):
                    category = meta.get("cat_id") or ""
                elif tn.get("kind") == "task":
                    other = next(
                        (t for t in self.data["active"] if t.get("id") == meta.get("task_id")),
                        None,
                    )
                    if other:
                        category = other.get("category") or ""
                        prereqs = [self.prereq_ref_for_task(other)]
            task = {
                "id": str(uuid.uuid4()),
                "text": name,
                "type": "one-off",
                "category": category,
                "due": None,
                "created": now_iso(),
                "prerequisites": prereqs,
                "show_on_calendar": False,
                "priority": 0,
            }
            self.data.setdefault("active", []).append(task)
            self.save_data()
            self.refresh_list()
            rebuild(False)

        def on_create_press(kind, event):
            state["drag"] = {
                "mode": "create",
                "kind": kind,
                "last_x": event.x_root,
                "last_y": event.y_root,
                "moved": False,
            }
            try:
                canvas.configure(cursor="crosshair")
                win.configure(cursor="crosshair")
            except Exception:
                pass
            # Track beyond the button bounds
            win.bind_all("<B1-Motion>", on_create_motion)
            win.bind_all("<ButtonRelease-1>", on_create_release)

        def on_create_motion(event):
            drag = state.get("drag")
            if not drag or drag.get("mode") != "create":
                return
            if abs(event.x_root - drag["last_x"]) > 4 or abs(event.y_root - drag["last_y"]) > 4:
                drag["moved"] = True

        def on_create_release(event):
            drag = state.get("drag")
            try:
                win.unbind_all("<B1-Motion>")
                win.unbind_all("<ButtonRelease-1>")
                canvas.configure(cursor="")
                win.configure(cursor="")
            except Exception:
                pass
            if not drag or drag.get("mode") != "create":
                state["drag"] = None
                return
            kind = drag.get("kind")
            moved = drag.get("moved")
            state["drag"] = None
            if not moved:
                return
            try:
                wx = canvas.winfo_rootx()
                wy = canvas.winfo_rooty()
                ww = canvas.winfo_width()
                wh = canvas.winfo_height()
                if not (wx <= event.x_root <= wx + ww and wy <= event.y_root <= wy + wh):
                    return
                class _E:
                    pass
                e = _E()
                e.x = event.x_root - wx
                e.y = event.y_root - wy
                place_new_item(kind, e)
            except Exception:
                return

        for btn, kind in ((create_cat_btn, "category"), (create_task_btn, "task")):
            btn.bind("<ButtonPress-1>", lambda e, k=kind: on_create_press(k, e))
            btn.configure(command=lambda: None)

        rebuild(True, restore_saved_view=True)

        def _mm_show():
            try:
                _mm_reapply()
            except Exception:
                pass
            try:
                win.attributes("-alpha", 1.0)
            except Exception:
                pass
            try:
                win.lift()
            except Exception:
                pass
            win._mm_geo_lock = False

        # Fade in on next tick once geometry has been applied
        win.after(20, _mm_show)

        def _clear_ref():
            if getattr(self, "_mind_map_win", None) is win:
                self._mind_map_win = None

        _old_persist = persist_and_close

        def persist_and_close_and_clear():
            try:
                _old_persist()
            finally:
                _clear_ref()

        win.protocol("WM_DELETE_WINDOW", persist_and_close_and_clear)

