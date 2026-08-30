"""Abstract persistence for the todo document.

Desktop V1 uses JsonFileStore. V2 can add ApiStore / SyncStore without
rewriting UI code — inject a different TodoStore into the app.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional


WarnFn = Callable[[str, str], None]  # (title, message)


class TodoStore(ABC):
    """Load/save the full todo document (dict matching DATA_VERSION schema)."""

    @abstractmethod
    def load(self) -> dict:
        """Return a migrated, normalized document. Never raises for missing data."""

    @abstractmethod
    def save(self, data: dict) -> None:
        """Persist document. May raise OSError on hard failure."""

    def close(self) -> None:
        """Optional cleanup (DB connections, HTTP sessions)."""
        return None
