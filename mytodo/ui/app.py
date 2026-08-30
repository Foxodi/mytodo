"""Desktop UI (CustomTkinter).

Depends on:
  - mytodo.domain  (pure logic)
  - mytodo.storage.TodoStore  (persistence; JsonFileStore by default)

V2: construct TodoApp(store=ApiStore(...)) for online data; keep this UI or
share domain logic with a mobile client.
"""
from __future__ import annotations

import customtkinter as ctk
from tkinter import messagebox, Menu, colorchooser, Canvas, ALL, font as tkfont
import tkinter as tk
import json
import os
import sys
import calendar
from datetime import datetime, timedelta, date, time as dtime
import uuid
from typing import Optional

from mytodo.domain import (
    DATA_VERSION,
    OVERDUE_FOCUS_COOLDOWN_SEC,
    SAVE_DEBOUNCE_MS,
    now_iso,
    safe_int,
    days_in_month,
    clamp_day,
    clamp_priority,
    as_bool,
    activity_kind,
    looks_iso,
    parse_display_timestamp,
    resolve_anchor_day,
    add_months,
    add_years,
    should_run_overdue_check,
    next_due_datetime,
    make_spawned_task,
)
from mytodo.storage import TodoStore, JsonFileStore
from mytodo.storage.migrate import default_data, migrate_data, migrate_categories_tree
from mytodo.ui.mindmap import MindMapMixin
from mytodo.ui.task_list import TaskListMixin
from mytodo.ui.dialogs import DialogsMixin

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class TodoApp(MindMapMixin, TaskListMixin, DialogsMixin, ctk.CTk):
    """Desktop shell: wires domain + store + UI mixins."""
    def __init__(self, store: Optional[TodoStore] = None):
        super().__init__()
        self.store = store or JsonFileStore(
            on_warn=lambda t, m: messagebox.showwarning(t, m)
        )

        self.title("My Todo List")
        self.minsize(720, 680)

        self.data = self.store.load()
        if self.cleanup_dead_prereqs():
            self.save_data()
        self.process_overdue_recurring()
        self._last_overdue_check = datetime.now()
        self._overdue_after = None
        if self.process_completion_rules():
            self.save_data()

        # Restore window geometry (size + position). Applied again after map to
        # defeat the classic Windows/Tk one-off offset on first layout.
        self._restore_main_geometry()

        # ========== TOP: ADD TASK (left) + COMPACT CALENDAR (right) ==========
        self.top_row = ctk.CTkFrame(self, fg_color="transparent")
        self.top_row.pack(fill="x", padx=15, pady=(12, 6))
        self.top_row.grid_columnconfigure(0, weight=1)
        self.top_row.grid_columnconfigure(1, weight=0)

        # Left: Add Task form
        self.add_frame = ctk.CTkFrame(self.top_row)
        self.add_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.entry_row = ctk.CTkFrame(self.add_frame, fg_color="transparent")
        self.entry_row.pack(fill="x", padx=10, pady=(10, 4))

        self.task_entry = ctk.CTkEntry(
            self.entry_row, placeholder_text="What do you need to do?", height=32
        )
        self.task_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.task_entry.bind("<Return>", lambda _e: self.add_task())

        self.add_button = ctk.CTkButton(
            self.entry_row, text="Add Task", width=90, height=32, command=self.add_task
        )
        self.add_button.pack(side="right")

        self.cat_frame = ctk.CTkFrame(self.add_frame, fg_color="transparent")
        self.cat_frame.pack(fill="x", padx=10, pady=2)

        ctk.CTkLabel(self.cat_frame, text="Header:").pack(side="left")
        self.add_header_var = ctk.StringVar(value="")
        self.add_header_menu = ctk.CTkOptionMenu(
            self.cat_frame,
            values=[""],
            variable=self.add_header_var,
            width=130,
            command=lambda _v: self.rebuild_add_category_menu(),
        )
        self.add_header_menu.pack(side="left", padx=(6, 10))
        self._add_header_map = {"": ""}

        ctk.CTkLabel(self.cat_frame, text="Category:").pack(side="left")
        self.category_var = ctk.StringVar(value="")
        self.category_menu = ctk.CTkOptionMenu(
            self.cat_frame, values=[""], variable=self.category_var, width=160
        )
        self.category_menu.pack(side="left", padx=(6, 0))
        self._add_category_map = {"": ""}

        self.type_frame = ctk.CTkFrame(self.add_frame, fg_color="transparent")
        self.type_frame.pack(fill="x", padx=10, pady=2)

        ctk.CTkLabel(self.type_frame, text="Frequency:").pack(side="left")
        self.freq_var = ctk.StringVar(value="Once")
        self.freq_menu = ctk.CTkOptionMenu(
            self.type_frame,
            values=["Once", "daily", "weekly", "monthly", "annually", "every X days"],
            variable=self.freq_var,
            command=self.toggle_interval,
            width=130,
        )
        self.freq_menu.pack(side="left", padx=(8, 10))

        self.interval_label = ctk.CTkLabel(self.type_frame, text="Every")
        self.interval_entry = ctk.CTkEntry(self.type_frame, width=50, placeholder_text="3")
        self.days_label = ctk.CTkLabel(self.type_frame, text="days")

        ctk.CTkLabel(self.type_frame, text="Priority:").pack(side="left", padx=(12, 4))
        self.priority_var = ctk.StringVar(value="☆")
        ctk.CTkOptionMenu(
            self.type_frame,
            values=["☆", "★", "★★", "★★★"],
            variable=self.priority_var,
            width=90,
        ).pack(side="left")
        self._priority_menu_map = {"☆": 0, "★": 1, "★★": 2, "★★★": 3}

        # Weekday pickers for Frequency=weekly (Mon=0 … Sun=6)
        self.weekday_frame = ctk.CTkFrame(self.add_frame, fg_color="transparent")
        self.weekday_vars = []
        for i, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            var = ctk.BooleanVar(value=False)
            self.weekday_vars.append(var)
            ctk.CTkCheckBox(
                self.weekday_frame, text=name, variable=var, width=52
            ).pack(side="left", padx=2)

        # Sticky vs Flexi due anchor (non-daily recurring only)
        self.due_anchor_frame = ctk.CTkFrame(self.add_frame, fg_color="transparent")
        ctk.CTkLabel(self.due_anchor_frame, text="Due date:").pack(side="left")
        self.due_anchor_var = ctk.StringVar(value="sticky")
        ctk.CTkRadioButton(
            self.due_anchor_frame, text="Sticky", variable=self.due_anchor_var,
            value="sticky", width=70,
        ).pack(side="left", padx=(8, 4))
        ctk.CTkRadioButton(
            self.due_anchor_frame, text="Flexi", variable=self.due_anchor_var,
            value="flexi", width=70,
        ).pack(side="left", padx=4)
        ctk.CTkLabel(
            self.due_anchor_frame,
            text="Sticky = from due date  ·  Flexi = from complete/skip date",
            text_color="gray",
        ).pack(side="left", padx=(10, 0))

        self.datetime_frame = ctk.CTkFrame(self.add_frame, fg_color="transparent")
        self.datetime_frame.pack(fill="x", padx=12, pady=2)

        ctk.CTkLabel(self.datetime_frame, text="Date:").pack(side="left")
        self.date_entry = ctk.CTkEntry(self.datetime_frame, width=90, placeholder_text="dd/mm/yy")
        self.date_entry.pack(side="left", padx=(6, 10))

        ctk.CTkLabel(self.datetime_frame, text="Time:").pack(side="left")
        self.time_entry = ctk.CTkEntry(self.datetime_frame, width=60, placeholder_text="hh:mm")
        self.time_entry.pack(side="left", padx=6)

        self.calendar_var = ctk.BooleanVar(value=False)
        self.calendar_check = ctk.CTkCheckBox(
            self.datetime_frame,
            text="Calendar",
            variable=self.calendar_var,
            width=80,
            state="disabled",
            command=self._on_add_activity_toggle,
        )
        self.calendar_check.pack(side="left", padx=(10, 0))
        self.date_entry.bind("<KeyRelease>", lambda _e: self._sync_calendar_checkbox())
        self.date_entry.bind("<FocusOut>", lambda _e: self._sync_calendar_checkbox())

        self.passive_var = ctk.BooleanVar(value=False)
        self.passive_check = ctk.CTkCheckBox(
            self.datetime_frame, text="Passive", variable=self.passive_var, width=80,
            command=self._on_add_activity_toggle,
        )
        self.passive_check.pack(side="left", padx=(12, 0))

        # Prerequisites (collapsible)
        self.prereq_header = ctk.CTkFrame(self.add_frame, fg_color="transparent")
        self.prereq_header.pack(fill="x", padx=12, pady=(4, 0))
        self.prereq_open = False
        self.prereq_toggle_btn = ctk.CTkButton(
            self.prereq_header,
            text="Prerequisites ▸",
            width=140,
            height=28,
            fg_color="transparent",
            border_width=1,
            command=self.toggle_prereq_panel,
        )
        self.prereq_toggle_btn.pack(side="left")
        self.prereq_summary = ctk.CTkLabel(
            self.prereq_header, text="None selected", text_color="gray", anchor="w"
        )
        self.prereq_summary.pack(side="left", padx=10)

        self.prereq_panel = ctk.CTkFrame(self.add_frame)
        self.prereq_vars = {}  # task_id -> BooleanVar

        # Right: compact calendar
        self.calendar_frame = ctk.CTkFrame(self.top_row)
        self.calendar_frame.grid(row=0, column=1, sticky="ne")
        self._calendar_cells = []
        self._calendar_tooltip = None
        self.build_calendar_strip()

        # ========== TOOLS + FILTER BARS ==========
        filters = self.data.get("filters") or {}

        self.tools_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.tools_frame.pack(fill="x", padx=15, pady=(0, 2))
        ctk.CTkButton(
            self.tools_frame, text="Completion Rules", width=140, height=28,
            command=self.open_completion_rules_manager,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            self.tools_frame, text="Mind Map", width=100, height=28,
            command=self.open_mind_map,
        ).pack(side="left", padx=(0, 6))
        self.split_btn = ctk.CTkButton(
            self.tools_frame, text="Split View", width=100, height=28,
            command=self.toggle_split_view,
        )
        self.split_btn.pack(side="left")
        self.split_enabled = bool((self.data.get("filters") or {}).get("split_view", False))

        # Right side of tools row (under calendar): Materials notepad
        ctk.CTkButton(
            self.tools_frame, text="Materials", width=100, height=28,
            command=self.open_materials_window,
        ).pack(side="right")

        # ========== TASK LISTS HOST (pane A always present; pane B on split) ==========
        self.lists_host = ctk.CTkFrame(self, fg_color="transparent")
        self.lists_host.pack(fill="both", expand=True, padx=15, pady=(5, 12))

        # Left column — primary filters + list (never destroyed / reparented)
        self.split_pane_a = ctk.CTkFrame(self.lists_host, fg_color="transparent")
        self.split_pane_a.pack(side="left", fill="both", expand=True)

        # Priority filter row
        self.priority_filter_frame = ctk.CTkFrame(self.split_pane_a)
        self.priority_filter_frame.pack(fill="x", pady=(0, 2))
        self.show_priority_0 = ctk.BooleanVar(value=filters.get("show_priority_0", True))
        self.show_priority_1 = ctk.BooleanVar(value=filters.get("show_priority_1", True))
        self.show_priority_2 = ctk.BooleanVar(value=filters.get("show_priority_2", True))
        self.show_priority_3 = ctk.BooleanVar(value=filters.get("show_priority_3", True))
        kinds = self._kind_filter_defaults(filters)
        self.show_passive = ctk.BooleanVar(value=kinds["show_passive"])
        self.show_active = ctk.BooleanVar(value=kinds["show_active"])
        self.show_one_off = ctk.BooleanVar(value=kinds["show_one_off"])
        self.show_recurring = ctk.BooleanVar(value=kinds["show_recurring"])
        self.show_calendar_kind = ctk.BooleanVar(value=kinds["show_calendar_kind"])
        ctk.CTkCheckBox(
            self.priority_filter_frame, text="☆", variable=self.show_priority_0,
            command=self.on_filters_changed, width=55,
        ).pack(side="left", padx=(8, 4))
        ctk.CTkCheckBox(
            self.priority_filter_frame, text="★", variable=self.show_priority_1,
            command=self.on_filters_changed, width=55,
        ).pack(side="left", padx=4)
        ctk.CTkCheckBox(
            self.priority_filter_frame, text="★★", variable=self.show_priority_2,
            command=self.on_filters_changed, width=55,
        ).pack(side="left", padx=4)
        ctk.CTkCheckBox(
            self.priority_filter_frame, text="★★★", variable=self.show_priority_3,
            command=self.on_filters_changed, width=60,
        ).pack(side="left", padx=4)

        # Status checkboxes
        self.status_filter_frame = ctk.CTkFrame(self.split_pane_a)
        self.status_filter_frame.pack(fill="x", pady=(0, 2))

        self.show_due = ctk.BooleanVar(value=filters.get("show_due", True))
        self.show_not_due = ctk.BooleanVar(value=filters.get("show_not_due", True))
        self.show_completed = ctk.BooleanVar(value=filters.get("show_completed", False))
        self.show_skipped = ctk.BooleanVar(value=filters.get("show_skipped", False))

        ctk.CTkCheckBox(
            self.status_filter_frame, text="Due", variable=self.show_due,
            command=self.on_filters_changed, width=60,
        ).pack(side="left", padx=(8, 2))
        ctk.CTkCheckBox(
            self.status_filter_frame, text="Not Due", variable=self.show_not_due,
            command=self.on_filters_changed, width=80,
        ).pack(side="left", padx=2)
        ctk.CTkCheckBox(
            self.status_filter_frame, text="Completed", variable=self.show_completed,
            command=self.on_filters_changed, width=90,
        ).pack(side="left", padx=2)
        ctk.CTkCheckBox(
            self.status_filter_frame, text="Skipped", variable=self.show_skipped,
            command=self.on_filters_changed, width=80,
        ).pack(side="left", padx=2)

        # Kind checkboxes (Passive / Active / One-Off / Recurring / Calendar)
        self.kind_filter_frame = ctk.CTkFrame(self.split_pane_a)
        self.kind_filter_frame.pack(fill="x", pady=(0, 2))
        for text, var, w in (
            ("Passive", self.show_passive, 80),
            ("Active", self.show_active, 70),
            ("Calendar", self.show_calendar_kind, 90),
            ("One-Off", self.show_one_off, 80),
            ("Recurring", self.show_recurring, 90),
        ):
            ctk.CTkCheckBox(
                self.kind_filter_frame, text=text, variable=var,
                command=self.on_filters_changed, width=w,
            ).pack(side="left", padx=(8 if text == "Passive" else 2, 2))

        # Dropdown filters
        self.filter_frame = ctk.CTkFrame(self.split_pane_a)
        self.filter_frame.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(self.filter_frame, text="Header:").pack(side="left", padx=(8, 4))
        self.filter_header_var = ctk.StringVar(value=filters.get("header", "All"))
        self.filter_header_menu = ctk.CTkOptionMenu(
            self.filter_frame,
            values=["All"],
            variable=self.filter_header_var,
            command=self.on_header_filter_changed,
            width=120,
        )
        self.filter_header_menu.pack(side="left", padx=(0, 10))

        ctk.CTkLabel(self.filter_frame, text="Sub:").pack(side="left", padx=(0, 4))
        self.filter_sub_var = ctk.StringVar(value=filters.get("sub", "All"))
        self.filter_sub_menu = ctk.CTkOptionMenu(
            self.filter_frame,
            values=["All"],
            variable=self.filter_sub_var,
            command=lambda _v: self.on_filters_changed(),
            width=160,
        )
        self.filter_sub_menu.pack(side="left", padx=(0, 15))
        self._filter_header_map = {"All": None}
        self._filter_sub_map = {"All": None}
        self._category_picker_map = {"": ""}

        ctk.CTkLabel(self.filter_frame, text="Search:").pack(side="left", padx=(4, 4))
        self.list_search_var = ctk.StringVar(value=filters.get("search", "") or "")
        self.list_search_entry = ctk.CTkEntry(
            self.filter_frame,
            textvariable=self.list_search_var,
            placeholder_text="Filter tasks…",
            width=140,
            height=28,
        )
        self.list_search_entry.pack(side="left", padx=(0, 4), fill="x", expand=True)
        self.list_search_entry.bind("<KeyRelease>", lambda _e: self.on_filters_changed())
        ctk.CTkButton(
            self.filter_frame,
            text="✕",
            width=28,
            height=28,
            command=lambda: (self.list_search_var.set(""), self.on_filters_changed()),
        ).pack(side="left", padx=(0, 8))

        self.list_frame = ctk.CTkScrollableFrame(self.split_pane_a, label_text="Tasks")
        self.list_frame.pack(fill="both", expand=True)
        self.list_frame_b = None
        self.split_pane_b = None
        self._init_secondary_filter_vars()

        self.toggle_interval()
        self.update_all_category_menus()
        if self.split_enabled:
            self._enter_split_layout()
            self.split_btn.configure(text="Single View")
        # First paint must fully build rows (incremental sync needs a populated registry)
        self.refresh_list(force_full=True)
        self.schedule_midnight_refresh()
        # Catch timed dues that already passed while the app was closed
        self.after(200, lambda: self._run_overdue_check(force=True))

        # Re-check overdue after sleep/hibernate, but NOT on every child-widget FocusIn.
        # Tk bind tags mean Toplevel bindings also fire for children; debounce + cooldown.
        self.bind("<FocusIn>", self.on_focus)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # Light periodic check every 15 minutes as a safety net
        self.after(15 * 60 * 1000, self.periodic_check)

    # ========== LIFECYCLE ==========
    def on_close(self):
        try:
            self._hide_calendar_tooltip()
        except Exception:
            pass
        self.save_window_and_filters()
        # Ensure any debounced write is on disk before the window dies
        self.save_data(immediate=True)
        self.destroy()

    def on_focus(self, _event=None):
        if getattr(self, "_overdue_after", None):
            try:
                self.after_cancel(self._overdue_after)
            except Exception:
                pass
        self._overdue_after = self.after(300, self._run_overdue_check)

    def _run_overdue_check(self, force=False):
        self._overdue_after = None
        now = datetime.now()
        last = getattr(self, "_last_overdue_check", None)
        if not force and not should_run_overdue_check(last, now):
            return
        self._last_overdue_check = now
        changed = self.process_overdue_recurring()
        if self.promote_timed_due_tasks():
            changed = True
        if changed:
            self.refresh_list()

    def periodic_check(self):
        self._run_overdue_check(force=True)
        self.after(15 * 60 * 1000, self.periodic_check)

    def schedule_midnight_refresh(self):
        now = datetime.now()
        tomorrow = now.date() + timedelta(days=1)
        midnight = datetime.combine(tomorrow, dtime.min)
        ms = max(int((midnight - now).total_seconds() * 1000), 1000)
        # Tk after() is happiest with moderate delays; clamp to ~12h chunks if needed
        if ms > 12 * 60 * 60 * 1000:
            self.after(12 * 60 * 60 * 1000, self.schedule_midnight_refresh)
        else:
            self.after(ms, self.midnight_refresh)

    def midnight_refresh(self):
        self._last_overdue_check = datetime.now()
        self.process_overdue_recurring()
        self.promote_timed_due_tasks()
        self.refresh_list()
        self.schedule_midnight_refresh()

    def _kind_filter_defaults(self, filters: dict) -> dict:
        """Map saved filters (including old passive_mode / type dropdowns) to checkboxes."""
        filters = filters or {}
        if "show_active" in filters or "show_one_off" in filters:
            return {
                "show_passive": bool(filters.get("show_passive", True)),
                "show_active": bool(filters.get("show_active", True)),
                "show_one_off": bool(filters.get("show_one_off", True)),
                "show_recurring": bool(filters.get("show_recurring", True)),
                "show_calendar_kind": bool(filters.get("show_calendar_kind", True)),
            }
        pm = filters.get("passive_mode")
        if pm not in ("all", "exclude", "only"):
            pm = "exclude" if filters.get("show_passive") is False else "all"
        typ = filters.get("type", "All")
        if typ == "One-off":
            one, rec, cal = True, False, True
        elif typ == "Recurring":
            one, rec, cal = False, True, True
        else:
            # "All" or old exclusive "Calendar" type — calendar is independent now
            one, rec, cal = True, True, True
        return {
            "show_passive": pm in ("all", "only"),
            "show_active": pm in ("all", "exclude"),
            "show_one_off": one,
            "show_recurring": rec,
            "show_calendar_kind": cal,
        }

    def _init_secondary_filter_vars(self):
        fb = self.data.get("filters_b") or {}
        self.show_priority_0_b = ctk.BooleanVar(value=fb.get("show_priority_0", True))
        self.show_priority_1_b = ctk.BooleanVar(value=fb.get("show_priority_1", True))
        self.show_priority_2_b = ctk.BooleanVar(value=fb.get("show_priority_2", True))
        self.show_priority_3_b = ctk.BooleanVar(value=fb.get("show_priority_3", True))
        kinds_b = self._kind_filter_defaults(fb)
        self.show_passive_b = ctk.BooleanVar(value=kinds_b["show_passive"])
        self.show_active_b = ctk.BooleanVar(value=kinds_b["show_active"])
        self.show_one_off_b = ctk.BooleanVar(value=kinds_b["show_one_off"])
        self.show_recurring_b = ctk.BooleanVar(value=kinds_b["show_recurring"])
        self.show_calendar_kind_b = ctk.BooleanVar(value=kinds_b["show_calendar_kind"])
        self.show_due_b = ctk.BooleanVar(value=fb.get("show_due", True))
        self.show_not_due_b = ctk.BooleanVar(value=fb.get("show_not_due", True))
        self.show_completed_b = ctk.BooleanVar(value=fb.get("show_completed", False))
        self.show_skipped_b = ctk.BooleanVar(value=fb.get("show_skipped", False))
        self.filter_header_var_b = ctk.StringVar(value=fb.get("header", "All"))
        self.filter_sub_var_b = ctk.StringVar(value=fb.get("sub", "All"))
        self._filter_header_map_b = {"All": None}
        self._filter_sub_map_b = {"All": None}

    def toggle_split_view(self):
        self.split_enabled = not self.split_enabled
        if self.split_enabled:
            self._enter_split_layout()
            self.split_btn.configure(text="Single View")
        else:
            self._exit_split_layout()
            self.split_btn.configure(text="Split View")
        self.data.setdefault("filters", {})["split_view"] = self.split_enabled
        self.save_data()
        self.refresh_list(refresh_cal=False)

    def _exit_split_layout(self):
        """Hide/destroy the right pane only — left pane stays put."""
        if self.split_pane_b is not None:
            try:
                self.split_pane_b.destroy()
            except Exception:
                pass
        self.split_pane_b = None
        self.list_frame_b = None
        self.filter_header_menu_b = None
        self.filter_sub_menu_b = None
        # Left pane already fills lists_host; nothing else to restore

    def _enter_split_layout(self):
        """Add a second column beside the existing left pane (50/50)."""
        if self.split_pane_b is not None:
            try:
                self.split_pane_b.destroy()
            except Exception:
                pass

        pane = ctk.CTkFrame(self.lists_host, fg_color="transparent")
        pane.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.split_pane_b = pane

        pri = ctk.CTkFrame(pane)
        pri.pack(fill="x", pady=(0, 2))
        for text, var, w in (
            ("☆", self.show_priority_0_b, 50),
            ("★", self.show_priority_1_b, 50),
            ("★★", self.show_priority_2_b, 50),
            ("★★★", self.show_priority_3_b, 55),
        ):
            ctk.CTkCheckBox(
                pri, text=text, variable=var, command=self.on_filters_changed_b, width=w
            ).pack(side="left", padx=2)

        st = ctk.CTkFrame(pane)
        st.pack(fill="x", pady=(0, 2))
        for text, var, w in (
            ("Due", self.show_due_b, 55),
            ("Not Due", self.show_not_due_b, 75),
            ("Completed", self.show_completed_b, 90),
            ("Skipped", self.show_skipped_b, 80),
        ):
            ctk.CTkCheckBox(
                st, text=text, variable=var, command=self.on_filters_changed_b, width=w
            ).pack(side="left", padx=2)

        kind_b = ctk.CTkFrame(pane)
        kind_b.pack(fill="x", pady=(0, 2))
        for text, var, w in (
            ("Passive", self.show_passive_b, 70),
            ("Active", self.show_active_b, 62),
            ("Calendar", self.show_calendar_kind_b, 80),
            ("One-Off", self.show_one_off_b, 72),
            ("Recurring", self.show_recurring_b, 80),
        ):
            ctk.CTkCheckBox(
                kind_b, text=text, variable=var, command=self.on_filters_changed_b, width=w
            ).pack(side="left", padx=2)

        ft = ctk.CTkFrame(pane)
        ft.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(ft, text="Header:").pack(side="left", padx=(4, 2))
        self.filter_header_menu_b = ctk.CTkOptionMenu(
            ft, values=["All"], variable=self.filter_header_var_b,
            command=self.on_header_filter_changed_b, width=100,
        )
        self.filter_header_menu_b.pack(side="left", padx=2)
        ctk.CTkLabel(ft, text="Sub:").pack(side="left", padx=(4, 2))
        self.filter_sub_menu_b = ctk.CTkOptionMenu(
            ft, values=["All"], variable=self.filter_sub_var_b,
            command=lambda _v: self.on_filters_changed_b(), width=110,
        )
        self.filter_sub_menu_b.pack(side="left", padx=2)

        ctk.CTkLabel(ft, text="Search:").pack(side="left", padx=(6, 2))
        self.list_search_var_b = ctk.StringVar(value=(self.data.get("filters_b") or {}).get("search", "") or "")
        self.list_search_entry_b = ctk.CTkEntry(
            ft,
            textvariable=self.list_search_var_b,
            placeholder_text="Filter…",
            width=110,
            height=28,
        )
        self.list_search_entry_b.pack(side="left", padx=2, fill="x", expand=True)
        self.list_search_entry_b.bind("<KeyRelease>", lambda _e: self.on_filters_changed_b())
        ctk.CTkButton(
            ft, text="✕", width=28, height=28,
            command=lambda: (self.list_search_var_b.set(""), self.on_filters_changed_b()),
        ).pack(side="left", padx=2)

        self.list_frame_b = ctk.CTkScrollableFrame(pane, label_text="Tasks")
        self.list_frame_b.pack(fill="both", expand=True)
        self._sync_secondary_category_menus()

    def on_filters_changed_b(self, _value=None):
        if getattr(self, "_filter_refresh_after_b", None):
            try:
                self.after_cancel(self._filter_refresh_after_b)
            except Exception:
                pass
        self._filter_refresh_after_b = self.after(50, lambda: self.refresh_list(refresh_cal=False))

    def on_header_filter_changed_b(self, _value=None):
        self._rebuild_sub_filter_menu_b()
        self.on_filters_changed_b()

    def _sync_secondary_category_menus(self):
        if not getattr(self, "filter_header_menu_b", None):
            return
        labels = list(self._filter_header_map.keys()) if self._filter_header_map else ["All"]
        self._filter_header_map_b = dict(self._filter_header_map)
        self.filter_header_menu_b.configure(values=labels)
        if self.filter_header_var_b.get() not in labels:
            self.filter_header_var_b.set("All")
        self._rebuild_sub_filter_menu_b()

    def _rebuild_sub_filter_menu_b(self):
        if not getattr(self, "filter_sub_menu_b", None):
            return
        header_id = self._filter_header_map_b.get(self.filter_header_var_b.get())
        self._filter_sub_map_b = {"All": None}
        sub_labels = ["All"]
        if header_id is None:
            nodes = [(cid, depth) for cid, depth in self.iter_category_tree(None) if depth >= 1]
        else:
            nodes = list(self.iter_category_tree(header_id, 1))
        for cid, depth in nodes:
            path = self.category_path(cid)
            label = path
            base = label
            n = 2
            while label in self._filter_sub_map_b:
                label = f"{base} ({n})"
                n += 1
            self._filter_sub_map_b[label] = cid
            sub_labels.append(label)
        cur = self.filter_sub_var_b.get()
        self.filter_sub_menu_b.configure(values=sub_labels)
        if cur not in sub_labels:
            self.filter_sub_var_b.set("All")

    def on_filters_changed(self):
        # Don't write disk on every tick — just refresh the list (fast path).
        # Filters are persisted on window close / geometry save.
        if getattr(self, "_filter_refresh_after", None):
            try:
                self.after_cancel(self._filter_refresh_after)
            except Exception:
                pass
        # Debounce ~50ms so multi-toggle doesn't stack full redraws
        self._filter_refresh_after = self.after(50, lambda: self.refresh_list(refresh_cal=False))

    def _is_window_zoomed(self, win=None) -> bool:
        win = win or self
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

    def _set_window_zoomed(self, zoomed: bool):
        try:
            self.state("zoomed" if zoomed else "normal")
            return
        except Exception:
            pass
        try:
            self.attributes("-zoomed", bool(zoomed))
        except Exception:
            pass

    def _track_normal_geometry(self, _event=None):
        """Keep last non-maximized geometry so we can restore size after a maximized session."""
        if getattr(self, "_geo_track_lock", False):
            return
        if self._is_window_zoomed():
            return
        try:
            self.update_idletasks()
            g = self.geometry()
            if g and "x" in g:
                self._last_normal_geometry = g
        except Exception:
            pass

    def _restore_main_geometry(self):
        geo = (self.data.get("window_geometry") or "780x820").strip() or "780x820"
        want_max = bool(self.data.get("window_maximized"))
        self._last_normal_geometry = geo
        self._geo_track_lock = True
        try:
            self.geometry(geo)
        except Exception:
            try:
                self.geometry("780x820")
            except Exception:
                pass

        def _reapply():
            g = (self.data.get("window_geometry") or geo).strip() or geo
            try:
                self.geometry(g)
            except Exception:
                pass
            if want_max:
                try:
                    self._set_window_zoomed(True)
                except Exception:
                    pass

        def _unlock():
            self._geo_track_lock = False
            self._track_normal_geometry()

        # Re-apply after map/styling; maximize after geometry settles
        self.after(1, _reapply)
        self.after(80, _reapply)
        self.after(120, _unlock)
        # Continuously remember normal (non-max) size/pos while the app runs
        self.bind("<Configure>", self._track_normal_geometry, add="+")

    def save_window_and_filters(self):
        try:
            self.update_idletasks()
            zoomed = self._is_window_zoomed()
            self.data["window_maximized"] = bool(zoomed)
            if zoomed:
                # Keep last known normal geometry so next restore has a real size to un-max into
                g = getattr(self, "_last_normal_geometry", None) or self.data.get("window_geometry")
                if g:
                    self.data["window_geometry"] = g
            else:
                self.data["window_geometry"] = self.geometry()
                self._last_normal_geometry = self.data["window_geometry"]
        except Exception:
            pass
        self.data["filters"] = {
            "header": self.filter_header_var.get(),
            "sub": self.filter_sub_var.get(),
            "show_priority_0": bool(self.show_priority_0.get()),
            "show_priority_1": bool(self.show_priority_1.get()),
            "show_priority_2": bool(self.show_priority_2.get()),
            "show_priority_3": bool(self.show_priority_3.get()),
            "show_passive": bool(self.show_passive.get()),
            "show_active": bool(self.show_active.get()),
            "show_one_off": bool(self.show_one_off.get()),
            "show_recurring": bool(self.show_recurring.get()),
            "show_calendar_kind": bool(self.show_calendar_kind.get()),
            "show_due": bool(self.show_due.get()),
            "show_not_due": bool(self.show_not_due.get()),
            "show_completed": bool(self.show_completed.get()),
            "show_skipped": bool(self.show_skipped.get()),
            "split_view": bool(getattr(self, "split_enabled", False)),
            "search": (self.list_search_var.get() if getattr(self, "list_search_var", None) else "") or "",
        }
        if getattr(self, "show_priority_0_b", None) is not None:
            self.data["filters_b"] = {
                "header": self.filter_header_var_b.get(),
                "sub": self.filter_sub_var_b.get(),
                "show_priority_0": bool(self.show_priority_0_b.get()),
                "show_priority_1": bool(self.show_priority_1_b.get()),
                "show_priority_2": bool(self.show_priority_2_b.get()),
                "show_priority_3": bool(self.show_priority_3_b.get()),
                "show_passive": bool(self.show_passive_b.get()),
                "show_active": bool(self.show_active_b.get()),
                "show_one_off": bool(self.show_one_off_b.get()),
                "show_recurring": bool(self.show_recurring_b.get()),
                "show_calendar_kind": bool(self.show_calendar_kind_b.get()),
                "show_due": bool(self.show_due_b.get()),
                "show_not_due": bool(self.show_not_due_b.get()),
                "show_completed": bool(self.show_completed_b.get()),
                "show_skipped": bool(self.show_skipped_b.get()),
                "search": (self.list_search_var_b.get() if getattr(self, "list_search_var_b", None) else "") or "",
            }
        self.save_data()

    # ========== DATA ==========
    def default_data(self):
        return default_data()


    def load_data(self):
        return self.store.load()


    def migrate_data(self, data):
        return migrate_data(data)


    @staticmethod
    def _looks_iso(value: str) -> bool:
        return looks_iso(value)


    @staticmethod
    def _parse_display_timestamp(value: str):
        return parse_display_timestamp(value)


    def save_data(self, immediate: bool = False):
        """
        Persist self.data via the injected TodoStore.

        Debounces (~SAVE_DEBOUNCE_MS) so rapid bursts only hit the backend once.
        Pass immediate=True to flush now (app exit).
        """
        self.data["version"] = DATA_VERSION
        self._save_dirty = True
        if immediate:
            if getattr(self, "_save_after", None):
                try:
                    self.after_cancel(self._save_after)
                except Exception:
                    pass
                self._save_after = None
            self._flush_save()
            return
        if getattr(self, "_save_after", None):
            try:
                self.after_cancel(self._save_after)
            except Exception:
                pass
        try:
            self._save_after = self.after(SAVE_DEBOUNCE_MS, self._flush_save)
        except Exception:
            self._flush_save()


    def _flush_save(self):
        self._save_after = None
        if not getattr(self, "_save_dirty", False):
            return
        self._save_dirty = False
        self.data["version"] = DATA_VERSION
        try:
            self.store.save(self.data)
        except OSError as e:
            self._save_dirty = True
            messagebox.showerror("Save failed", str(e))


    def _migrate_categories_tree(self, categories, data):
        return migrate_categories_tree(categories, data)


    def get_category(self, cat_id: str):
        if not cat_id:
            return None
        return (self.data.get("categories") or {}).get(cat_id)

    def category_name(self, cat_id: str) -> str:
        meta = self.get_category(cat_id)
        return (meta or {}).get("name") or ""

    def category_parent_id(self, cat_id: str):
        meta = self.get_category(cat_id)
        return (meta or {}).get("parent_id")

    def is_header_category(self, cat_id: str) -> bool:
        """Headers are coloured root nodes (created via Create Header)."""
        meta = self.get_category(cat_id)
        if not meta:
            return False
        return meta.get("parent_id") is None and bool(meta.get("color"))

    def category_root_id(self, cat_id: str):
        if not cat_id or not self.get_category(cat_id):
            return None
        seen = set()
        cur = cat_id
        while cur and cur not in seen:
            seen.add(cur)
            parent = self.category_parent_id(cur)
            if parent is None:
                return cur
            cur = parent
        return cat_id

    def _ancestors_and_self(self, cat_id: str) -> set:
        out = set()
        cur = cat_id
        while cur and cur not in out:
            out.add(cur)
            cur = self.category_parent_id(cur)
        return out

    def would_create_cycle(self, cat_id: str, new_parent_id) -> bool:
        if new_parent_id is None:
            return False
        if new_parent_id == cat_id:
            return True
        return cat_id in self._ancestors_and_self(new_parent_id)

    def category_path(self, cat_id: str, sep: str = " › ") -> str:
        if not cat_id:
            return ""
        parts = []
        seen = set()
        cur = cat_id
        while cur and cur not in seen:
            seen.add(cur)
            meta = self.get_category(cur)
            if not meta:
                break
            parts.append(meta.get("name") or "?")
            cur = meta.get("parent_id")
        parts.reverse()
        return sep.join(parts)

    def category_children(self, parent_id) -> list:
        """Return child category ids under parent_id (None = roots), sorted by sort_order then name."""
        kids = []
        for cid, meta in (self.data.get("categories") or {}).items():
            if not isinstance(meta, dict):
                continue
            if meta.get("parent_id") == parent_id:
                kids.append(cid)
        kids.sort(
            key=lambda c: (
                (self.get_category(c) or {}).get("sort_order", 0),
                (self.category_name(c) or "").lower(),
            )
        )
        return kids

    def resequence_siblings(self, parent_id):
        """Normalize sort_order to 0..n-1 under a parent."""
        for i, cid in enumerate(self.category_children(parent_id)):
            meta = self.get_category(cid)
            if meta is not None:
                meta["sort_order"] = i

    def set_category_parent(self, cat_id: str, new_parent_id, before_id=None):
        """
        Reparent cat_id. Optionally place it before before_id among the new siblings.
        Active tasks keep their category id (hierarchy is structural only).
        """
        meta = self.get_category(cat_id)
        if not meta:
            return False
        if self.would_create_cycle(cat_id, new_parent_id):
            return False

        old_parent = meta.get("parent_id")
        meta["parent_id"] = new_parent_id

        # Headers must stay at root; if dragged under something, strip header colour
        if new_parent_id is not None and meta.get("color"):
            meta["color"] = None

        # Place among siblings
        siblings = [c for c in self.category_children(new_parent_id) if c != cat_id]
        if before_id and before_id in siblings:
            idx = siblings.index(before_id)
            siblings.insert(idx, cat_id)
        else:
            siblings.append(cat_id)
        for i, cid in enumerate(siblings):
            m = self.get_category(cid)
            if m is not None:
                m["sort_order"] = i

        if old_parent != new_parent_id:
            self.resequence_siblings(old_parent)
        return True

    def category_descendants(self, cat_id: str) -> list:
        """All descendant ids (not including cat_id itself)."""
        out = []
        stack = list(self.category_children(cat_id))
        while stack:
            cid = stack.pop()
            out.append(cid)
            stack.extend(self.category_children(cid))
        return out

    def iter_category_tree(self, parent_id=None, depth=0):
        """Yield (cat_id, depth) in depth-first order."""
        for cid in self.category_children(parent_id):
            yield cid, depth
            yield from self.iter_category_tree(cid, depth + 1)

    def get_category_color(self, cat_id: str):
        """Colour comes from the header (root) category only."""
        if not cat_id:
            return "#9ca3af"
        root = self.category_root_id(cat_id)
        meta = self.get_category(root) if root else None
        if meta and meta.get("color"):
            return meta["color"]
        return "#6b7280"

    def get_category_list(self):
        """Labels for add-task picker: blank + full paths for every node."""
        labels = [""]
        self._category_picker_map = {"": ""}
        for cid, depth in self.iter_category_tree(None, 0):
            path = self.category_path(cid)
            # Indent leaf-ish display slightly by depth for readability
            label = ("  " * depth) + path if depth else path
            # Ensure unique labels
            base = label
            n = 2
            while label in self._category_picker_map:
                label = f"{base} ({n})"
                n += 1
            self._category_picker_map[label] = cid
            labels.append(label)
        return labels

    def category_id_from_picker_label(self, label: str) -> str:
        return self._category_picker_map.get(label, "")

    def picker_label_for_category_id(self, cat_id: str) -> str:
        if not cat_id:
            return ""
        for label, cid in self._category_picker_map.items():
            if cid == cat_id:
                return label
        # Rebuild map if needed
        self.get_category_list()
        for label, cid in self._category_picker_map.items():
            if cid == cat_id:
                return label
        return self.category_path(cat_id)

    def build_header_picker_options(self):
        """Labels + map for Header dropdown (coloured roots only)."""
        mapping = {"": ""}
        labels = [""]
        for cid in self.category_children(None):
            if not self.is_header_category(cid):
                continue
            name = self.category_name(cid) or cid
            label = name
            base = label
            n = 2
            while label in mapping:
                label = f"{base} ({n})"
                n += 1
            mapping[label] = cid
            labels.append(label)
        return labels, mapping

    def build_category_picker_options(self, header_id):
        """
        Category options depend on Header selection:
        - Header chosen → that header + all its descendants
        - No Header → categories not under any header (root non-headers + their trees)
        """
        mapping = {"": ""}
        labels = [""]

        def add_label(cid):
            path = self.category_path(cid)
            if header_id and path:
                hname = self.category_name(header_id) or ""
                if path.startswith(hname + " › "):
                    path = path[len(hname) + 3 :]
                elif path == hname:
                    path = hname
            label = path or (self.category_name(cid) or cid)
            base = label
            n = 2
            while label in mapping:
                label = f"{base} ({n})"
                n += 1
            mapping[label] = cid
            labels.append(label)

        if header_id:
            add_label(header_id)
            for cid, _depth in self.iter_category_tree(header_id, 1):
                add_label(cid)
        else:
            for cid in self.category_children(None):
                if self.is_header_category(cid):
                    continue
                add_label(cid)
                for kid, _depth in self.iter_category_tree(cid, 1):
                    add_label(kid)
        return labels, mapping

    def rebuild_add_header_menu(self):
        labels, mapping = self.build_header_picker_options()
        self._add_header_map = mapping
        cur = self.add_header_var.get()
        self.add_header_menu.configure(values=labels)
        if cur not in labels:
            self.add_header_var.set("")

    def rebuild_add_category_menu(self):
        header_id = self._add_header_map.get(self.add_header_var.get(), "") or ""
        labels, mapping = self.build_category_picker_options(header_id)
        self._add_category_map = mapping
        cur = self.category_var.get()
        self.category_menu.configure(values=labels)
        if cur not in labels:
            self.category_var.set("")

    def resolve_header_category_pickers(self, header_label, category_label, header_map, category_map) -> str:
        """Resolve Header + Category labels to a single category id."""
        if category_label and category_label in category_map:
            return category_map.get(category_label) or ""
        if header_label and header_label in header_map:
            return header_map.get(header_label) or ""
        return ""

    def get_add_form_category_id(self) -> str:
        return self.resolve_header_category_pickers(
            self.add_header_var.get(),
            self.category_var.get(),
            self._add_header_map,
            self._add_category_map,
        )

    def header_and_category_labels_for_id(self, cat_id: str):
        """Return (header_label, category_label) suitable for the dual pickers."""
        if not cat_id or not self.get_category(cat_id):
            return "", ""
        root = self.category_root_id(cat_id)
        header_labels, header_map = self.build_header_picker_options()
        header_label = ""
        header_id = ""
        if root and self.is_header_category(root):
            header_id = root
            for lab, cid in header_map.items():
                if cid == root:
                    header_label = lab
                    break
        cat_labels, cat_map = self.build_category_picker_options(header_id)
        category_label = ""
        for lab, cid in cat_map.items():
            if cid == cat_id:
                category_label = lab
                break
        return header_label, category_label

    def on_header_filter_changed(self, _value=None):
        self.rebuild_sub_filter_menu()
        self.on_filters_changed()

    def rebuild_sub_filter_menu(self):
        header_label = self.filter_header_var.get()
        header_id = self._filter_header_map.get(header_label)

        self._filter_sub_map = {"All": None}
        sub_labels = ["All"]

        if header_id is None:
            # All headers: every non-root category with full path
            nodes = [(cid, depth) for cid, depth in self.iter_category_tree(None) if depth >= 1]
        else:
            nodes = list(self.iter_category_tree(header_id, 1))

        for cid, depth in nodes:
            path = self.category_path(cid)
            label = path
            base = label
            n = 2
            while label in self._filter_sub_map:
                label = f"{base} ({n})"
                n += 1
            self._filter_sub_map[label] = cid
            sub_labels.append(label)

        current = self.filter_sub_var.get()
        self.filter_sub_menu.configure(values=sub_labels)
        if current not in sub_labels:
            self.filter_sub_var.set("All")

    def update_all_category_menus(self):
        # Header filter (coloured roots only)
        self._filter_header_map = {"All": None}
        header_labels = ["All"]
        for cid in self.category_children(None):
            if not self.is_header_category(cid):
                continue
            name = self.category_name(cid) or cid
            label = name
            base = label
            n = 2
            while label in self._filter_header_map:
                label = f"{base} ({n})"
                n += 1
            self._filter_header_map[label] = cid
            header_labels.append(label)

        cur_h = self.filter_header_var.get()
        self.filter_header_menu.configure(values=header_labels)
        if cur_h not in header_labels:
            self.filter_header_var.set("All")

        self.rebuild_sub_filter_menu()
        self._sync_secondary_category_menus()

        # Add-task Header + Category pickers
        self.rebuild_add_header_menu()
        self.rebuild_add_category_menu()

    # ========== OVERDUE RECURRING ==========
    def process_overdue_recurring(self) -> bool:
        """
        Auto-skip past-due *daily* recurring tasks and roll them forward.
        Weekly / monthly / annually / every-X-days are left overdue until the user
        completes or skips them manually (no silent roll-forward).
        Returns True if data changed.
        """
        today = date.today()
        changed = False
        new_active = []

        for task in list(self.data["active"]):
            if task.get("type") != "recurring" or not task.get("due"):
                new_active.append(task)
                continue

            # Only daily series auto-roll; longer periods wait for manual complete/skip
            if (task.get("frequency") or "daily") != "daily":
                new_active.append(task)
                continue

            # Don't auto-skip while still waiting on prerequisites
            if self.is_blocked(task):
                new_active.append(task)
                continue

            try:
                due_date = datetime.fromisoformat(task["due"]).date()
            except Exception:
                new_active.append(task)
                continue

            if due_date >= today:
                new_active.append(task)
                continue

            current_task = task
            # Guard against infinite loops on bad frequency data
            for _ in range(3660):  # ~10 years of daily skips max
                series_id = current_task.get("series_id") or current_task["id"]
                self.data["history"].append(
                    {
                        "id": current_task["id"],
                        "series_id": series_id,
                        "text": current_task["text"],
                        "type": "recurring",
                        "category": current_task.get("category", ""),
                        "status": "skipped",
                        "original_due": current_task.get("due"),
                        "completed_at": now_iso(),
                        "frequency": current_task.get("frequency"),
                        "interval": current_task.get("interval"),
                        "auto_skipped": True,
                        "passive": as_bool(current_task.get("passive")),
                    }
                )
                changed = True

                next_due = self.calculate_next_due(current_task)
                new_task = self._next_recurring_occurrence(current_task, next_due, series_id)
                if next_due.date() >= today:
                    new_active.append(new_task)
                    break
                current_task = new_task
            else:
                # Safety: if still overdue after many steps, keep the last rolled task
                new_active.append(new_task)

        if changed:
            self.data["active"] = new_active
            self.save_data()
        return changed

    # ========== CATEGORY MANAGER ==========
    def promote_timed_due_tasks(self) -> bool:
        """
        When a task with a specific clock time (not 00:00) becomes Due, auto-bump
        it to ★★★ and place it at the top of the 3-star group.
        Runs once per due value (tracked via auto_starred_for_due).
        """
        changed = False
        newly = []
        for task in self.data.get("active", []):
            due = task.get("due")
            if not due:
                continue
            try:
                dt = datetime.fromisoformat(due)
            except Exception:
                continue
            # Date-only (midnight) dues are not auto-starred
            if (dt.hour, dt.minute, dt.second) == (0, 0, 0):
                continue
            # Due from midnight of that calendar day (same as list status)
            if dt.date() > date.today():
                continue
            if self.is_blocked(task):
                continue
            if task.get("auto_starred_for_due") == due:
                continue

            task["auto_starred_for_due"] = due
            task["priority"] = 3
            task.pop("p3_order", None)
            newly.append(task)
            changed = True

        if not newly:
            return False

        # Push existing ★★★ orders down; new promotions take the top slots
        others = [
            t for t in self.data.get("active", [])
            if clamp_priority(t.get("priority", 0)) >= 3 and t not in newly
        ]
        others.sort(key=lambda t: self._active_sort_key(t))
        # Newest promotions first (most recently due first among the batch)
        newly.sort(key=lambda t: t.get("due") or "", reverse=True)
        for i, t in enumerate(newly + others):
            t["star_order"] = i
            t.pop("p3_order", None)
        self.save_data()
        return True


    def completion_count(self, source_ref: str) -> int:
        """How many times this source (task id or series id) has been completed."""
        if not source_ref:
            return 0
        series_id = self.resolve_series_id(source_ref)
        count = 0
        for h in self.data.get("history", []):
            if h.get("status") != "completed":
                continue
            if h.get("id") == source_ref:
                count += 1
                continue
            if series_id and h.get("series_id") == series_id:
                count += 1
                continue
            if h.get("series_id") == source_ref:
                count += 1
        return count

    def process_completion_rules(self, source_ref: str = None) -> int:
        """
        For each rule, if source completions crossed one or more 'every_n' thresholds
        since last_fired_at_count, spawn that many new due tasks.
        Returns number of tasks spawned.
        """
        rules = self.data.get("completion_rules") or []
        if not rules:
            return 0

        spawned = 0
        event_series = self.resolve_series_id(source_ref) if source_ref else None

        for rule in rules:
            ref = rule.get("source_ref") or ""
            if source_ref:
                rule_series = self.resolve_series_id(ref)
                matches = (
                    ref == source_ref
                    or (event_series and ref == event_series)
                    or (event_series and rule_series and event_series == rule_series)
                )
                if not matches:
                    continue

            every_n = safe_int(rule.get("every_n", 0), default=0)
            if every_n < 1:
                continue
            spawn_text = (rule.get("spawn_text") or "").strip()
            if not spawn_text:
                continue

            total = self.completion_count(ref)
            last = safe_int(rule.get("last_fired_at_count", 0), default=0)
            # Count from the counter base (supports reset at any total, not only
            # multiples of every_n). Spawn when (total - last) crosses every_n.
            since = max(0, total - last)
            to_spawn = since // every_n
            if to_spawn <= 0:
                continue

            # Inherit category + other template fields from the current source task
            spawn_cat = rule.get("spawn_category") or ""
            source_task = None
            for t in self.data.get("active", []):
                if t.get("id") == ref or t.get("series_id") == ref or self.prereq_ref_for_task(t) == ref:
                    spawn_cat = t.get("category") or spawn_cat
                    source_task = t
                    break

            for _ in range(to_spawn):
                self.data["active"].append(
                    make_spawned_task(
                        spawn_text,
                        category=spawn_cat,
                        source=source_task,
                        rule_id=rule.get("id"),
                        spawned_from_count=total,
                    )
                )
                spawned += 1

            rule["last_fired_at_count"] = last + to_spawn * every_n

        return spawned

    def source_task_choices(self):
        """Label + ref pairs for completion-rule pickers (↻ marks recurring)."""
        choices = []
        seen_series = set()
        for t in self.data.get("active", []):
            ref = self.prereq_ref_for_task(t)
            if t.get("type") == "recurring":
                if ref in seen_series:
                    continue
                seen_series.add(ref)
                label = "↻ " + (t.get("text") or "(unnamed)")
            else:
                label = t.get("text") or "(unnamed)"
            choices.append((label, ref))
        return choices

    def rule_progress(self, rule) -> tuple:
        """
        Return (progress_toward_next, every_n, total_completions, last_fired).
        Progress counts completions since last_fired_at_count (reset-aware),
        not total % every_n.
        """
        ref = rule.get("source_ref") or ""
        every_n = max(1, safe_int(rule.get("every_n", 1), default=1))
        total = self.completion_count(ref)
        last = safe_int(rule.get("last_fired_at_count", 0), default=0)
        since = max(0, total - last)
        progress = since % every_n
        return progress, every_n, total, last

    def delete_completion_rule(self, rule_id) -> bool:
        rule = next(
            (r for r in (self.data.get("completion_rules") or []) if r.get("id") == rule_id),
            None,
        )
        spawn = (rule.get("spawn_text") or "").strip() if rule else ""
        if not spawn:
            spawn = "(unnamed spawn)"
        every_n = rule.get("every_n", "?") if rule else "?"
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Delete completion rule that spawns “{spawn}” every {every_n} completions?",
        ):
            return False
        self.data["completion_rules"] = [
            r for r in (self.data.get("completion_rules") or []) if r.get("id") != rule_id
        ]
        self.save_data()
        return True

    def toggle_interval(self, *_args):
        """Show interval / weekday / sticky-flexi controls based on frequency."""
        freq = self.freq_var.get()
        if freq == "every X days":
            self.interval_label.pack(side="left", padx=(5, 2), after=self.freq_menu)
            self.interval_entry.pack(side="left", after=self.interval_label)
            self.days_label.pack(side="left", padx=(2, 8), after=self.interval_entry)
        else:
            self.interval_label.pack_forget()
            self.interval_entry.pack_forget()
            self.days_label.pack_forget()

        if freq == "weekly":
            self.weekday_frame.pack(fill="x", padx=12, pady=(2, 2), after=self.type_frame)
        else:
            self.weekday_frame.pack_forget()

        # Sticky/Flexi for longer-than-daily recurrences only
        if freq in ("weekly", "monthly", "annually", "every X days"):
            after_w = self.weekday_frame if freq == "weekly" else self.type_frame
            self.due_anchor_frame.pack(fill="x", padx=12, pady=(2, 2), after=after_w)
        else:
            self.due_anchor_frame.pack_forget()

    def get_selected_weekdays(self):
        return [i for i, v in enumerate(getattr(self, "weekday_vars", []) or []) if v.get()]

    def parse_datetime(self, date_str, time_str):
        date_str = (date_str or "").strip()
        time_str = (time_str or "").strip()
        if not date_str:
            return None
        dt = None
        for fmt in ("%d/%m/%y", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(date_str, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            messagebox.showerror("Invalid Date", "Please use format dd/mm/yy")
            return "error"
        if time_str:
            try:
                t = datetime.strptime(time_str, "%H:%M")
                dt = dt.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            except ValueError:
                messagebox.showerror("Invalid Time", "Please use format hh:mm")
                return "error"
        else:
            dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return dt

    def format_dt(self, dt_str):
        """Date display: blank if missing; date only at midnight; otherwise date + time."""
        if not dt_str:
            return ""
        try:
            dt = datetime.fromisoformat(dt_str)
            if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
                return dt.strftime("%d/%m/%y")
            return dt.strftime("%d/%m/%y %H:%M")
        except Exception:
            return str(dt_str)

    def format_completed_at(self, value):
        if not value:
            return ""
        try:
            return datetime.fromisoformat(value).strftime("%d/%m/%y %H:%M")
        except Exception:
            return str(value)

    def is_future(self, task):
        """True if the due calendar day is still in the future (due from midnight that day)."""
        due = task.get("due")
        if not due:
            return False
        try:
            return datetime.fromisoformat(due).date() > date.today()
        except Exception:
            return False

    # ========== PREREQUISITES ==========
    def prereq_ref_for_task(self, task) -> str:
        """
        Stable reference used in prerequisite lists.
        One-off tasks → their id.
        Recurring tasks → series_id (survives skip/complete roll-forward).
        """
        if task.get("type") == "recurring":
            return task.get("series_id") or task.get("id")
        return task.get("id")

    def _refs_match(self, stored_ref: str, other_id: str = None, other_series: str = None) -> bool:
        if not stored_ref:
            return False
        if other_id and stored_ref == other_id:
            return True
        if other_series and stored_ref == other_series:
            return True
        return False

    def resolve_series_id(self, prereq_ref: str):
        """If prereq_ref points at a recurring series (or one of its occurrences), return series_id."""
        if not prereq_ref:
            return None
        for t in self.data.get("active", []):
            if t.get("series_id") == prereq_ref:
                return prereq_ref
            if t.get("id") == prereq_ref and t.get("type") == "recurring":
                return t.get("series_id") or t.get("id")
        for h in self.data.get("history", []):
            if h.get("series_id") == prereq_ref:
                return prereq_ref
            if h.get("id") == prereq_ref and h.get("series_id"):
                return h.get("series_id")
            if h.get("id") == prereq_ref and h.get("type") == "recurring":
                return h.get("series_id") or h.get("id")
        return None

    def latest_series_history(self, series_id: str):
        rows = [
            h for h in self.data.get("history", [])
            if h.get("series_id") == series_id
        ]
        if not rows:
            return None
        return max(rows, key=lambda h: h.get("completed_at") or "")

    def build_runtime_caches(self):
        """One-shot indexes used during list refresh / status checks (big speedup)."""
        completed_ids = set()
        series_last = {}  # series_id -> latest history row
        name_by_ref = {}
        active_by_id = {}
        active_by_series = {}

        for t in self.data.get("active", []):
            tid = t.get("id")
            if tid:
                active_by_id[tid] = t
                name_by_ref[tid] = t.get("text") or "(unnamed)"
            sid = t.get("series_id")
            if sid:
                active_by_series[sid] = t
                name_by_ref[sid] = t.get("text") or "(unnamed)"

        for h in self.data.get("history", []):
            hid = h.get("id")
            text = h.get("text") or "(unnamed)"
            if hid:
                name_by_ref.setdefault(hid, text)
                if h.get("status") == "completed":
                    completed_ids.add(hid)
            sid = h.get("series_id")
            if sid:
                name_by_ref.setdefault(sid, text)
                prev = series_last.get(sid)
                if not prev or (h.get("completed_at") or "") >= (prev.get("completed_at") or ""):
                    series_last[sid] = h

        # Category path / colour memo
        path_cache = {}
        color_cache = {}
        for cid in (self.data.get("categories") or {}):
            path_cache[cid] = self.category_path(cid)
            color_cache[cid] = self.get_category_color(cid)

        self._rt = {
            "completed_ids": completed_ids,
            "series_last": series_last,
            "name_by_ref": name_by_ref,
            "active_by_id": active_by_id,
            "active_by_series": active_by_series,
            "path_cache": path_cache,
            "color_cache": color_cache,
            "prereq_done": {},  # filled lazily
        }

    def clear_runtime_caches(self):
        self._rt = None

    def is_prereq_completed(self, prereq_ref: str) -> bool:
        """
        One-off: satisfied only when that task id has a *completed* history row.

        Recurring series (current-cycle rule):
        - If an active occurrence is due or overdue → not satisfied (must finish it).
        - If the active occurrence is still in the future → satisfied only when the
          previous cycle was *completed* (a skip does not unlock dependents).
        - If the series has no active occurrence → satisfied only if the last
          history row for the series was completed.
        """
        if not prereq_ref:
            return False

        rt = getattr(self, "_rt", None)
        if rt is not None and prereq_ref in rt["prereq_done"]:
            return rt["prereq_done"][prereq_ref]

        series_id = self.resolve_series_id(prereq_ref)

        if series_id:
            if rt is not None:
                active = rt["active_by_series"].get(series_id)
                last = rt["series_last"].get(series_id)
            else:
                active = next(
                    (
                        t for t in self.data.get("active", [])
                        if t.get("series_id") == series_id or (
                            t.get("id") == series_id and t.get("type") == "recurring"
                        )
                    ),
                    None,
                )
                last = self.latest_series_history(series_id)

            if active is not None:
                if not self.is_future(active):
                    result = False
                else:
                    result = bool(last and last.get("status") == "completed")
            else:
                result = bool(last and last.get("status") == "completed")
        else:
            if rt is not None:
                result = prereq_ref in rt["completed_ids"]
            else:
                result = any(
                    h.get("id") == prereq_ref and h.get("status") == "completed"
                    for h in self.data.get("history", [])
                )

        if rt is not None:
            rt["prereq_done"][prereq_ref] = result
        return result

    def is_blocked(self, task) -> bool:
        prereqs = task.get("prerequisites") or []
        if not prereqs:
            return False
        return any(not self.is_prereq_completed(pid) for pid in prereqs)

    def task_status(self, task) -> str:
        """due | not_due | blocked — blocked means waiting on incomplete prerequisites."""
        if self.is_blocked(task):
            return "blocked"
        if self.is_future(task):
            return "not_due"
        return "due"

    def resolve_task_name(self, prereq_ref: str) -> str:
        rt = getattr(self, "_rt", None)
        if rt is not None:
            return rt["name_by_ref"].get(prereq_ref, "(missing task)")
        for t in self.data.get("active", []):
            if t.get("id") == prereq_ref or t.get("series_id") == prereq_ref:
                return t.get("text") or "(unnamed)"
        for h in reversed(self.data.get("history", [])):
            if h.get("id") == prereq_ref or h.get("series_id") == prereq_ref:
                return h.get("text") or "(unnamed)"
        return "(missing task)"

    def prereq_ref_still_exists(self, prereq_ref: str) -> bool:
        """True if this prereq still points at a known active or historical task."""
        if not prereq_ref:
            return False
        for t in self.data.get("active", []):
            if t.get("id") == prereq_ref or t.get("series_id") == prereq_ref:
                return True
        for h in self.data.get("history", []):
            if h.get("id") == prereq_ref or h.get("series_id") == prereq_ref:
                return True
        return False

    def remove_prereq_refs(self, drop_refs) -> bool:
        """Strip refs from every active task's prerequisite list. Returns True if anything changed."""
        if not drop_refs:
            return False
        drop_refs = set(drop_refs)
        changed = False
        for t in self.data.get("active", []):
            prereqs = t.get("prerequisites") or []
            cleaned = [p for p in prereqs if p not in drop_refs]
            if cleaned != prereqs:
                t["prerequisites"] = cleaned
                changed = True
        return changed

    def cleanup_dead_prereqs(self) -> bool:
        """Remove prerequisite refs that point at nothing (deleted tasks with no history)."""
        changed = False
        for t in self.data.get("active", []):
            prereqs = t.get("prerequisites") or []
            cleaned = [p for p in prereqs if self.prereq_ref_still_exists(p)]
            if cleaned != prereqs:
                t["prerequisites"] = cleaned
                changed = True
        return changed

    def prereq_labels(self, task) -> list:
        """Human-readable list of prerequisite names with done/waiting markers."""
        labels = []
        for pid in task.get("prerequisites") or []:
            name = self.resolve_task_name(pid)
            done = self.is_prereq_completed(pid)
            labels.append(("✓ " if done else "○ ") + name)
        return labels

    def get_selected_prerequisites(self) -> list:
        return [ref for ref, var in self.prereq_vars.items() if var.get()]

    def toggle_prereq_panel(self):
        self.prereq_open = not self.prereq_open
        if self.prereq_open:
            self.prereq_toggle_btn.configure(text="Prerequisites ▾")
            self.prereq_panel.pack(fill="x", padx=12, pady=(2, 6), after=self.prereq_header)
            self.rebuild_prereq_checkboxes()
        else:
            self.prereq_toggle_btn.configure(text="Prerequisites ▸")
            self.prereq_panel.pack_forget()

    def _prereq_candidate_label(self, task) -> str:
        label = task.get("text") or "(unnamed)"
        if task.get("type") == "recurring":
            label = "↻ " + label
        cat = task.get("category") or ""
        if cat and self.get_category(cat):
            label = f"{label}  ·  {self.category_path(cat)}"
        elif cat:
            label = f"{label}  ·  {cat}"
        return label

    def _prereq_search_haystack(self, task) -> str:
        """Lowercase text used for search matching (name + category path)."""
        parts = [task.get("text") or ""]
        cat = task.get("category") or ""
        if cat and self.get_category(cat):
            parts.append(self.category_path(cat))
        elif cat:
            parts.append(str(cat))
        return " ".join(parts).lower()

    def rebuild_prereq_checkboxes(self, selected=None, exclude_id=None):
        """Rebuild the add-form prerequisite checkbox list (with search filter)."""
        if selected is None:
            selected = set(self.get_selected_prerequisites())
        else:
            selected = set(selected)

        for w in self.prereq_panel.winfo_children():
            w.destroy()
        self.prereq_vars = {}
        self._prereq_cb_widgets = {}

        candidates = [
            t for t in self.data.get("active", []) if t.get("id") != exclude_id
        ]
        if not candidates:
            ctk.CTkLabel(
                self.prereq_panel,
                text="No other active tasks to use as prerequisites.",
                text_color="gray",
            ).pack(padx=10, pady=8, anchor="w")
            self._update_prereq_summary()
            return

        ctk.CTkLabel(
            self.prereq_panel,
            text="This task will not be Due until all selected tasks are completed:",
            text_color="gray",
            anchor="w",
        ).pack(fill="x", padx=10, pady=(6, 2))

        search_row = ctk.CTkFrame(self.prereq_panel, fg_color="transparent")
        search_row.pack(fill="x", padx=8, pady=(2, 4))
        search_var = ctk.StringVar(value=getattr(self, "_prereq_search_text", "") or "")
        search_entry = ctk.CTkEntry(
            search_row,
            textvariable=search_var,
            placeholder_text="Search tasks…",
            height=28,
        )
        search_entry.pack(side="left", fill="x", expand=True)
        clear_btn = ctk.CTkButton(
            search_row, text="Clear", width=56, height=28,
            command=lambda: search_var.set(""),
        )
        clear_btn.pack(side="left", padx=(6, 0))

        scroll = ctk.CTkScrollableFrame(self.prereq_panel, height=110)
        scroll.pack(fill="x", padx=8, pady=(0, 4))
        empty_lbl = ctk.CTkLabel(
            self.prereq_panel, text="", text_color="gray", anchor="w"
        )
        empty_lbl.pack(fill="x", padx=12, pady=(0, 6))

        # Deduplicate by stable ref (one checkbox per series)
        seen_refs = set()
        for t in candidates:
            ref = self.prereq_ref_for_task(t)
            if not ref or ref in seen_refs:
                continue
            seen_refs.add(ref)
            is_on = ref in selected or t.get("id") in selected
            var = ctk.BooleanVar(value=is_on)
            self.prereq_vars[ref] = var
            label = self._prereq_candidate_label(t)
            cb = ctk.CTkCheckBox(
                scroll, text=label, variable=var, command=self._update_prereq_summary
            )
            cb.pack(anchor="w", padx=6, pady=2)
            self._prereq_cb_widgets[ref] = {
                "widget": cb,
                "haystack": self._prereq_search_haystack(t),
            }

        def apply_filter(*_args):
            q = (search_var.get() or "").strip().lower()
            self._prereq_search_text = search_var.get()
            visible = 0
            for ref, info in self._prereq_cb_widgets.items():
                cb = info["widget"]
                if not q or q in info["haystack"]:
                    cb.pack(anchor="w", padx=6, pady=2)
                    visible += 1
                else:
                    cb.pack_forget()
            if visible == 0:
                empty_lbl.configure(text="No tasks match that search.")
            else:
                empty_lbl.configure(text="")
            self._update_prereq_summary()

        search_var.trace_add("write", lambda *_: apply_filter())
        apply_filter()
        self._update_prereq_summary()

    def _update_prereq_summary(self):
        n = len(self.get_selected_prerequisites())
        if n == 0:
            self.prereq_summary.configure(text="None selected")
        elif n == 1:
            self.prereq_summary.configure(text="1 task selected")
        else:
            self.prereq_summary.configure(text=f"{n} tasks selected")

    def build_prereq_picker(self, parent, selected_ids=None, exclude_id=None):
        """
        Build a searchable scrollable checkbox list inside parent for edit dialogs.
        Returns a dict of stable_ref -> BooleanVar (series_id for recurring).
        Selection is preserved while filtering — hidden boxes stay checked.
        """
        selected_ids = set(selected_ids or [])
        vars_map = {}
        candidates = [
            t for t in self.data.get("active", []) if t.get("id") != exclude_id
        ]
        if not candidates:
            ctk.CTkLabel(
                parent, text="No other active tasks available.", text_color="gray"
            ).pack(padx=8, pady=6, anchor="w")
            return vars_map

        search_row = ctk.CTkFrame(parent, fg_color="transparent")
        search_row.pack(fill="x", padx=8, pady=(4, 2))
        search_var = ctk.StringVar(value="")
        search_entry = ctk.CTkEntry(
            search_row,
            textvariable=search_var,
            placeholder_text="Search tasks…",
            height=28,
        )
        search_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            search_row, text="Clear", width=56, height=28,
            command=lambda: search_var.set(""),
        ).pack(side="left", padx=(6, 0))

        scroll = ctk.CTkScrollableFrame(parent, height=120)
        scroll.pack(fill="both", expand=True, padx=8, pady=4)
        empty_lbl = ctk.CTkLabel(parent, text="", text_color="gray", anchor="w")
        empty_lbl.pack(fill="x", padx=12, pady=(0, 4))

        cb_info = {}
        seen_refs = set()
        for t in candidates:
            ref = self.prereq_ref_for_task(t)
            if not ref or ref in seen_refs:
                continue
            seen_refs.add(ref)
            is_on = ref in selected_ids or t.get("id") in selected_ids
            var = ctk.BooleanVar(value=is_on)
            vars_map[ref] = var
            label = self._prereq_candidate_label(t)
            cb = ctk.CTkCheckBox(scroll, text=label, variable=var)
            cb.pack(anchor="w", padx=4, pady=2)
            cb_info[ref] = {
                "widget": cb,
                "haystack": self._prereq_search_haystack(t),
            }

        def apply_filter(*_args):
            q = (search_var.get() or "").strip().lower()
            visible = 0
            for ref, info in cb_info.items():
                cb = info["widget"]
                if not q or q in info["haystack"]:
                    cb.pack(anchor="w", padx=4, pady=2)
                    visible += 1
                else:
                    cb.pack_forget()
            empty_lbl.configure(
                text="No tasks match that search." if visible == 0 else ""
            )

        search_var.trace_add("write", lambda *_: apply_filter())
        return vars_map

    # ========== CORE LOGIC ==========
    def add_task(self):
        text = self.task_entry.get().strip()
        if not text:
            messagebox.showwarning("Empty", "Please type a task first!")
            return
        freq = self.freq_var.get() or "Once"
        due = self.parse_datetime(self.date_entry.get(), self.time_entry.get())
        if due == "error":
            return

        show_cal = bool(self.calendar_var.get()) if due else False
        is_passive = bool(self.passive_var.get()) and not show_cal
        pri_label = self.priority_var.get()
        priority = self._priority_menu_map.get(pri_label, 0)
        is_recurring = freq != "Once"
        task = {
            "id": str(uuid.uuid4()),
            "text": text,
            "type": "recurring" if is_recurring else "one-off",
            "category": self.get_add_form_category_id(),
            "due": due.isoformat() if due else None,
            "created": now_iso(),
            "prerequisites": self.get_selected_prerequisites(),
            "show_on_calendar": show_cal,
            "priority": priority,
            "passive": is_passive,
            "notes": "",
        }
        if is_recurring:
            if not due:
                messagebox.showwarning("Date required", "Recurring tasks need a start date.")
                return
            task["frequency"] = freq
            task["series_id"] = task["id"]
            if freq == "every X days":
                interval = safe_int(self.interval_entry.get().strip(), default=0)
                if interval < 1:
                    messagebox.showerror("Invalid interval", "Enter a valid number of days")
                    return
                task["interval"] = interval
            else:
                task["interval"] = 1
            if freq == "weekly":
                days = self.get_selected_weekdays()
                if not days:
                    messagebox.showwarning(
                        "Weekdays",
                        "Pick at least one day of the week for a weekly task.",
                    )
                    return
                task["weekdays"] = days
            else:
                task.pop("weekdays", None)
            if freq in ("weekly", "monthly", "annually", "every X days"):
                task["due_anchor"] = self.due_anchor_var.get() or "sticky"
            else:
                task.pop("due_anchor", None)
            if freq in ("monthly", "annually"):
                task["anchor_day"] = due.day
            else:
                task.pop("anchor_day", None)

        if priority >= 1:
            task["star_order"] = self._next_star_order(priority)
        self.data["active"].append(task)
        self.save_data()
        self.task_entry.delete(0, "end")
        self.date_entry.delete(0, "end")
        self.time_entry.delete(0, "end")
        self.interval_entry.delete(0, "end")
        self.calendar_var.set(False)
        self._sync_calendar_checkbox()
        self.passive_var.set(False)
        self.priority_var.set("☆")
        self.freq_var.set("Once")
        for v in getattr(self, "weekday_vars", []) or []:
            v.set(False)
        self.due_anchor_var.set("sticky")
        self.toggle_interval()
        self.add_header_var.set("")
        self.category_var.set("")
        self.rebuild_add_category_menu()
        for var in self.prereq_vars.values():
            var.set(False)
        self._update_prereq_summary()
        if self.prereq_open:
            self.rebuild_prereq_checkboxes()
        self.refresh_list()

    def calculate_next_due(self, task, from_dt=None) -> datetime:
        return next_due_datetime(task, from_dt=from_dt)

    def _next_recurring_occurrence(self, task, next_due, series_id) -> dict:
        try:
            priority = clamp_priority(task.get("priority", 0))
        except (TypeError, ValueError):
            priority = 0
        nxt = {
            "id": str(uuid.uuid4()),
            "series_id": series_id or task.get("id"),
            "text": task["text"],
            "type": "recurring",
            "category": task.get("category", ""),
            "due": next_due.isoformat(),
            "frequency": task.get("frequency", "daily"),
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
        if priority >= 1:
            # Keep manual same-star order across roll-forward
            so = task.get("star_order", task.get("p3_order"))
            if so is not None:
                nxt["star_order"] = so
            else:
                nxt["star_order"] = self._next_star_order(priority)
        return nxt

    def complete_or_skip(self, task_id, status):
        task = next((t for t in self.data["active"] if t["id"] == task_id), None)
        if not task:
            return

        series_id = task.get("series_id") or (task["id"] if task.get("type") == "recurring" else None)
        try:
            _hist_pri = clamp_priority(task.get("priority", 0))
        except (TypeError, ValueError):
            _hist_pri = 0
        self.data["history"].append(
            {
                "id": task["id"],
                "series_id": series_id,
                "text": task["text"],
                "type": task["type"],
                "category": task.get("category", ""),
                "status": status,
                "original_due": task.get("due"),
                "completed_at": now_iso(),
                "frequency": task.get("frequency"),
                "interval": task.get("interval"),
                "passive": as_bool(task.get("passive")),
                "show_on_calendar": as_bool(task.get("show_on_calendar")),
                "priority": _hist_pri,
            }
        )
        self.data["active"] = [t for t in self.data["active"] if t["id"] != task_id]

        # One-off skipped → drop it as a prerequisite everywhere (it will never be completed)
        if status == "skipped" and task.get("type") != "recurring":
            self.remove_prereq_refs({task["id"]})

        if task["type"] == "recurring":
            anchor = (task.get("due_anchor") or "sticky").lower()
            from_dt = datetime.now() if anchor == "flexi" else None
            next_due = self.calculate_next_due(task, from_dt=from_dt)
            self.data["active"].append(
                self._next_recurring_occurrence(task, next_due, series_id or task["id"])
            )

        # After a real completion, fire any "every N completions" spawn rules
        if status == "completed":
            source_ref = series_id or task["id"]
            self.process_completion_rules(source_ref)

        self.save_data()
        if self.prereq_open:
            self.rebuild_prereq_checkboxes()
        # Incremental: refresh_list diffs rows instead of tearing everything down
        self.refresh_list()

    def delete_task(self, task_id):
        doomed = next((t for t in self.data["active"] if t["id"] == task_id), None)
        label = (doomed.get("text") or "").strip() if doomed else ""
        if not label:
            label = "(unnamed task)"
        kind = "recurring task" if doomed and doomed.get("type") == "recurring" else "task"
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Delete {kind} “{label}”?\n\nThis cannot be undone.",
        ):
            return
        drop_refs = {task_id}
        if doomed:
            ref = self.prereq_ref_for_task(doomed)
            if ref:
                drop_refs.add(ref)
            if doomed.get("series_id"):
                drop_refs.add(doomed["series_id"])

        self.data["active"] = [t for t in self.data["active"] if t["id"] != task_id]
        # Deleted tasks are removed as prerequisites everywhere
        self.remove_prereq_refs(drop_refs)
        self.cleanup_dead_prereqs()
        self.save_data()
        if self.prereq_open:
            self.rebuild_prereq_checkboxes()
        self.refresh_list()

    def delete_history_entry(self, history_id):
        entry = next((h for h in self.data["history"] if h.get("id") == history_id), None)
        label = (entry.get("text") or "").strip() if entry else ""
        if not label:
            label = "(unnamed)"
        status = (entry.get("status") or "entry") if entry else "entry"
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Delete history record for “{label}” ({status})?\n\n"
            "This only removes the past record — it does not change any active task.",
        ):
            return
        self.data["history"] = [h for h in self.data["history"] if h.get("id") != history_id]
        self.save_data()
        self.refresh_list()

    def get_series_history(self, series_id):
        """All history rows that belong to this recurring series, newest first."""
        if not series_id:
            return []
        rows = [h for h in self.data["history"] if h.get("series_id") == series_id]
        return sorted(rows, key=lambda h: h.get("completed_at") or "", reverse=True)

