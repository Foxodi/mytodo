"""Remote TodoStore — talks to the hosted document API."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional

from mytodo.storage.migrate import default_data, migrate_data
from mytodo.storage.protocol import TodoStore, WarnFn


class ApiStore(TodoStore):
    def __init__(
        self,
        base_url: str,
        token: str,
        on_warn: Optional[WarnFn] = None,
        timeout: float = 20.0,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.on_warn = on_warn
        self.timeout = timeout
        self._rev = 0

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        url = self.base_url + path
        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            data = raw
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read().decode("utf-8")
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise OSError(f"API {method} {path} failed ({e.code}): {detail}") from e
        except urllib.error.URLError as e:
            raise OSError(f"Could not reach API at {self.base_url}: {e.reason}") from e

    def load(self) -> dict:
        try:
            payload = self._request("GET", "/api/document")
        except OSError as e:
            if self.on_warn:
                self.on_warn("Online store", str(e))
            return default_data()
        self._rev = int(payload.get("rev") or 0)
        doc = payload.get("document")
        if not isinstance(doc, dict):
            return default_data()
        return migrate_data(doc)

    def save(self, data: dict) -> None:
        try:
            payload = self._request(
                "PUT",
                "/api/document",
                {"rev": self._rev, "document": data},
            )
        except OSError as e:
            if "409" in str(e):
                # Someone else saved — force this desktop snapshot so we don't stall
                payload = self._request(
                    "PUT",
                    "/api/document",
                    {"rev": self._rev, "document": data, "force": True},
                )
                if self.on_warn:
                    self.on_warn(
                        "Online sync",
                        "Server had a newer copy; your desktop save was written over it.\n"
                        "Reload if a phone edit disappeared.",
                    )
            else:
                raise
        self._rev = int(payload.get("rev") or self._rev)


def load_online_config() -> Optional[dict]:
    """Env wins, then mytodo.online.json next to the process."""
    url = (os.environ.get("MYTODO_API_URL") or "").strip()
    token = (os.environ.get("MYTODO_TOKEN") or "").strip()
    if url and token:
        return {"api_url": url, "token": token}
    from mytodo.storage.paths import resolve_data_file
    cfg_path = resolve_data_file("mytodo.online.json")
    if not os.path.isfile(cfg_path):
        # also look beside tasks.json name swap
        alt = os.path.join(os.path.dirname(cfg_path), "mytodo.online.json")
        if os.path.isfile(alt):
            cfg_path = alt
        else:
            return None
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    url = (cfg.get("api_url") or "").strip()
    token = (cfg.get("token") or "").strip()
    if url and token:
        return {"api_url": url, "token": token}
    return None


def choose_store(on_warn=None):
    """ApiStore when online config exists, otherwise local JSON."""
    from mytodo.storage.json_store import JsonFileStore

    cfg = load_online_config()
    if cfg:
        return ApiStore(cfg["api_url"], cfg["token"], on_warn=on_warn)
    return JsonFileStore(on_warn=on_warn)
