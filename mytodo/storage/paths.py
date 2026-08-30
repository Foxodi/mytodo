"""Local filesystem path helpers for desktop builds."""
from __future__ import annotations

import os
import sys


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # Prefer package parent (artifacts/) when running from source layout
    here = os.path.dirname(os.path.abspath(__file__))
    # mytodo/storage -> mytodo -> parent of package
    pkg = os.path.dirname(here)
    parent = os.path.dirname(pkg)
    return parent if parent else pkg


def resolve_data_file(filename: str = "tasks.json") -> str:
    """Prefer tasks.json next to the app; fall back to LocalAppData if not writable."""
    primary_dir = app_dir()
    primary = os.path.join(primary_dir, filename)
    if os.path.isfile(primary):
        return primary
    probe = os.path.join(primary_dir, ".todo_write_test")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return primary
    except OSError:
        pass
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    fallback_dir = os.path.join(base, "MyTodoList")
    try:
        os.makedirs(fallback_dir, exist_ok=True)
    except OSError:
        fallback_dir = primary_dir
    return os.path.join(fallback_dir, filename)


def set_file_hidden(path: str, hidden: bool = True) -> None:
    """Keep tasks.json hidden on Windows after atomic rewrite."""
    if os.name != "nt" or not path:
        return
    try:
        import ctypes
        from ctypes import wintypes

        FILE_ATTRIBUTE_HIDDEN = 0x2
        INVALID = 0xFFFFFFFF
        GetFileAttributesW = ctypes.windll.kernel32.GetFileAttributesW
        SetFileAttributesW = ctypes.windll.kernel32.SetFileAttributesW
        GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
        GetFileAttributesW.restype = wintypes.DWORD
        SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        SetFileAttributesW.restype = wintypes.BOOL

        attrs = GetFileAttributesW(str(path))
        if attrs == INVALID:
            return
        if hidden:
            new_attrs = attrs | FILE_ATTRIBUTE_HIDDEN
        else:
            new_attrs = attrs & ~FILE_ATTRIBUTE_HIDDEN
        if new_attrs != attrs:
            SetFileAttributesW(str(path), new_attrs)
    except Exception:
        pass
