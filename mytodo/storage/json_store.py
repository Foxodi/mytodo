"""Local JSON document store (desktop V1).

Implements TodoStore. Swap for an API-backed store in V2 without changing UI.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from mytodo.domain.constants import DATA_VERSION
from mytodo.storage.migrate import default_data, migrate_data
from mytodo.storage.paths import resolve_data_file, set_file_hidden
from mytodo.storage.protocol import TodoStore, WarnFn


class JsonFileStore(TodoStore):
    def __init__(
        self,
        path: Optional[str] = None,
        on_warn: Optional[WarnFn] = None,
    ):
        self.path = path or resolve_data_file()
        self.on_warn = on_warn
        if os.path.isfile(self.path):
            set_file_hidden(self.path, True)

    def load(self) -> dict:
        if not os.path.exists(self.path):
            return default_data()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            if self.on_warn:
                self.on_warn(
                    "Data file",
                    "Could not read tasks.json — starting with an empty list.\n"
                    "Your old file was left on disk.",
                )
            return default_data()
        return migrate_data(data)

    def save(self, data: dict) -> None:
        data = data if isinstance(data, dict) else default_data()
        data["version"] = DATA_VERSION
        path = self.path
        tmp = path + ".tmp"
        dump_kwargs = dict(ensure_ascii=False, separators=(",", ":"))
        try:
            folder = os.path.dirname(path) or "."
            os.makedirs(folder, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, **dump_kwargs)
            os.replace(tmp, path)
            set_file_hidden(path, True)
            self.path = path
        except OSError as e:
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            fallback = os.path.join(base, "MyTodoList", "tasks.json")
            try:
                os.makedirs(os.path.dirname(fallback), exist_ok=True)
                with open(fallback + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(data, f, **dump_kwargs)
                os.replace(fallback + ".tmp", fallback)
                set_file_hidden(fallback, True)
                self.path = fallback
                if self.on_warn:
                    self.on_warn(
                        "Saved to AppData",
                        f"Could not write next to the app:\n{path}\n\n"
                        f"Saved instead to:\n{fallback}\n\n({e})",
                    )
            except OSError as e2:
                raise OSError(
                    f"Could not save tasks.json. Tried:\n{path}\n{fallback}\n\n{e2}"
                ) from e2
