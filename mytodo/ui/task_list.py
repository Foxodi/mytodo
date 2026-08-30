"""Task list + calendar strip + row interactions."""
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
    activity_kind,
    next_due_datetime,
    make_spawned_task,
)

class TaskListMixin:
    def set_task_priority(self, task_id, priority: int):
        task = next((t for t in self.data["active"] if t.get("id") == task_id), None)
        if not task:
            return
        new_pri = clamp_priority(priority)
        old_pri = clamp_priority(task.get("priority", 0))
        task["priority"] = new_pri
        if new_pri >= 1:
            if old_pri != new_pri or self._task_star_order(task) is None:
                task["star_order"] = self._next_star_order(new_pri)
            task.pop("p3_order", None)  # legacy field
        else:
            task.pop("star_order", None)
            task.pop("p3_order", None)
        self.save_data()
        self.refresh_list()

    def _task_star_order(self, task):
        """Manual order within a star tier (supports legacy p3_order)."""
        val = task.get("star_order", task.get("p3_order"))
        if val is None:
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    def _next_star_order(self, priority: int) -> int:
        pri = clamp_priority(priority)
        best = -1
        for t in self.data.get("active", []):
            if clamp_priority(t.get("priority", 0)) != pri:
                continue
            so = self._task_star_order(t)
            if so is not None:
                best = max(best, so)
        return best + 1

    def _reorder_star_tasks(self, dragged_id: str, target_id: str, priority: int, place_before: bool = True):
        """Reorder tasks that share the same star level (1, 2, or 3)."""
        if dragged_id == target_id:
            return
        pri = clamp_priority(priority)
        if pri < 1:
            return
        peers = [
            t for t in self.data.get("active", [])
            if clamp_priority(t.get("priority", 0)) == pri
        ]
        if not peers:
            return
        peers.sort(key=lambda t: self._active_sort_key(t))
        ids = [t["id"] for t in peers]
        if dragged_id not in ids or target_id not in ids:
            return
        ids.remove(dragged_id)
        idx = ids.index(target_id)
        if not place_before:
            idx += 1
        ids.insert(idx, dragged_id)
        by_id = {t["id"]: t for t in peers}
        for i, tid in enumerate(ids):
            by_id[tid]["star_order"] = i
            by_id[tid].pop("p3_order", None)
        self.save_data()
        self.refresh_list(refresh_cal=False)

    def _hide_list_tooltip(self):
        tip = getattr(self, "_list_tooltip", None)
        if tip is not None:
            try:
                tip.destroy()
            except Exception:
                pass
            self._list_tooltip = None
        aid = getattr(self, "_list_tooltip_after", None)
        if aid is not None:
            try:
                self.after_cancel(aid)
            except Exception:
                pass
            self._list_tooltip_after = None

    def _show_list_tooltip(self, widget, text, event=None):
        self._hide_list_tooltip()
        if not text:
            return
        tip = ctk.CTkToplevel(self)
        tip.overrideredirect(True)
        try:
            tip.attributes("-topmost", True)
        except Exception:
            pass
        frame = ctk.CTkFrame(tip, border_width=1, corner_radius=6)
        frame.pack(fill="both", expand=True)
        ctk.CTkLabel(frame, text=text, anchor="w", justify="left").pack(padx=10, pady=8)
        tip.update_idletasks()
        try:
            x = widget.winfo_rootx()
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            tip.geometry(f"+{x}+{y}")
        except Exception:
            if event:
                tip.geometry(f"+{event.x_root + 12}+{event.y_root + 12}")
        self._list_tooltip = tip

    def _bind_list_tooltip(self, widget, text):
        def on_enter(e, w=widget, t=text):
            self._hide_list_tooltip()
            self._list_tooltip_after = self.after(
                100, lambda: self._show_list_tooltip(w, t, e)
            )

        def on_leave(_e):
            self._hide_list_tooltip()

        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)

    def task_prefix_icons(self, task) -> tuple:
        """Stars then ↻ 📅 💤 🔀 📝 — shared by list rows and mind-map labels."""
        stars = self.priority_stars(task.get("priority", 0))
        icons = ""
        if task.get("type") == "recurring":
            icons += "↻"
        if task.get("show_on_calendar"):
            icons += "📅"
        if task.get("passive"):
            icons += "💤"
        if (task.get("due_anchor") or "sticky") == "flexi":
            icons += "🔀"
        if (task.get("notes") or "").strip():
            icons += "📝"
        return stars, icons

    def priority_stars(self, priority) -> str:
        try:
            p = clamp_priority(priority)
        except (TypeError, ValueError):
            p = 0
        if p <= 0:
            return ""
        return "★" * p

    def show_context_menu(self, event, task_id):
        """Right-click menu for active tasks."""
        task = next((t for t in self.data["active"] if t["id"] == task_id), None)
        if not task:
            return

        menu = Menu(self, tearoff=0)
        menu.add_command(label="Edit", command=lambda: self.open_edit_window(task_id))
        menu.add_command(
            label="Notes",
            command=lambda: self.open_notes_editor("task", task_id, title_hint=task.get("text", "")),
        )

        if task.get("type") == "recurring":
            series_id = task.get("series_id") or task["id"]
            title = task.get("text", "")
            menu.add_command(
                label="Series History",
                command=lambda: self.open_series_history_window(series_id, title_hint=title),
            )

        pri_menu = Menu(menu, tearoff=0)
        for stars, label in ((0, "☆"), (1, "★"), (2, "★★"), (3, "★★★")):
            pri_menu.add_command(
                label=label,
                command=lambda p=stars: self.set_task_priority(task_id, p),
            )
        menu.add_cascade(label="Priority", menu=pri_menu)

        menu.add_separator()
        menu.add_command(label="Delete", command=lambda: self.delete_task(task_id))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def show_history_context_menu(self, event, history_id, refresh_cb=None):
        """Right-click menu for history rows (main list or series popup)."""
        entry = next((h for h in self.data["history"] if h.get("id") == history_id), None)
        if not entry:
            return

        menu = Menu(self, tearoff=0)

        # If this history row belongs to a series, offer the series history popup
        series_id = entry.get("series_id")
        if series_id:
            menu.add_command(
                label="Series History",
                command=lambda: self.open_series_history_window(
                    series_id, title_hint=entry.get("text", "")
                ),
            )
            menu.add_separator()

        def do_delete():
            entry = next((h for h in self.data["history"] if h.get("id") == history_id), None)
            label = (entry.get("text") or "").strip() if entry else ""
            if not label:
                label = "(unnamed)"
            status = (entry.get("status") or "entry") if entry else "entry"
            if messagebox.askyesno(
                "Confirm Delete",
                f"Delete history record for “{label}” ({status})?\n\n"
                "This only removes the past record — it does not change any active task.",
            ):
                self.data["history"] = [
                    h for h in self.data["history"] if h.get("id") != history_id
                ]
                self.save_data()
                self.refresh_list()
                if refresh_cb:
                    refresh_cb()

        menu.add_command(label="Delete", command=do_delete)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ========== MAIN LIST RENDER ==========
    def _sync_calendar_checkbox(self):
        self._sync_add_activity_checks()

    def _on_add_activity_toggle(self):
        self._sync_add_activity_checks()

    def _sync_add_activity_checks(self):
        """Calendar needs a date. Calendar and Passive disable each other."""
        has_date = bool((self.date_entry.get() or "").strip())
        try:
            cal_on = bool(self.calendar_var.get())
            pas_on = bool(self.passive_var.get())
        except Exception:
            cal_on, pas_on = False, False

        if cal_on and pas_on:
            # Calendar wins if both somehow on
            self.passive_var.set(False)
            pas_on = False

        if not has_date:
            if cal_on:
                self.calendar_var.set(False)
                cal_on = False
            self.calendar_check.configure(state="disabled")
        elif pas_on:
            if cal_on:
                self.calendar_var.set(False)
                cal_on = False
            self.calendar_check.configure(state="disabled")
        else:
            self.calendar_check.configure(state="normal")

        if cal_on:
            self.passive_var.set(False)
            self.passive_check.configure(state="disabled")
        else:
            self.passive_check.configure(state="normal")

    def build_calendar_strip(self):
        """Build empty 28-day cell grid; refresh_calendar fills content."""
        for w in self.calendar_frame.winfo_children():
            w.destroy()
        self._calendar_cells = []
        grid = ctk.CTkFrame(self.calendar_frame, fg_color="transparent")
        grid.pack(fill="x", padx=4, pady=4)
        for i in range(28):
            cell = ctk.CTkFrame(grid, width=44, height=36, corner_radius=5, border_width=1)
            cell.grid(row=i // 7, column=i % 7, padx=2, pady=2, sticky="nsew")
            cell.grid_propagate(False)
            lbl = ctk.CTkLabel(cell, text="", font=ctk.CTkFont(size=12))
            lbl.place(relx=0.5, rely=0.5, anchor="center")
            cell._day_label = lbl
            cell._day_date = None
            cell._day_tasks = []
            cell.bind("<Button-3>", lambda e, c=cell: self._on_calendar_right_click(c, e))
            lbl.bind("<Button-3>", lambda e, c=cell: self._on_calendar_right_click(c, e))
            self._calendar_cells.append(cell)
            for col in range(7):
                grid.grid_columnconfigure(col, weight=1)
        self.refresh_calendar()
        self._ensure_calendar_tooltip_lifecycle()

    def calendar_tasks_by_date(self):
        """Map date → list of {text, time_str} for active tasks flagged for calendar."""
        by_date = {}
        for t in self.data.get("active", []):
            if not t.get("show_on_calendar") or not t.get("due"):
                continue
            try:
                dt = datetime.fromisoformat(t["due"])
            except Exception:
                continue
            d = dt.date()
            time_str = ""
            if not (dt.hour == 0 and dt.minute == 0 and dt.second == 0):
                time_str = dt.strftime("%H:%M")
            by_date.setdefault(d, []).append(
                {"text": t.get("text") or "(unnamed)", "time": time_str}
            )
        return by_date

    def refresh_calendar(self):
        if not getattr(self, "_calendar_cells", None):
            return
        today = datetime.now().date()
        by_date = self.calendar_tasks_by_date()
        for i, cell in enumerate(self._calendar_cells):
            d = today + timedelta(days=i)
            tasks = by_date.get(d, [])
            cell._day_date = d
            cell._day_tasks = tasks
            # Label: weekday letter + day number (no month)
            wd = d.strftime("%a")[0]  # M T W…
            cell._day_label.configure(text=f"{wd}\n{d.day}")
            if tasks:
                cell.configure(
                    fg_color=("#93c5fd", "#1e3a5f"),
                    border_color=("#2563eb", "#60a5fa"),
                )
                cell._day_label.configure(text_color=("#1e3a8a", "#e0f2fe"))
            else:
                cell.configure(
                    fg_color=("gray90", "gray20"),
                    border_color=("gray70", "gray40"),
                )
                cell._day_label.configure(text_color=("gray20", "gray80"))
            # Hover bindings — only when the task set actually changes
            prev = getattr(cell, "_bound_has_tasks", False)
            has = bool(tasks)
            if has != prev:
                cell.unbind("<Enter>")
                cell.unbind("<Leave>")
                cell._day_label.unbind("<Enter>")
                cell._day_label.unbind("<Leave>")
                if has:
                    cell.bind("<Enter>", lambda e, c=cell: self._show_calendar_tooltip(c, e))
                    cell.bind("<Leave>", lambda e, c=cell: self._schedule_hide_calendar_tooltip(c))
                    cell._day_label.bind("<Enter>", lambda e, c=cell: self._show_calendar_tooltip(c, e))
                    cell._day_label.bind("<Leave>", lambda e, c=cell: self._schedule_hide_calendar_tooltip(c))
                cell._bound_has_tasks = has

    def _on_calendar_right_click(self, cell, event):
        self._hide_calendar_tooltip()
        day = getattr(cell, "_day_date", None)
        if day is None:
            return
        menu = Menu(self, tearoff=0)
        menu.add_command(
            label="Add Calendar",
            command=lambda d=day: self.open_add_calendar_popup(d),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _ensure_calendar_tooltip_lifecycle(self):
        """Hide stuck tooltips when the main window minimizes, unmaps, or loses the app."""
        if getattr(self, "_calendar_tip_lifecycle_bound", False):
            return
        self._calendar_tip_lifecycle_bound = True

        def _hide(_event=None):
            self._hide_calendar_tooltip()

        for seq in ("<Unmap>", "<Withdraw>"):
            try:
                self.bind(seq, _hide, add="+")
            except Exception:
                pass
        try:
            self.bind("<Configure>", self._maybe_hide_tooltip_if_iconified, add="+")
        except Exception:
            pass

    def _maybe_hide_tooltip_if_iconified(self, _event=None):
        try:
            if str(self.state()) in ("iconic", "withdrawn"):
                self._hide_calendar_tooltip()
        except Exception:
            pass

    def _pointer_over_calendar_hover(self, cell):
        """True if the mouse is still over the day cell or the live tooltip."""
        try:
            x, y = self.winfo_pointerxy()
        except Exception:
            return False
        widgets = [cell, getattr(cell, "_day_label", None), getattr(self, "_calendar_tooltip", None)]
        for w in widgets:
            if w is None:
                continue
            try:
                if not w.winfo_exists():
                    continue
                wx, wy = w.winfo_rootx(), w.winfo_rooty()
                ww, wh = w.winfo_width(), w.winfo_height()
                if wx <= x <= wx + ww and wy <= y <= wy + wh:
                    return True
            except Exception:
                continue
        return False

    def _schedule_hide_calendar_tooltip(self, cell=None):
        aid = getattr(self, "_calendar_tip_hide_after", None)
        if aid is not None:
            try:
                self.after_cancel(aid)
            except Exception:
                pass

        def _do():
            self._calendar_tip_hide_after = None
            if cell is not None and self._pointer_over_calendar_hover(cell):
                return
            self._hide_calendar_tooltip()

        self._calendar_tip_hide_after = self.after(80, _do)

    def _show_calendar_tooltip(self, cell, event=None):
        aid = getattr(self, "_calendar_tip_hide_after", None)
        if aid is not None:
            try:
                self.after_cancel(aid)
            except Exception:
                pass
            self._calendar_tip_hide_after = None

        tasks = getattr(cell, "_day_tasks", None) or []
        if not tasks:
            self._hide_calendar_tooltip()
            return

        # Reuse existing tooltip if it's already showing this cell
        existing = getattr(self, "_calendar_tooltip", None)
        if existing is not None and getattr(self, "_calendar_tooltip_cell", None) is cell:
            return

        self._hide_calendar_tooltip()
        lines = []
        for t in tasks:
            if t.get("time"):
                lines.append(f"{t['time']}  {t['text']}")
            else:
                lines.append(t["text"])
        text = "\n".join(lines)
        tip = ctk.CTkToplevel(self)
        tip.overrideredirect(True)
        # Not topmost — otherwise a stuck tip sits over every other app
        try:
            tip.transient(self)
        except Exception:
            pass
        frame = ctk.CTkFrame(tip, border_width=1, corner_radius=6)
        frame.pack(fill="both", expand=True)
        lbl = ctk.CTkLabel(
            frame,
            text=text,
            justify="left",
            anchor="w",
        )
        lbl.pack(fill="both", expand=True, padx=10, pady=8)
        tip.update_idletasks()
        try:
            x = cell.winfo_rootx()
            y = cell.winfo_rooty() + cell.winfo_height() + 4
            tip.geometry(f"+{x}+{y}")
        except Exception:
            if event:
                tip.geometry(f"+{event.x_root + 12}+{event.y_root + 12}")

        def _leave(_e=None, c=cell):
            self._schedule_hide_calendar_tooltip(c)

        def _enter(_e=None):
            aid2 = getattr(self, "_calendar_tip_hide_after", None)
            if aid2 is not None:
                try:
                    self.after_cancel(aid2)
                except Exception:
                    pass
                self._calendar_tip_hide_after = None

        for w in (tip, frame, lbl):
            try:
                w.bind("<Leave>", _leave, add="+")
                w.bind("<Enter>", _enter, add="+")
            except Exception:
                pass

        self._calendar_tooltip = tip
        self._calendar_tooltip_cell = cell

    def _hide_calendar_tooltip(self):
        aid = getattr(self, "_calendar_tip_hide_after", None)
        if aid is not None:
            try:
                self.after_cancel(aid)
            except Exception:
                pass
            self._calendar_tip_hide_after = None
        tip = getattr(self, "_calendar_tooltip", None)
        if tip is not None:
            try:
                tip.destroy()
            except Exception:
                pass
            self._calendar_tooltip = None
        self._calendar_tooltip_cell = None

    def _primary_filter_state(self):
        return {
            "header_map": self._filter_header_map,
            "sub_map": self._filter_sub_map,
            "header_var": self.filter_header_var.get(),
            "sub_var": self.filter_sub_var.get(),
            "show_due": self.show_due.get(),
            "show_not_due": self.show_not_due.get(),
            "show_completed": self.show_completed.get(),
            "show_skipped": self.show_skipped.get(),
            "show_p0": self.show_priority_0.get(),
            "show_p1": self.show_priority_1.get(),
            "show_p2": self.show_priority_2.get(),
            "show_p3": self.show_priority_3.get(),
            "show_passive": bool(self.show_passive.get()),
            "show_active": bool(self.show_active.get()),
            "show_one_off": bool(self.show_one_off.get()),
            "show_recurring": bool(self.show_recurring.get()),
            "show_calendar_kind": bool(self.show_calendar_kind.get()),
            "search": (self.list_search_var.get() if getattr(self, "list_search_var", None) else "") or "",
        }

    def _secondary_filter_state(self):
        return {
            "header_map": self._filter_header_map_b,
            "sub_map": self._filter_sub_map_b,
            "header_var": self.filter_header_var_b.get(),
            "sub_var": self.filter_sub_var_b.get(),
            "show_due": self.show_due_b.get(),
            "show_not_due": self.show_not_due_b.get(),
            "show_completed": self.show_completed_b.get(),
            "show_skipped": self.show_skipped_b.get(),
            "show_p0": self.show_priority_0_b.get(),
            "show_p1": self.show_priority_1_b.get(),
            "show_p2": self.show_priority_2_b.get(),
            "show_p3": self.show_priority_3_b.get(),
            "show_passive": bool(self.show_passive_b.get()),
            "show_active": bool(self.show_active_b.get()),
            "show_one_off": bool(self.show_one_off_b.get()),
            "show_recurring": bool(self.show_recurring_b.get()),
            "show_calendar_kind": bool(self.show_calendar_kind_b.get()),
            "search": (self.list_search_var_b.get() if getattr(self, "list_search_var_b", None) else "") or "",
        }

    def _active_sort_key(self, task, status=None) -> str:
        if status is None:
            status = self.task_status(task)
        due_part = task.get("due") or "9999"
        if status == "blocked":
            due_part = "8" + due_part
        pri = clamp_priority(task.get("priority", 0))
        # Higher stars first. Within ★ / ★★ / ★★★, respect manual star_order.
        pri_rank = 3 - pri
        if pri >= 1:
            order = self._task_star_order(task)
            if order is None:
                order = 10**9
            return f"{pri_rank:02d}-{order:010d}-{due_part}"
        return f"{pri_rank:02d}-{due_part}"

    def _row_fingerprint(self, kind, data, status=None) -> tuple:
        """Stable signature of everything a list row displays. Mismatch → rebuild that row."""
        if kind == "active":
            t = data
            try:
                pri = clamp_priority(t.get("priority", 0))
            except (TypeError, ValueError):
                pri = 0
            weekdays = tuple(
                int(d) for d in (t.get("weekdays") or [])
                if str(d).strip() != "" and str(d).lstrip("-").isdigit()
            )
            return (
                "active",
                t.get("id"),
                t.get("text") or "",
                t.get("category") or "",
                t.get("due") or "",
                t.get("type") or "",
                t.get("frequency") or "",
                t.get("interval"),
                weekdays,
                pri,
                self._task_star_order(t),
                as_bool(t.get("passive")),
                bool(t.get("show_on_calendar")),
                (t.get("due_anchor") or "sticky"),
                (t.get("notes") or "").strip(),
                tuple(t.get("prerequisites") or []),
                status or self.task_status(t),
            )
        h = data
        return (
            "history",
            h.get("id"),
            h.get("status") or "",
            h.get("completed_at") or "",
            h.get("text") or "",
            h.get("category") or "",
            h.get("original_due") or "",
            h.get("type") or "",
        )

    def _collect_list_items(self, fs) -> list:
        """Filtered + sorted rows for one list pane (active first, then history)."""
        header_id = fs["header_map"].get(fs["header_var"])
        sub_id = fs["sub_map"].get(fs["sub_var"])
        show_due = fs["show_due"]
        show_not_due = fs["show_not_due"]
        show_completed = fs["show_completed"]
        show_skipped = fs["show_skipped"]
        show_p0 = fs["show_p0"]
        show_p1 = fs["show_p1"]
        show_p2 = fs["show_p2"]
        show_p3 = fs.get("show_p3", True)
        show_passive = bool(fs.get("show_passive", True))
        show_active = bool(fs.get("show_active", True))
        show_one_off = bool(fs.get("show_one_off", True))
        show_recurring = bool(fs.get("show_recurring", True))
        show_calendar_kind = bool(fs.get("show_calendar_kind", True))
        search_q = (fs.get("search") or "").strip().lower()

        def matches_kind(item, *, is_history=False) -> bool:
            # Exclusive: calendar | passive | active
            kind = activity_kind(item)
            if kind == "passive" and not show_passive:
                return False
            if kind == "active" and not show_active:
                return False
            if kind == "calendar" and not show_calendar_kind:
                return False
            is_rec = item.get("type") == "recurring"
            if is_rec and not show_recurring:
                return False
            if (not is_rec) and not show_one_off:
                return False
            return True

        sub_desc = set(self.category_descendants(sub_id)) if sub_id else None
        root_cache = {}

        def matches_category_filters(cat_val: str) -> bool:
            if header_id is None and sub_id is None:
                return True
            if not cat_val:
                return False
            cid = cat_val if self.get_category(cat_val) else None
            if cid is None:
                return False
            if sub_id is not None:
                return cid == sub_id or cid in sub_desc
            if header_id is not None:
                if cid not in root_cache:
                    root_cache[cid] = self.category_root_id(cid)
                return root_cache[cid] == header_id
            return True

        def matches_search(text: str, category: str = "", notes: str = "") -> bool:
            if not search_q:
                return True
            hay = " ".join(
                [
                    text or "",
                    category or "",
                    self.category_path(category) if category and self.get_category(category) else "",
                    notes or "",
                ]
            ).lower()
            return search_q in hay

        active_items = []
        for t in self.data["active"]:
            status = self.task_status(t)
            if status == "due" and not show_due:
                continue
            if status in ("not_due", "blocked") and not show_not_due:
                continue
            if not matches_category_filters(t.get("category", "")):
                continue
            if not matches_kind(t):
                continue
            try:
                pri = clamp_priority(t.get("priority", 0))
            except (TypeError, ValueError):
                pri = 0
            if pri == 0 and not show_p0:
                continue
            if pri == 1 and not show_p1:
                continue
            if pri == 2 and not show_p2:
                continue
            if pri == 3 and not show_p3:
                continue
            if not matches_search(
                t.get("text") or "",
                t.get("category") or "",
                t.get("notes") or "",
            ):
                continue
            sort_key = self._active_sort_key(t, status)
            active_items.append(
                {
                    "kind": "active",
                    "data": t,
                    "row_id": t.get("id"),
                    "sort_key": sort_key,
                    "status": status,
                    "fp": self._row_fingerprint("active", t, status),
                }
            )

        history_items = []
        if show_completed or show_skipped:
            for h in self.data["history"]:
                status = h.get("status", "")
                if status == "completed" and not show_completed:
                    continue
                if status == "skipped" and not show_skipped:
                    continue
                if not matches_category_filters(h.get("category", "")):
                    continue
                if not matches_kind(h, is_history=True):
                    continue
                if not matches_search(
                    h.get("text") or "",
                    h.get("category") or "",
                    h.get("notes") or "",
                ):
                    continue
                rid = h.get("id")
                if not rid:
                    continue
                history_items.append(
                    {
                        "kind": "history",
                        "data": h,
                        "row_id": rid,
                        "sort_key": h.get("completed_at") or "",
                        "status": status,
                        "fp": self._row_fingerprint("history", h),
                    }
                )

        active_items.sort(key=lambda x: x["sort_key"])
        history_items.sort(key=lambda x: x["sort_key"], reverse=True)
        return active_items + history_items

    def refresh_list(self, refresh_cal=True, force_full=False):
        self.build_runtime_caches()
        if refresh_cal:
            self.refresh_calendar()
        self._sync_task_list(self.list_frame, self._primary_filter_state(), force_full=force_full)
        if self.split_enabled and getattr(self, "list_frame_b", None) is not None:
            self._sync_task_list(self.list_frame_b, self._secondary_filter_state(), force_full=force_full)
        self.clear_runtime_caches()

    def _row_registry(self, list_frame) -> dict:
        """Per-pane map of (kind, id) -> row widget. Never touch CTkScrollableFrame internals."""
        reg = getattr(list_frame, "_todo_row_registry", None)
        if reg is None:
            reg = {}
            list_frame._todo_row_registry = reg
        return reg

    def _clear_empty_labels(self, list_frame):
        for w in list(getattr(list_frame, "_todo_empty_widgets", []) or []):
            try:
                w.destroy()
            except Exception:
                pass
        list_frame._todo_empty_widgets = []

    def _sync_task_list(self, list_frame, fs, force_full=False):
        """
        Incremental list update via an explicit row registry (safe with CTkScrollableFrame).
        force_full=True rebuilds every row (used on first open).
        """
        items = self._collect_list_items(fs)
        reg = self._row_registry(list_frame)
        self._clear_empty_labels(list_frame)

        if force_full or not reg:
            for w in list(reg.values()):
                try:
                    w.destroy()
                except Exception:
                    pass
            reg.clear()

        existing = dict(reg)

        if not items:
            for w in list(existing.values()):
                try:
                    w.destroy()
                except Exception:
                    pass
            reg.clear()
            lbl = ctk.CTkLabel(
                list_frame, text="No tasks match the current filters.", text_color="gray"
            )
            lbl.pack(pady=40)
            lbl._todo_empty = True
            list_frame._todo_empty_widgets = [lbl]
            return

        widgets_in_order = []
        kept_keys = set()

        for item in items:
            key = (item["kind"], item["row_id"])
            w = existing.get(key)
            fp = item["fp"]
            if (
                not force_full
                and w is not None
                and getattr(w, "_todo_fp", None) == fp
            ):
                try:
                    if not w.winfo_exists():
                        w = None
                except Exception:
                    w = None
            if w is not None and getattr(w, "_todo_fp", None) == fp and not force_full:
                w._todo_sort_key = item["sort_key"]
                kept_keys.add(key)
                widgets_in_order.append(w)
                continue

            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass
                existing.pop(key, None)
                reg.pop(key, None)

            if item["kind"] == "active":
                w = self.create_active_row(
                    item["data"],
                    status=item.get("status"),
                    parent=list_frame,
                    sort_key=item.get("sort_key"),
                    pack=False,
                )
            else:
                w = self.create_history_row(item["data"], parent=list_frame, pack=False)
            if w is None:
                continue
            w._todo_fp = fp
            kept_keys.add(key)
            reg[key] = w
            widgets_in_order.append(w)

        for key, w in list(existing.items()):
            if key not in kept_keys:
                try:
                    w.destroy()
                except Exception:
                    pass
                reg.pop(key, None)

        # Keep registry aligned with surviving widgets
        reg.clear()
        for w in widgets_in_order:
            reg[(w._todo_row_kind, w._todo_row_id)] = w

        # Re-pack into desired order
        for w in widgets_in_order:
            try:
                w.pack_forget()
            except Exception:
                pass
        for w in widgets_in_order:
            try:
                w.pack(fill="x", pady=2, padx=4)
            except Exception:
                pass

    def _list_row_widgets(self, parent, exclude_id=None):
        """Visible active rows in list order (any priority), for drop-line geometry."""
        rows = []
        for w in parent.winfo_children():
            if getattr(w, "_todo_row_kind", None) != "active":
                continue
            tid = getattr(w, "_todo_row_id", None)
            if not tid or (exclude_id and tid == exclude_id):
                continue
            try:
                rows.append(
                    {
                        "id": tid,
                        "widget": w,
                        "y": w.winfo_rooty(),
                        "h": max(1, w.winfo_height()),
                        "priority": int(getattr(w, "_todo_priority", 0) or 0),
                    }
                )
            except Exception:
                pass
        rows.sort(key=lambda r: r["y"])
        return rows

    def _set_row_bg(self, frame, bg: str, saved=None, skip_buttons=True):
        """Recolor a row frame + labels. Keeps category color swatch intact."""
        if saved is None:
            saved = {}
            try:
                saved[frame] = frame.cget("bg")
                frame.configure(bg=bg)
            except Exception:
                pass
            stack = list(frame.winfo_children())
            while stack:
                child = stack.pop()
                try:
                    if skip_buttons and isinstance(child, tk.Button):
                        continue
                    # Category header color sticker must not be overwritten
                    if getattr(child, "_todo_cat_swatch", False):
                        continue
                    saved[child] = child.cget("bg")
                    child.configure(bg=bg)
                    stack.extend(child.winfo_children())
                except Exception:
                    pass
            return saved
        for w, old_bg in saved.items():
            try:
                if w.winfo_exists():
                    w.configure(bg=old_bg)
            except Exception:
                pass
        return None

    def _clear_drop_line(self):
        line = getattr(self, "_star_drop_line", None)
        if line is not None:
            try:
                line.destroy()
            except Exception:
                pass
            self._star_drop_line = None

    def _show_star_drop_line(self, parent, y_root, exclude_id, priority: int):
        """
        Draw insertion line only for a valid same-star reorder.
        No line when the cursor is over a different priority tier.
        """
        self._clear_drop_line()
        rows = self._list_row_widgets(parent, exclude_id=None)
        if not rows:
            return None, True

        pri = clamp_priority(priority)
        peers = [r for r in rows if r["priority"] == pri and r["id"] != exclude_id]
        if not peers:
            return None, True

        # Which peer is the cursor nearest to?
        nearest = min(peers, key=lambda r: abs((r["y"] + r["h"] / 2) - y_root))
        place_before = y_root < nearest["y"] + nearest["h"] / 2
        target_id = nearest["id"]

        # Only show the line when the cursor is actually near this peer
        # (within the peer row, or in the gap to the next/prev same-star peer)
        near = nearest["y"] - 8 <= y_root <= nearest["y"] + nearest["h"] + 8
        if not near:
            # Also allow the gap between two consecutive peers of this star level
            peers_sorted = sorted(peers, key=lambda r: r["y"])
            in_gap = False
            for a, b in zip(peers_sorted, peers_sorted[1:]):
                gap_top = a["y"] + a["h"]
                gap_bot = b["y"]
                if gap_top - 4 <= y_root <= gap_bot + 4:
                    in_gap = True
                    # Choose side based on closer edge
                    if abs(y_root - gap_top) <= abs(y_root - gap_bot):
                        target_id, place_before = a["id"], False
                        nearest = a
                    else:
                        target_id, place_before = b["id"], True
                        nearest = b
                    break
            if not in_gap:
                return None, True

        try:
            parent.update_idletasks()
            parent_y = parent.winfo_rooty()
        except Exception:
            return None, True

        if place_before:
            line_y = nearest["y"] - parent_y
        else:
            line_y = nearest["y"] + nearest["h"] - parent_y

        line = tk.Frame(parent, height=3, bg="#38bdf8", highlightthickness=0)
        try:
            line.place(x=4, y=max(0, line_y - 1), relwidth=1, width=-8)
            line.lift()
        except Exception:
            try:
                line.destroy()
            except Exception:
                pass
            return None, True
        self._star_drop_line = line
        return target_id, place_before

    def _end_star_drag_ui(self, drag):
        if not drag:
            return
        frame = drag.get("frame")
        saved = drag.get("saved_bgs")
        if frame is not None and saved is not None:
            self._set_row_bg(frame, "", saved=saved)
            try:
                frame.configure(cursor="")
            except Exception:
                pass
        # Re-apply hover if pointer still over this row
        if frame is not None and getattr(self, "_hover_row", None) is frame:
            self._apply_hover_highlight(frame, True)
        self._clear_drop_line()

    def _apply_hover_highlight(self, frame, on: bool):
        """Subtle hover tint for any active row (skipped while that row is dragging)."""
        drag = getattr(self, "_star_drag", None)
        if drag and drag.get("frame") is frame and drag.get("moved"):
            return
        HOVER = "#333b4a"
        DEFAULT = "#2b2b2b"
        if on:
            if getattr(frame, "_hover_saved", None) is None:
                frame._hover_saved = self._set_row_bg(frame, HOVER)
        else:
            saved = getattr(frame, "_hover_saved", None)
            if saved is not None:
                self._set_row_bg(frame, DEFAULT, saved=saved)
                frame._hover_saved = None

    def _bind_row_hover(self, frame):
        def on_enter(_event=None):
            self._hover_row = frame
            self._apply_hover_highlight(frame, True)

        def on_leave(event):
            # Ignore leave if pointer moved into a child of this row
            try:
                x, y = frame.winfo_pointerxy()
                widget = frame.winfo_containing(x, y)
                if widget is not None:
                    w = widget
                    while w is not None:
                        if w is frame:
                            return
                        w = w.master
            except Exception:
                pass
            if getattr(self, "_hover_row", None) is frame:
                self._hover_row = None
            self._apply_hover_highlight(frame, False)

        frame.bind("<Enter>", on_enter, add="+")
        frame.bind("<Leave>", on_leave, add="+")
        for child in frame.winfo_children():
            try:
                child.bind("<Enter>", on_enter, add="+")
                child.bind("<Leave>", on_leave, add="+")
            except Exception:
                pass

    def _bind_star_row_drag(self, frame, label, task_id, list_parent, priority: int):
        """Left-drag a starred row to reorder it against same-star peers only."""
        pri = clamp_priority(priority)

        def on_press(event):
            self._end_star_drag_ui(getattr(self, "_star_drag", None))
            self._star_drag = {
                "id": task_id,
                "priority": pri,
                "start_y": event.y_root,
                "moved": False,
                "parent": list_parent,
                "frame": frame,
                "target_id": None,
                "place_before": True,
                "saved_bgs": None,
            }
            try:
                frame.configure(cursor="hand2")
                label.configure(cursor="hand2")
            except Exception:
                pass

        def on_motion(event):
            drag = getattr(self, "_star_drag", None)
            if not drag or drag.get("id") != task_id:
                return
            if abs(event.y_root - drag["start_y"]) <= 6 and not drag.get("moved"):
                return
            if not drag.get("moved"):
                drag["moved"] = True
                # Clear hover save so drag highlight owns the colours
                frame._hover_saved = None
                drag["saved_bgs"] = self._set_row_bg(frame, "#1e3a5f")
            parent = drag.get("parent") or list_parent
            target_id, place_before = self._show_star_drop_line(
                parent, event.y_root, exclude_id=task_id, priority=pri
            )
            drag["target_id"] = target_id
            drag["place_before"] = place_before

        def on_release(event):
            drag = getattr(self, "_star_drag", None)
            if not drag or drag.get("id") != task_id:
                self._end_star_drag_ui(drag)
                self._star_drag = None
                return
            moved = drag.get("moved")
            target_id = drag.get("target_id")
            place_before = drag.get("place_before", True)
            parent = drag.get("parent") or list_parent
            if moved:
                tid2, before2 = self._show_star_drop_line(
                    parent, event.y_root, exclude_id=task_id, priority=pri
                )
                if tid2:
                    target_id, place_before = tid2, before2
            self._end_star_drag_ui(drag)
            self._star_drag = None
            try:
                label.configure(cursor="")
            except Exception:
                pass
            if not moved or not target_id:
                return
            self._reorder_star_tasks(
                task_id, target_id, priority=pri, place_before=place_before
            )

        for w in (frame, label):
            w.bind("<ButtonPress-1>", on_press, add="+")
            w.bind("<B1-Motion>", on_motion, add="+")
            w.bind("<ButtonRelease-1>", on_release, add="+")

    def create_active_row(self, task, status=None, parent=None, before=None, sort_key=None, pack=True):
        # Lightweight tk widgets — much faster than CTkFrame/CTkLabel per row
        ROW_BG = "#2b2b2b"
        FG = "#e5e5e5"
        FG_DIM = "#fbbf24"
        tid = task["id"]
        parent = parent or self.list_frame
        frame = tk.Frame(parent, bg=ROW_BG, highlightthickness=0)
        if status is None:
            status = self.task_status(task)
        if sort_key is None:
            sort_key = self._active_sort_key(task, status)
        frame._todo_row_kind = "active"
        frame._todo_row_id = tid
        frame._todo_sort_key = sort_key
        frame._todo_fp = self._row_fingerprint("active", task, status)
        if pack:
            pack_kwargs = {"fill": "x", "pady": 2, "padx": 4}
            if before is not None:
                try:
                    if before.winfo_exists():
                        pack_kwargs["before"] = before
                except Exception:
                    pass
            frame.pack(**pack_kwargs)
        frame.bind("<Button-3>", lambda e, i=tid: self.show_context_menu(e, i))

        cat = task.get("category", "")
        rt = getattr(self, "_rt", None)

        stars, icons = self.task_prefix_icons(task)
        note_text = (task.get("notes") or "").strip()
        prefix_host = tk.Frame(frame, bg=ROW_BG, width=108)
        prefix_host.pack(side="left", padx=(8, 6), fill="y")
        prefix_host.pack_propagate(False)
        # Same-height labels so hover highlight is one strip, not staggered boxes
        if stars:
            star_lbl = tk.Label(
                prefix_host, text=stars, anchor="center", bg=ROW_BG, fg=FG_DIM,
                font=("Segoe UI Symbol", 11),
            )
            star_lbl.pack(side="left", fill="y", pady=(5, 3))
        if icons:
            icon_lbl = tk.Label(
                prefix_host, text=icons, anchor="center", bg=ROW_BG, fg=FG_DIM,
                font=("Segoe UI Emoji", 11),
            )
            icon_lbl.pack(side="left", fill="y")
        if not stars and not icons:
            tk.Label(
                prefix_host, text=" ", bg=ROW_BG, fg=FG_DIM, font=("Segoe UI", 11)
            ).pack(side="left", fill="y")
        prefix_lbl = prefix_host

        tip_text = None
        if note_text:
            tip_text = note_text if len(note_text) <= 400 else note_text[:400] + "…"

        if cat and self.get_category(cat):
            color = (rt["color_cache"].get(cat) if rt else None) or self.get_category_color(cat)
        else:
            color = ROW_BG
        swatch = tk.Label(frame, text="  ", width=2, bg=color)
        swatch._todo_cat_swatch = True
        swatch.pack(side="left", padx=(8, 6), pady=6)

        parts = [task.get("text", "")]
        if cat:
            if rt and cat in rt["path_cache"]:
                parts.append(rt["path_cache"][cat])
            elif self.get_category(cat):
                parts.append(self.category_path(cat))
            else:
                parts.append(str(cat))
        if status == "blocked":
            parts.append("⏳ Waiting")
        due_text = self.format_dt(task.get("due"))
        if due_text:
            parts.append(f"Due: {due_text}")
        if task.get("type") == "recurring":
            freq = task.get("frequency", "")
            if freq == "every X days":
                freq = f"every {task.get('interval', 1)} days"
            elif freq == "weekly":
                names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                days = task.get("weekdays") or []
                labels = []
                for d in days:
                    try:
                        di = int(d)
                        if 0 <= di < 7:
                            labels.append(names[di])
                    except (TypeError, ValueError):
                        pass
                freq = ("weekly " + ",".join(labels)) if labels else "weekly"
            if freq:
                parts.append(freq)
        prereq_bits = self.prereq_labels(task)
        if prereq_bits:
            parts.append("Needs: " + ", ".join(prereq_bits))

        info = "  •  ".join(parts)
        try:
            pri_val = clamp_priority(task.get("priority", 0))
        except Exception:
            pri_val = 0
        FG_P3 = "#ef4444"  # red task name for ★★★
        if status == "blocked":
            fg = FG_DIM
        elif pri_val >= 3:
            fg = FG_P3
        else:
            fg = FG

        # Pack action buttons FIRST (side=right) so the text label cannot cover them.
        skip_btn = tk.Button(
            frame, text="⏭", width=3, bg="#f9a825", fg="#1a1a1a", relief="flat",
            activebackground="#f57f17",
            command=lambda i=tid: self.complete_or_skip(i, "skipped"),
        )
        skip_btn.pack(side="right", padx=(2, 8), pady=4)
        done_btn = tk.Button(
            frame, text="✓", width=3, bg="#2e7d32", fg="white", relief="flat",
            activebackground="#1b5e20",
            command=lambda i=tid: self.complete_or_skip(i, "completed"),
        )
        done_btn.pack(side="right", padx=2, pady=4)
        # Keep buttons above any overflowing label text
        try:
            done_btn.lift()
            skip_btn.lift()
        except Exception:
            pass

        label = tk.Label(
            frame, text=info, anchor="w", justify="left", bg=ROW_BG, fg=fg,
            font=("Segoe UI", 11),
        )
        # Packed after buttons so it only gets leftover width; long text stays under buttons
        label.pack(side="left", padx=(2, 8), pady=6, fill="x", expand=True)
        label.bind("<Button-3>", lambda e, i=tid: self.show_context_menu(e, i))
        frame._todo_priority = pri_val

        # Hover highlight on every active row
        self._bind_row_hover(frame)

        # ★ / ★★ / ★★★: drag to reorder among the same star level only
        if pri_val >= 1:
            self._bind_star_row_drag(frame, label, tid, parent, pri_val)

        # Note tooltip anywhere on the row except the action buttons
        if tip_text:
            for w in (frame, prefix_lbl, label):
                self._bind_list_tooltip(w, tip_text)
            for child in frame.winfo_children():
                if child in (skip_btn, done_btn):
                    continue
                if child in (frame, prefix_lbl, label):
                    continue
                try:
                    self._bind_list_tooltip(child, tip_text)
                except Exception:
                    pass
        return frame

    def create_history_row(self, h, parent=None, pack=True):
        ROW_BG = "#242424"
        parent = parent or self.list_frame
        frame = tk.Frame(parent, bg=ROW_BG, highlightthickness=0)
        frame._todo_row_kind = "history"
        frame._todo_row_id = h.get("id")
        frame._todo_sort_key = h.get("completed_at") or ""
        frame._todo_fp = self._row_fingerprint("history", h)
        if pack:
            frame.pack(fill="x", pady=2, padx=4)

        status_icon = "✓" if h.get("status") == "completed" else "⏭"
        color = "#81c784" if h.get("status") == "completed" else "#ffb74d"
        cat = h.get("category", "")
        rt = getattr(self, "_rt", None)
        if cat and self.get_category(cat):
            path = (rt["path_cache"].get(cat) if rt else None) or self.category_path(cat)
            cat_text = f" • {path}"
        elif cat:
            cat_text = f" • {cat}"
        else:
            cat_text = ""
        when = self.format_completed_at(h.get("completed_at"))

        text = f"{status_icon}  {h.get('text', '')}{cat_text}  •  {str(h.get('status', '')).title()} on {when}"
        if h.get("original_due"):
            due_txt = self.format_dt(h["original_due"])
            if due_txt:
                text += f"  •  Was due: {due_txt}"

        label = tk.Label(
            frame, text=text, anchor="w", justify="left", bg=ROW_BG, fg=color,
            font=("Segoe UI", 11),
        )
        label.pack(fill="x", padx=12, pady=8)

        history_id = h.get("id")
        frame.bind("<Button-3>", lambda e, hid=history_id: self.show_history_context_menu(e, hid))
        label.bind("<Button-3>", lambda e, hid=history_id: self.show_history_context_menu(e, hid))
        return frame



