"""Shared constants for My Todo List.

UI-free. Safe to import from desktop, mobile, or server workers.
"""
from __future__ import annotations

# Bump when the on-disk / API document shape changes.
DATA_VERSION = 9

# Focus-in overdue scan cooldown (seconds)
OVERDUE_FOCUS_COOLDOWN_SEC = 60

# Debounce for local file writes (ms) — UI may use; remote stores may ignore
SAVE_DEBOUNCE_MS = 400
