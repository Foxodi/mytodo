"""Modal/tool windows: edit task, notes, categories, completion rules, series history."""
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

class DialogsMixin:
    def toggle_window_maximize(self, win):
        """Toggle maximized (zoomed) state — Windows / typical desktop behavior."""
        try:
            if str(win.state()) == "zoomed":
                win.state("normal")
            else:
                win.state("zoomed")
        except Exception:
            try:
                # Some Linux WMs
                if win.attributes("-zoomed"):
                    win.attributes("-zoomed", False)
                else:
                    win.attributes("-zoomed", True)
            except Exception:
                pass

    def enable_double_click_maximize(self, win, *header_widgets):
        """
        Ensure dialog can maximize, and double-clicking the top header area
        toggles maximize (same idea as double-clicking a normal title bar).
        """
        try:
            win.resizable(True, True)
        except Exception:
            pass

        def on_dbl(_event=None):
            self.toggle_window_maximize(win)

        for w in header_widgets:
            if w is None:
                continue
            try:
                w.bind("<Double-Button-1>", on_dbl)
                for child in w.winfo_children():
                    try:
                        child.bind("<Double-Button-1>", on_dbl)
                    except Exception:
                        pass
            except Exception:
                pass
        # Also: double-click near the very top of the window content
        def on_win_dbl(event):
            try:
                if event.y <= 56:
                    on_dbl()
            except Exception:
                pass

        try:
            win.bind("<Double-Button-1>", on_win_dbl)
        except Exception:
            pass

    def _attach_source_task_search(self, parent, menu, src_var, labels):
        """Search box that filters the Source task OptionMenu values."""
        search_row = ctk.CTkFrame(parent, fg_color="transparent")
        search_row.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(search_row, text="Search:").pack(side="left")
        search_var = ctk.StringVar()
        entry = ctk.CTkEntry(
            search_row,
            textvariable=search_var,
            placeholder_text="Filter source tasks…",
            width=220,
            height=28,
        )
        entry.pack(side="left", padx=8, fill="x", expand=True)

        all_labels = list(labels)

        def apply_filter(_event=None):
            q = (search_var.get() or "").strip().lower()
            filtered = [lab for lab in all_labels if q in lab.lower()] if q else list(all_labels)
            if not filtered:
                menu.configure(values=["(no matches)"])
                src_var.set("(no matches)")
                return
            menu.configure(values=filtered)
            if src_var.get() not in filtered:
                src_var.set(filtered[0])

        entry.bind("<KeyRelease>", apply_filter)
        ctk.CTkButton(
            search_row, text="✕", width=28, height=28,
            command=lambda: (search_var.set(""), apply_filter()),
        ).pack(side="left", padx=(4, 0))

    # ========== COMPLETION RULES (every N completions → spawn task) ==========
    def open_completion_rules_manager(self):
        win = ctk.CTkToplevel(self)
        win.title("Completion Rules")
        try:
            win.geometry(self.data.get("completion_rules_geometry") or "560x520")
        except Exception:
            win.geometry("560x520")
        win.minsize(480, 420)
        win.transient(self)
        win.grab_set()

        def persist_and_close():
            try:
                if str(win.state()) != "zoomed":
                    self.data["completion_rules_geometry"] = win.geometry()
                    self.save_data()
            except Exception:
                pass
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", persist_and_close)

        header = ctk.CTkFrame(win, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(15, 4))
        ctk.CTkLabel(
            header,
            text="Completion Rules",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Every N times a source task is completed, create a new due task.",
            text_color="gray",
            anchor="w",
        ).pack(fill="x", pady=(0, 4))
        self.enable_double_click_maximize(win, header)

        list_frame = ctk.CTkScrollableFrame(win, label_text="Active rules")
        list_frame.pack(fill="both", expand=True, padx=15, pady=6)

        def rebuild_list():
            for w in list_frame.winfo_children():
                w.destroy()
            rules = self.data.get("completion_rules") or []
            if not rules:
                ctk.CTkLabel(
                    list_frame,
                    text="No rules yet. Create one below.",
                    text_color="gray",
                ).pack(pady=20)
                return
            for rule in rules:
                row = ctk.CTkFrame(list_frame)
                row.pack(fill="x", pady=4, padx=4)

                src_name = self.resolve_task_name(rule.get("source_ref") or "")
                progress, every_n, total, _last = self.rule_progress(rule)
                spawn_text = rule.get("spawn_text") or "(unnamed)"

                info = (
                    f"Every {every_n}×  {src_name}\n"
                    f"→ creates: {spawn_text}\n"
                    f"Progress: {progress}/{every_n} toward next  "
                    f"(total completions: {total})"
                )
                ctk.CTkLabel(row, text=info, anchor="w", justify="left").pack(
                    side="left", fill="x", expand=True, padx=10, pady=8
                )
                ctk.CTkButton(
                    row,
                    text="Delete",
                    width=70,
                    height=28,
                    fg_color="#d9534f",
                    hover_color="#c9302c",
                    command=lambda rid=rule.get("id"): delete_rule(rid),
                ).pack(side="right", padx=8, pady=8)
                ctk.CTkButton(
                    row,
                    text="Edit",
                    width=70,
                    height=28,
                    command=lambda rid=rule.get("id"): self.open_edit_completion_rule(
                        rid, on_saved=rebuild_list
                    ),
                ).pack(side="right", padx=(8, 0), pady=8)

        def delete_rule(rule_id):
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
                return
            self.data["completion_rules"] = [
                r for r in (self.data.get("completion_rules") or []) if r.get("id") != rule_id
            ]
            self.save_data()
            rebuild_list()

        rebuild_list()

        # --- Create new rule ---
        create = ctk.CTkFrame(win)
        create.pack(fill="x", padx=15, pady=(4, 12))
        ctk.CTkLabel(create, text="New rule", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=10, pady=(8, 4)
        )

        choices = self.source_task_choices()
        if not choices:
            ctk.CTkLabel(
                create,
                text="Add an active task first (e.g. a daily Pill task) to use as the source.",
                text_color="gray",
            ).pack(padx=10, pady=8, anchor="w")
            ctk.CTkButton(win, text="Close", width=100, command=persist_and_close).pack(
                pady=(0, 12)
            )
            return

        label_to_ref = {label: ref for label, ref in choices}
        labels = list(label_to_ref.keys())

        src_row = ctk.CTkFrame(create, fg_color="transparent")
        src_row.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(src_row, text="Source task:").pack(side="left")
        src_var = ctk.StringVar(value=labels[0])
        src_menu = ctk.CTkOptionMenu(src_row, values=labels, variable=src_var, width=280)
        src_menu.pack(side="left", padx=8)
        self._attach_source_task_search(create, src_menu, src_var, labels)

        n_row = ctk.CTkFrame(create, fg_color="transparent")
        n_row.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(n_row, text="Every").pack(side="left")
        n_entry = ctk.CTkEntry(n_row, width=60, placeholder_text="12")
        n_entry.pack(side="left", padx=8)
        n_entry.insert(0, "12")
        ctk.CTkLabel(n_row, text="completions, create:").pack(side="left")

        text_row = ctk.CTkFrame(create, fg_color="transparent")
        text_row.pack(fill="x", padx=10, pady=4)
        spawn_entry = ctk.CTkEntry(
            text_row, placeholder_text="Visit Doctor for more Pills", height=32
        )
        spawn_entry.pack(fill="x", expand=True)

        def create_rule():
            every_n = safe_int(n_entry.get().strip(), default=0)
            if every_n < 1:
                messagebox.showerror("Invalid", "Enter a whole number ≥ 1 for completions.")
                return
            spawn_text = spawn_entry.get().strip()
            if not spawn_text:
                messagebox.showwarning("Empty", "Enter the task name to create.")
                return
            src_label = src_var.get()
            source_ref = label_to_ref.get(src_label)
            if not source_ref:
                messagebox.showerror("Source", "Pick a source task.")
                return

            # Start counting from current total so we don't instantly spawn for past completions
            current = self.completion_count(source_ref)

            self.data.setdefault("completion_rules", []).append(
                {
                    "id": str(uuid.uuid4()),
                    "source_ref": source_ref,
                    "every_n": every_n,
                    "spawn_text": spawn_text,
                    "spawn_category": "",
                    "last_fired_at_count": current,  # only future completions count
                }
            )
            self.save_data()
            spawn_entry.delete(0, "end")
            rebuild_list()
            messagebox.showinfo(
                "Rule created",
                f"After {every_n} more completion(s) of “{self.resolve_task_name(source_ref)}”,\n"
                f"a new due task “{spawn_text}” will be created.\n\n"
                f"(Current completions: {current} — counting starts from here.)",
            )

        ctk.CTkButton(
            create, text="Create Rule", height=34, command=create_rule
        ).pack(pady=10, padx=10, fill="x")

        ctk.CTkButton(win, text="Close", width=100, command=persist_and_close).pack(
            pady=(0, 12)
        )

    def open_edit_completion_rule(self, rule_id, on_saved=None):
        """Edit spawn text, source, every_n, or reset progress counter."""
        rule = next(
            (r for r in (self.data.get("completion_rules") or []) if r.get("id") == rule_id),
            None,
        )
        if not rule:
            messagebox.showerror("Missing", "That rule no longer exists.")
            return

        win = ctk.CTkToplevel(self)
        win.title("Edit Completion Rule")
        try:
            win.geometry(self.data.get("completion_rule_edit_geometry") or "480x360")
        except Exception:
            win.geometry("480x360")
        win.minsize(400, 320)
        win.transient(self)
        win.grab_set()

        def persist_geometry():
            try:
                self.data["completion_rule_edit_geometry"] = win.geometry()
                self.save_data()
            except Exception:
                pass

        def close_edit():
            persist_geometry()
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close_edit)

        ctk.CTkLabel(win, text="Edit Completion Rule", font=ctk.CTkFont(size=16, weight="bold")).pack(
            pady=(14, 8), padx=15, anchor="w"
        )

        choices = self.source_task_choices()
        label_to_ref = {label: ref for label, ref in choices}
        labels = list(label_to_ref.keys()) or ["(no active sources)"]

        current_ref = rule.get("source_ref") or ""
        current_label = labels[0]
        for label, ref in label_to_ref.items():
            if ref == current_ref or self.resolve_series_id(ref) == self.resolve_series_id(current_ref):
                current_label = label
                break

        src_row = ctk.CTkFrame(win, fg_color="transparent")
        src_row.pack(fill="x", padx=15, pady=4)
        ctk.CTkLabel(src_row, text="Source task:").pack(side="left")
        src_var = ctk.StringVar(value=current_label)
        src_menu = ctk.CTkOptionMenu(src_row, values=labels, variable=src_var, width=280)
        src_menu.pack(side="left", padx=8)
        self._attach_source_task_search(win, src_menu, src_var, labels)

        n_row = ctk.CTkFrame(win, fg_color="transparent")
        n_row.pack(fill="x", padx=15, pady=4)
        ctk.CTkLabel(n_row, text="Every").pack(side="left")
        n_entry = ctk.CTkEntry(n_row, width=60)
        n_entry.pack(side="left", padx=8)
        n_entry.insert(0, str(rule.get("every_n") or 1))
        ctk.CTkLabel(n_row, text="completions, create:").pack(side="left")

        spawn_entry = ctk.CTkEntry(win, height=32)
        spawn_entry.pack(fill="x", padx=15, pady=4)
        spawn_entry.insert(0, rule.get("spawn_text") or "")

        def refresh_prog_label():
            progress, every_n, total, last = self.rule_progress(rule)
            prog_lbl.configure(
                text=(
                    f"Progress: {progress}/{every_n} toward next  "
                    f"(total completions: {total}, counter base: {last})"
                )
            )

        progress, every_n, total, last = self.rule_progress(rule)
        prog_lbl = ctk.CTkLabel(
            win,
            text=(
                f"Progress: {progress}/{every_n} toward next  "
                f"(total completions: {total}, counter base: {last})"
            ),
            text_color="gray",
            anchor="w",
        )
        prog_lbl.pack(fill="x", padx=15, pady=(8, 4))

        def reset_counter():
            if not messagebox.askyesno(
                "Reset progress counter",
                "Reset this rule’s progress counter?\n\n"
                "The next completion cycle will start from the current total "
                f"({self.completion_count(rule.get('source_ref') or '')}) — "
                "no immediate spawn from past completions.",
            ):
                return
            # Mutate the live rule object in self.data (same dict reference)
            live = next(
                (r for r in (self.data.get("completion_rules") or []) if r.get("id") == rule_id),
                rule,
            )
            ref = live.get("source_ref") or ""
            live["last_fired_at_count"] = self.completion_count(ref)
            self.save_data()
            refresh_prog_label()
            if on_saved:
                on_saved()

        ctk.CTkButton(win, text="Reset progress counter", command=reset_counter).pack(
            padx=15, pady=6, anchor="w"
        )

        def save():
            every_n_val = safe_int(n_entry.get().strip(), default=0)
            if every_n_val < 1:
                messagebox.showerror("Invalid", "Enter a whole number ≥ 1 for completions.")
                return
            spawn_text = spawn_entry.get().strip()
            if not spawn_text:
                messagebox.showwarning("Empty", "Enter the task name to create.")
                return
            source_ref = label_to_ref.get(src_var.get()) or rule.get("source_ref")
            if not source_ref:
                messagebox.showerror("Source", "Pick a source task.")
                return
            live = next(
                (r for r in (self.data.get("completion_rules") or []) if r.get("id") == rule_id),
                rule,
            )
            live["source_ref"] = source_ref
            live["every_n"] = every_n_val
            live["spawn_text"] = spawn_text
            live["spawn_category"] = ""
            # Keep last_fired_at_count as-is unless reset was used
            self.save_data()
            persist_geometry()
            if on_saved:
                on_saved()
            win.destroy()

        ctk.CTkButton(win, text="Save", height=34, command=save).pack(padx=15, pady=(10, 4), fill="x")
        ctk.CTkButton(win, text="Cancel", height=30, command=close_edit).pack(padx=15, pady=(0, 12), fill="x")

    def rename_category_dialog(self, cat_id) -> bool:
        old_name = self.category_name(cat_id)
        dialog = ctk.CTkInputDialog(text=f"Rename '{old_name}' to:", title="Rename")
        new_name = (dialog.get_input() or "").strip()
        if not new_name or new_name == old_name:
            return False
        if not self.get_category(cat_id):
            return False
        self.data["categories"][cat_id]["name"] = new_name
        self.save_data()
        self.update_all_category_menus()
        self.refresh_list()
        return True

    def recolour_category_dialog(self, cat_id) -> bool:
        meta = self.get_category(cat_id) or {}
        current = meta.get("color") or "#6b7280"
        chosen = colorchooser.askcolor(color=current, title=f"Colour for {meta.get('name')}")
        if not chosen or not chosen[1]:
            return False
        self.data["categories"][cat_id]["color"] = chosen[1]
        self.data["categories"][cat_id]["parent_id"] = None
        self.save_data()
        self.update_all_category_menus()
        self.refresh_list()
        return True

    def promote_to_header_dialog(self, cat_id) -> bool:
        """Convert a Category into a Header Category (top-level + colour)."""
        meta = self.get_category(cat_id)
        if not meta:
            return False
        if self.is_header_category(cat_id):
            return False
        name = meta.get("name") or "Category"
        chosen = colorchooser.askcolor(
            color="#3b82f6",
            title=f"Header colour for '{name}'",
        )
        if not chosen or not chosen[1]:
            return False
        old_parent = meta.get("parent_id")
        meta["parent_id"] = None
        meta["color"] = chosen[1]
        # Place among root siblings
        self.resequence_siblings(None)
        if old_parent is not None:
            self.resequence_siblings(old_parent)
        self.save_data()
        self.update_all_category_menus()
        self.refresh_list()
        return True

    def add_sub_category_dialog(self, parent_id) -> bool:
        parent_name = self.category_name(parent_id)
        dialog = ctk.CTkInputDialog(
            text=f"Category under '{parent_name}':",
            title="Add Category",
        )
        new_name = (dialog.get_input() or "").strip()
        if not new_name:
            return False
        cid = str(uuid.uuid4())
        self.data.setdefault("categories", {})[cid] = {
            "name": new_name,
            "parent_id": parent_id,
            "color": None,
            "sort_order": len(self.category_children(parent_id)),
        }
        self.save_data()
        self.update_all_category_menus()
        self.refresh_list()
        return True

    def delete_category_dialog(self, cat_id) -> bool:
        """Same delete rules as Manage Categories (header cascades; sub promotes children)."""
        name = self.category_name(cat_id)
        is_header = self.is_header_category(cat_id)

        if is_header:
            descendants = self.category_descendants(cat_id)
            desc_names = [self.category_name(d) for d in descendants]
            extra = ""
            if desc_names:
                listed = ", ".join(desc_names[:12])
                more = f" (+{len(desc_names) - 12} more)" if len(desc_names) > 12 else ""
                extra = (
                    f"\n\nThis will also delete ALL categories under it:\n{listed}{more}\n\n"
                    "Tasks will stay — they just lose this category."
                )
            if not messagebox.askyesno("Delete Header Category", f"Delete Header Category '{name}'?{extra}"):
                return False
            to_delete = {cat_id} | set(descendants)
            id_to_name = {i: self.category_path(i) for i in to_delete}
            for i in to_delete:
                self.data["categories"].pop(i, None)
            for t in self.data["active"]:
                if t.get("category") in to_delete:
                    t["category"] = ""
            for h in self.data["history"]:
                cid = h.get("category")
                if cid in to_delete:
                    h["category"] = id_to_name.get(cid, "")
            for rule in self.data.get("completion_rules") or []:
                if rule.get("spawn_category") in to_delete:
                    rule["spawn_category"] = ""
        else:
            parent_id = self.category_parent_id(cat_id)
            kids = list(self.category_children(cat_id))
            msg = f"Delete Category '{name}'?"
            if kids:
                msg += (
                    f"\n\n{len(kids)} nested categor{'y' if len(kids)==1 else 'ies'} "
                    "will move up one level."
                )
            msg += "\n\nActive tasks using only this category will clear it. History is unchanged."
            if not messagebox.askyesno("Delete Category", msg):
                return False
            for kid in kids:
                self.set_category_parent(kid, parent_id)
            self.data["categories"].pop(cat_id, None)
            self.resequence_siblings(parent_id)
            for t in self.data["active"]:
                if t.get("category") == cat_id:
                    t["category"] = ""
            for rule in self.data.get("completion_rules") or []:
                if rule.get("spawn_category") == cat_id:
                    rule["spawn_category"] = ""

        self.save_data()
        self.update_all_category_menus()
        self.refresh_list()
        return True

    # ========== MIND MAP ==========
    def open_series_history_window(self, series_id, title_hint=""):
        rows = self.get_series_history(series_id)
        # Also show the current active occurrence if present
        active = next(
            (t for t in self.data["active"] if t.get("series_id") == series_id),
            None,
        )

        win = ctk.CTkToplevel(self)
        name = title_hint or (active.get("text") if active else (rows[0].get("text") if rows else "Series"))
        win.title(f"History — {name}")
        try:
            win.geometry(self.data.get("series_history_geometry") or "520x480")
        except Exception:
            win.geometry("520x480")
        win.minsize(420, 360)
        win.transient(self)
        win.grab_set()

        def persist_and_close():
            try:
                self.data["series_history_geometry"] = win.geometry()
                self.save_data()
            except Exception:
                pass
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", persist_and_close)

        ctk.CTkLabel(
            win, text=f"🔁  {name}", font=ctk.CTkFont(size=18, weight="bold")
        ).pack(pady=(15, 4), padx=15, anchor="w")

        meta_bits = []
        if active:
            freq = active.get("frequency", "")
            if freq == "every X days":
                freq = f"every {active.get('interval', 1)} days"
            meta_bits.append(freq)
            meta_bits.append(f"Next due: {self.format_dt(active.get('due'))}")
        if active and active.get("category"):
            meta_bits.append(active.get("category"))
        if meta_bits:
            ctk.CTkLabel(win, text="  •  ".join(meta_bits), text_color="gray").pack(
                padx=15, anchor="w"
            )

        list_frame = ctk.CTkScrollableFrame(win, label_text=f"Past instances ({len(rows)})")
        list_frame.pack(fill="both", expand=True, padx=15, pady=10)

        if not rows:
            ctk.CTkLabel(
                list_frame, text="No history for this series yet.", text_color="gray"
            ).pack(pady=30)
        else:
            for h in rows:
                row = ctk.CTkFrame(list_frame)
                row.pack(fill="x", pady=3, padx=4)

                status = h.get("status", "")
                status_icon = "✓" if status == "completed" else "⏭"
                color = "#81c784" if status == "completed" else "#ffb74d"
                when = self.format_completed_at(h.get("completed_at"))
                due = self.format_dt(h.get("original_due")) if h.get("original_due") else "—"
                auto = "  (auto)" if h.get("auto_skipped") else ""

                label_text = f"{status_icon}  {str(status).title()} on {when}  •  Was due: {due}{auto}"
                lbl = ctk.CTkLabel(row, text=label_text, anchor="w", text_color=color)
                lbl.pack(side="left", fill="x", expand=True, padx=10, pady=8)

                hid = h.get("id")

                def on_right_click(e, history_id=hid, popup=win, sid=series_id, title=name):
                    self.show_history_context_menu(
                        e,
                        history_id,
                        refresh_cb=lambda: self._reopen_series_popup(popup, sid, title),
                    )

                row.bind("<Button-3>", on_right_click)
                lbl.bind("<Button-3>", on_right_click)

                ctk.CTkButton(
                    row,
                    text="Delete",
                    width=70,
                    height=28,
                    fg_color="#d9534f",
                    hover_color="#c9302c",
                    command=lambda history_id=hid: self._delete_history_and_refresh_popup(
                        history_id, win, series_id, name
                    ),
                ).pack(side="right", padx=8, pady=4)

        ctk.CTkButton(win, text="Close", width=100, command=persist_and_close).pack(pady=(0, 12))

    def _reopen_series_popup(self, old_win, series_id, title_hint):
        try:
            old_win.destroy()
        except Exception:
            pass
        self.open_series_history_window(series_id, title_hint=title_hint)
        self.refresh_list()

    def _delete_history_and_refresh_popup(self, history_id, win, series_id, title_hint):
        entry = next((h for h in self.data["history"] if h.get("id") == history_id), None)
        label = (entry.get("text") or title_hint or "").strip() or "(unnamed)"
        status = (entry.get("status") or "entry") if entry else "entry"
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Delete history record for “{label}” ({status})?\n\n"
            "This only removes the past record.",
        ):
            return
        self.data["history"] = [h for h in self.data["history"] if h.get("id") != history_id]
        self.save_data()
        win.destroy()
        self.open_series_history_window(series_id, title_hint=title_hint)
        self.refresh_list()

    def open_edit_window(self, task_id, on_saved=None):
        task = next((t for t in self.data["active"] if t["id"] == task_id), None)
        if not task:
            return

        old_text = task["text"]
        old_category = task.get("category", "")
        series_id = task.get("series_id")

        win = ctk.CTkToplevel(self)
        win.title("Edit Task")
        try:
            win.geometry(self.data.get("edit_task_geometry") or "440x620")
        except Exception:
            win.geometry("440x620")
        win.minsize(400, 520)
        win.transient(self)
        win.grab_set()

        def persist_edit_geometry():
            try:
                if str(win.state()) != "zoomed":
                    self.data["edit_task_geometry"] = win.geometry()
                    self.save_data()
            except Exception:
                pass

        def on_edit_close():
            persist_edit_geometry()
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_edit_close)

        ctk.CTkLabel(win, text="Edit Task", font=ctk.CTkFont(size=18, weight="bold")).pack(
            pady=(15, 10)
        )

        name_frame = ctk.CTkFrame(win, fg_color="transparent")
        name_frame.pack(fill="x", padx=20, pady=6)
        ctk.CTkLabel(name_frame, text="Task name:").pack(side="left")
        name_entry = ctk.CTkEntry(name_frame, width=280)
        name_entry.pack(side="left", padx=10)
        name_entry.insert(0, task["text"])

        cat_frame = ctk.CTkFrame(win, fg_color="transparent")
        cat_frame.pack(fill="x", padx=20, pady=6)
        ctk.CTkLabel(cat_frame, text="Header:").pack(side="left")
        init_header, init_category = self.header_and_category_labels_for_id(task.get("category", ""))
        header_labels, edit_header_map = self.build_header_picker_options()
        edit_header_var = ctk.StringVar(value=init_header if init_header in header_labels else "")
        edit_header_menu = ctk.CTkOptionMenu(
            cat_frame, values=header_labels, variable=edit_header_var, width=130
        )
        edit_header_menu.pack(side="left", padx=(8, 10))

        ctk.CTkLabel(cat_frame, text="Category:").pack(side="left")
        cat_labels, edit_category_map = self.build_category_picker_options(
            edit_header_map.get(edit_header_var.get(), "") or ""
        )
        cat_var = ctk.StringVar(value=init_category if init_category in cat_labels else "")
        edit_category_menu = ctk.CTkOptionMenu(
            cat_frame, values=cat_labels, variable=cat_var, width=160
        )
        edit_category_menu.pack(side="left", padx=8)

        def on_edit_header_changed(_value=None):
            nonlocal edit_category_map
            hid = edit_header_map.get(edit_header_var.get(), "") or ""
            labels, edit_category_map = self.build_category_picker_options(hid)
            edit_category_menu.configure(values=labels)
            if cat_var.get() not in labels:
                cat_var.set("")

        edit_header_menu.configure(command=on_edit_header_changed)

        date_frame = ctk.CTkFrame(win, fg_color="transparent")
        date_frame.pack(fill="x", padx=20, pady=6)
        ctk.CTkLabel(date_frame, text="Date (dd/mm/yy):").pack(side="left")
        date_entry = ctk.CTkEntry(date_frame, width=120)
        date_entry.pack(side="left", padx=10)

        time_frame = ctk.CTkFrame(win, fg_color="transparent")
        time_frame.pack(fill="x", padx=20, pady=6)
        ctk.CTkLabel(time_frame, text="Time (hh:mm):").pack(side="left")
        time_entry = ctk.CTkEntry(time_frame, width=80)
        time_entry.pack(side="left", padx=10)

        if task.get("due"):
            try:
                dt = datetime.fromisoformat(task["due"])
                date_entry.insert(0, dt.strftime("%d/%m/%y"))
                time_entry.insert(0, dt.strftime("%H:%M"))
            except Exception:
                pass

        cal_frame = ctk.CTkFrame(win, fg_color="transparent")
        cal_frame.pack(fill="x", padx=20, pady=4)
        # Calendar wins over Passive if both were stored historically
        start_cal = bool(task.get("show_on_calendar"))
        start_passive = bool(task.get("passive")) and not start_cal
        edit_cal_var = ctk.BooleanVar(value=start_cal)
        edit_passive_var = ctk.BooleanVar(value=start_passive)

        edit_cal_check = ctk.CTkCheckBox(
            cal_frame, text="Calendar", variable=edit_cal_var, width=90,
        )
        edit_cal_check.pack(side="left")
        edit_passive_check = ctk.CTkCheckBox(
            cal_frame, text="Passive", variable=edit_passive_var, width=80,
        )
        edit_passive_check.pack(side="left", padx=(16, 0))

        def sync_edit_activity(_event=None):
            has_date = bool((date_entry.get() or "").strip())
            cal_on = bool(edit_cal_var.get())
            pas_on = bool(edit_passive_var.get())
            if cal_on and pas_on:
                edit_passive_var.set(False)
                pas_on = False
            if not has_date:
                if cal_on:
                    edit_cal_var.set(False)
                    cal_on = False
                edit_cal_check.configure(state="disabled")
            elif pas_on:
                if cal_on:
                    edit_cal_var.set(False)
                    cal_on = False
                edit_cal_check.configure(state="disabled")
            else:
                edit_cal_check.configure(state="normal")
            if cal_on:
                edit_passive_var.set(False)
                edit_passive_check.configure(state="disabled")
            else:
                edit_passive_check.configure(state="normal")

        edit_cal_check.configure(command=sync_edit_activity)
        edit_passive_check.configure(command=sync_edit_activity)
        date_entry.bind("<KeyRelease>", sync_edit_activity)
        date_entry.bind("<FocusOut>", sync_edit_activity)
        sync_edit_activity()

        init_freq = "Once" if task.get("type") != "recurring" else (task.get("frequency") or "daily")
        if init_freq not in ("Once", "daily", "weekly", "monthly", "annually", "every X days"):
            init_freq = "Once" if task.get("type") != "recurring" else "daily"
        freq_var = ctk.StringVar(value=init_freq)
        freq_frame = ctk.CTkFrame(win, fg_color="transparent")
        freq_frame.pack(fill="x", padx=20, pady=6)
        ctk.CTkLabel(freq_frame, text="Frequency:").pack(side="left")
        ctk.CTkOptionMenu(
            freq_frame,
            values=["Once", "daily", "weekly", "monthly", "annually", "every X days"],
            variable=freq_var,
            width=140,
            command=lambda _v: sync_edit_interval(),
        ).pack(side="left", padx=10)
        interval_frame = ctk.CTkFrame(win, fg_color="transparent")
        ctk.CTkLabel(interval_frame, text="Every X days:").pack(side="left")
        interval_entry = ctk.CTkEntry(interval_frame, width=60)
        interval_entry.pack(side="left", padx=10)
        if task.get("interval"):
            interval_entry.insert(0, str(task["interval"]))

        weekday_frame = ctk.CTkFrame(win, fg_color="transparent")
        edit_weekday_vars = []
        existing_days = set()
        for d in task.get("weekdays") or []:
            try:
                existing_days.add(int(d))
            except (TypeError, ValueError):
                pass
        for i, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            var = ctk.BooleanVar(value=(i in existing_days))
            edit_weekday_vars.append(var)
            ctk.CTkCheckBox(weekday_frame, text=name, variable=var, width=52).pack(
                side="left", padx=2
            )

        anchor_frame = ctk.CTkFrame(win, fg_color="transparent")
        ctk.CTkLabel(anchor_frame, text="Due date:").pack(side="left")
        edit_anchor_var = ctk.StringVar(
            value=(task.get("due_anchor") if task.get("due_anchor") in ("sticky", "flexi") else "sticky")
        )
        ctk.CTkRadioButton(
            anchor_frame, text="Sticky", variable=edit_anchor_var, value="sticky", width=70
        ).pack(side="left", padx=(8, 4))
        ctk.CTkRadioButton(
            anchor_frame, text="Flexi", variable=edit_anchor_var, value="flexi", width=70
        ).pack(side="left", padx=4)

        def sync_edit_interval(_v=None):
            f = freq_var.get()
            if f == "every X days":
                interval_frame.pack(fill="x", padx=20, pady=4, after=freq_frame)
            else:
                interval_frame.pack_forget()
            if f == "weekly":
                weekday_frame.pack(fill="x", padx=20, pady=4, after=freq_frame)
            else:
                weekday_frame.pack_forget()
            if f in ("weekly", "monthly", "annually", "every X days"):
                after_w = weekday_frame if f == "weekly" else freq_frame
                anchor_frame.pack(fill="x", padx=20, pady=4, after=after_w)
            else:
                anchor_frame.pack_forget()

        sync_edit_interval()

        # Prerequisites
        prereq_box = ctk.CTkFrame(win)
        prereq_box.pack(fill="both", expand=True, padx=20, pady=(10, 4))
        ctk.CTkLabel(
            prereq_box,
            text="Prerequisites (must be completed first):",
            anchor="w",
        ).pack(fill="x", padx=8, pady=(6, 2))
        prereq_vars = self.build_prereq_picker(
            prereq_box,
            selected_ids=task.get("prerequisites") or [],
            exclude_id=task["id"],
        )

        def save_edit():
            new_text = name_entry.get().strip()
            if not new_text:
                messagebox.showwarning("Empty", "Task name cannot be empty.")
                return
            new_due = self.parse_datetime(date_entry.get(), time_entry.get())
            if new_due == "error":
                return
            new_category = self.resolve_header_category_pickers(
                edit_header_var.get(),
                cat_var.get(),
                edit_header_map,
                edit_category_map,
            )

            task["text"] = new_text
            task["due"] = new_due.isoformat() if new_due else None
            task["category"] = new_category
            show_cal = bool(edit_cal_var.get()) if new_due else False
            task["show_on_calendar"] = show_cal
            task["passive"] = bool(edit_passive_var.get()) and not show_cal
            task["prerequisites"] = [tid for tid, var in prereq_vars.items() if var.get()]
            new_freq = freq_var.get() or "Once"
            if new_freq == "Once":
                task["type"] = "one-off"
                task.pop("frequency", None)
                task.pop("interval", None)
                task.pop("weekdays", None)
                task.pop("due_anchor", None)
                task.pop("anchor_day", None)
                if not series_id:
                    task.pop("series_id", None)
            else:
                if not new_due:
                    messagebox.showwarning("Date required", "Recurring tasks need a due date.")
                    return
                task["type"] = "recurring"
                task["frequency"] = new_freq
                if new_freq == "every X days":
                    interval = safe_int(interval_entry.get().strip(), default=0)
                    if interval < 1:
                        messagebox.showerror("Invalid", "Enter a valid number of days")
                        return
                    task["interval"] = interval
                else:
                    task["interval"] = 1
                if new_freq == "weekly":
                    days = [i for i, v in enumerate(edit_weekday_vars) if v.get()]
                    if not days:
                        messagebox.showwarning(
                            "Weekdays",
                            "Pick at least one day of the week.",
                        )
                        return
                    task["weekdays"] = days
                else:
                    task.pop("weekdays", None)
                if new_freq in ("weekly", "monthly", "annually", "every X days"):
                    task["due_anchor"] = edit_anchor_var.get() or "sticky"
                else:
                    task.pop("due_anchor", None)
                if new_freq in ("monthly", "annually"):
                    task["anchor_day"] = new_due.day
                else:
                    task.pop("anchor_day", None)
                task["series_id"] = series_id or task["id"]

            # Propagate name/category to history for this series (or exact id for one-offs)
            for h in self.data["history"]:
                same_series = series_id and h.get("series_id") == series_id
                same_one_off = (not series_id) and h.get("id") == task["id"]
                if same_series or same_one_off:
                    h["text"] = new_text
                    h["category"] = new_category
                elif not series_id and h.get("text") == old_text and h.get("id") == task["id"]:
                    h["text"] = new_text

            self.save_data()
            if self.prereq_open:
                self.rebuild_prereq_checkboxes()
            self.refresh_list()
            try:
                if str(win.state()) != "zoomed":
                    self.data["edit_task_geometry"] = win.geometry()
                    self.save_data()
            except Exception:
                pass
            win.destroy()
            if on_saved:
                on_saved()

        ctk.CTkButton(win, text="Save Changes", command=save_edit, height=36).pack(
            pady=20, padx=20, fill="x"
        )

    def open_notes_editor(self, kind, ref_id, title_hint="", on_saved=None):
        """
        kind: 'task' | 'category'
        ref_id: task id or category id
        """
        if kind == "task":
            obj = next((t for t in self.data.get("active", []) if t.get("id") == ref_id), None)
            if not obj:
                return
            get_notes = lambda: obj.get("notes") or ""
            def set_notes(text):
                obj["notes"] = text
        elif kind == "category":
            obj = self.get_category(ref_id)
            if not obj:
                return
            get_notes = lambda: obj.get("notes") or ""
            def set_notes(text):
                obj["notes"] = text
        else:
            return

        win = ctk.CTkToplevel(self)
        title = title_hint or (obj.get("text") if kind == "task" else obj.get("name")) or "Notes"
        win.title(f"Notes — {title}")
        try:
            win.geometry(self.data.get("notes_geometry") or "480x360")
        except Exception:
            win.geometry("480x360")
        win.minsize(320, 220)
        win.transient(self)

        def persist_geo():
            try:
                if str(win.state()) != "zoomed":
                    self.data["notes_geometry"] = win.geometry()
            except Exception:
                pass

        def close():
            persist_geo()
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close)

        ctk.CTkLabel(
            win, text=f"📝  {title}", font=ctk.CTkFont(size=16, weight="bold"), anchor="w"
        ).pack(fill="x", padx=14, pady=(12, 6))

        box = ctk.CTkTextbox(win, wrap="word")
        box.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        box.insert("1.0", get_notes())

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(0, 12))

        def save_and_close():
            raw = box.get("1.0", "end-1c")
            # Strip trailing whitespace; keep body as user typed
            body = raw.rstrip()
            if body:
                stamp = datetime.now().strftime("%d/%m/%y")
                # Avoid stacking the same day's stamp if they re-save unchanged header
                first_line = body.splitlines()[0].strip() if body else ""
                if first_line != stamp and not first_line.startswith(stamp + " "):
                    body = f"{stamp}\n{body}"
                elif first_line.startswith(stamp + " ") and not body.startswith(stamp + "\n"):
                    # already has date inline on first line — leave as-is
                    pass
            set_notes(body)
            persist_geo()
            self.save_data()
            self.refresh_list()
            win.destroy()
            if on_saved:
                on_saved()

        ctk.CTkButton(btn_row, text="Save", width=100, command=save_and_close).pack(side="right")
        ctk.CTkButton(btn_row, text="Cancel", width=100, fg_color="gray", command=close).pack(
            side="right", padx=8
        )
        self.enable_double_click_maximize(win, btn_row)

    def open_materials_window(self):
        """Two-pane freeform notepad: Me | Others. Saved into the todo document."""
        prev = getattr(self, "_materials_win", None)
        if prev is not None:
            try:
                if prev.winfo_exists():
                    prev.lift()
                    prev.focus_force()
                    return
            except Exception:
                pass

        mats = self.data.get("materials")
        if not isinstance(mats, dict):
            mats = {"me": "", "others": ""}
            self.data["materials"] = mats
        mats.setdefault("me", "")
        mats.setdefault("others", "")

        win = ctk.CTkToplevel(self)
        self._materials_win = win
        win.title("Materials")
        try:
            win.configure(fg_color="#1a1b1e")
        except Exception:
            pass
        geo = (self.data.get("materials_geometry") or "900x560").strip() or "900x560"
        want_max = bool(self.data.get("materials_maximized"))
        try:
            win.geometry(geo)
        except Exception:
            win.geometry("900x560")
            geo = "900x560"
        win.minsize(520, 360)
        win.transient(self)
        win._mat_last_normal = geo

        def _is_zoomed():
            try:
                if str(win.state()) == "zoomed":
                    return True
            except Exception:
                pass
            try:
                return bool(win.attributes("-zoomed"))
            except Exception:
                return False

        def _set_zoomed(flag):
            try:
                win.state("zoomed" if flag else "normal")
                return
            except Exception:
                pass
            try:
                win.attributes("-zoomed", bool(flag))
            except Exception:
                pass

        if want_max:
            win.after(40, lambda: _set_zoomed(True))

        header = ctk.CTkFrame(win, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(
            header,
            text="Scratch notes — auto-saves when you close",
            text_color="gray",
        ).pack(side="left")
        self.enable_double_click_maximize(win, header)

        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(body, text="Me", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=(4, 8), pady=(0, 4)
        )
        ctk.CTkLabel(body, text="Others", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=1, sticky="w", padx=(8, 4), pady=(0, 4)
        )

        me_box = ctk.CTkTextbox(body, wrap="word")
        me_box.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        others_box = ctk.CTkTextbox(body, wrap="word")
        others_box.grid(row=1, column=1, sticky="nsew", padx=(6, 0))

        me_box.insert("1.0", mats.get("me") or "")
        others_box.insert("1.0", mats.get("others") or "")

        save_after = {"id": None}

        def schedule_save(_event=None):
            aid = save_after.get("id")
            if aid is not None:
                try:
                    win.after_cancel(aid)
                except Exception:
                    pass
            save_after["id"] = win.after(500, persist_text)

        def persist_text():
            save_after["id"] = None
            try:
                self.data.setdefault("materials", {})
                self.data["materials"]["me"] = me_box.get("1.0", "end-1c")
                self.data["materials"]["others"] = others_box.get("1.0", "end-1c")
                self.save_data()
            except Exception:
                pass

        me_box.bind("<KeyRelease>", schedule_save)
        others_box.bind("<KeyRelease>", schedule_save)

        def on_close():
            try:
                persist_text()
                if not _is_zoomed():
                    win.update_idletasks()
                    self.data["materials_geometry"] = win.geometry()
                    win._mat_last_normal = self.data["materials_geometry"]
                else:
                    self.data["materials_maximized"] = True
                    if getattr(win, "_mat_last_normal", None):
                        self.data["materials_geometry"] = win._mat_last_normal
                self.data["materials_maximized"] = bool(_is_zoomed())
                if not self.data["materials_maximized"] and not _is_zoomed():
                    try:
                        self.data["materials_geometry"] = win.geometry()
                    except Exception:
                        pass
                self.save_data(immediate=True)
            except Exception:
                pass
            try:
                if getattr(self, "_materials_win", None) is win:
                    self._materials_win = None
            except Exception:
                pass
            win.destroy()

        def track_geo(_event=None):
            if _is_zoomed():
                return
            try:
                g = win.geometry()
                if g and "x" in g:
                    win._mat_last_normal = g
            except Exception:
                pass

        win.bind("<Configure>", track_geo, add="+")
        win.protocol("WM_DELETE_WINDOW", on_close)

    def open_add_calendar_popup(self, day):
        """Quick-add a Calendar task for a clicked day: name + optional HH:MM."""
        if day is None:
            return
        date_label = day.strftime("%d/%m/%y")
        weekday = day.strftime("%A")

        win = ctk.CTkToplevel(self)
        win.title("Add Calendar")
        try:
            win.geometry(self.data.get("add_calendar_geometry") or "360x220")
        except Exception:
            win.geometry("360x220")
        win.minsize(320, 200)
        win.transient(self)
        win.grab_set()
        try:
            win.focus_force()
        except Exception:
            pass

        ctk.CTkLabel(
            win,
            text=f"Adding to {weekday} {date_label}",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(16, 8))

        ctk.CTkLabel(win, text="Name").pack(anchor="w", padx=16)
        name_entry = ctk.CTkEntry(win, placeholder_text="What's on this day?")
        name_entry.pack(fill="x", padx=16, pady=(2, 10))
        name_entry.focus_set()

        ctk.CTkLabel(win, text="Time (optional, HH:MM)").pack(anchor="w", padx=16)
        time_entry = ctk.CTkEntry(win, placeholder_text="e.g. 14:30")
        time_entry.pack(fill="x", padx=16, pady=(2, 12))

        hint = ctk.CTkLabel(
            win,
            text="Saved as Calendar · ★★★ · no category",
            text_color="gray",
        )
        hint.pack(anchor="w", padx=16, pady=(0, 8))

        def persist_geometry():
            try:
                win.update_idletasks()
                self.data["add_calendar_geometry"] = win.geometry()
            except Exception:
                pass

        def close_popup():
            persist_geometry()
            self.save_data()
            try:
                win.destroy()
            except Exception:
                pass

        def submit(_event=None):
            text = (name_entry.get() or "").strip()
            if not text:
                messagebox.showwarning("Empty", "Please enter a name.", parent=win)
                return
            time_str = (time_entry.get() or "").strip()
            hour, minute = 0, 0
            if time_str:
                try:
                    parsed = datetime.strptime(time_str, "%H:%M")
                    hour, minute = parsed.hour, parsed.minute
                except ValueError:
                    messagebox.showerror(
                        "Invalid Time",
                        "Please use format HH:MM (e.g. 09:30).",
                        parent=win,
                    )
                    return
            due = datetime(day.year, day.month, day.day, hour, minute, 0)
            task = {
                "id": str(uuid.uuid4()),
                "text": text,
                "type": "one-off",
                "category": "",
                "due": due.isoformat(),
                "created": now_iso(),
                "prerequisites": [],
                "show_on_calendar": True,
                "priority": 3,
                "passive": False,
                "notes": "",
            }
            task["star_order"] = self._next_star_order(3)
            self.data.setdefault("active", []).append(task)
            persist_geometry()
            self.save_data()
            try:
                win.destroy()
            except Exception:
                pass
            self.refresh_list()

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkButton(btns, text="Cancel", width=90, command=close_popup).pack(side="right")
        ctk.CTkButton(btns, text="Add", width=90, command=submit).pack(side="right", padx=(0, 8))
        win.bind("<Return>", submit)
        win.protocol("WM_DELETE_WINDOW", close_popup)

