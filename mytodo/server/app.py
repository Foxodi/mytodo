"""FastAPI host: one shared document + mobile PWA."""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from mytodo.domain.actions import add_task, complete_or_skip, delete_task
from mytodo.domain.constants import DATA_VERSION
from mytodo.storage.migrate import default_data, migrate_data

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _data_path() -> Path:
    raw = os.environ.get("MYTODO_DATA_PATH")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[2] / "server_data" / "tasks.json"


def _token() -> str:
    tok = (os.environ.get("MYTODO_TOKEN") or "").strip()
    if tok:
        return tok
    gen_path = _data_path().parent / "token.txt"
    if gen_path.is_file():
        return gen_path.read_text(encoding="utf-8").strip()
    gen_path.parent.mkdir(parents=True, exist_ok=True)
    created = secrets.token_urlsafe(24)
    gen_path.write_text(created + "\n", encoding="utf-8")
    print(f"\nMy Todo List token (saved to {gen_path}):\n  {created}\n")
    return created


class Store:
    def __init__(self):
        self.path = _data_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rev = 0
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.rev = int(raw.get("_rev") or 0)
                self.doc = migrate_data(raw.get("document") if "document" in raw else raw)
            except (OSError, json.JSONDecodeError, TypeError):
                self.doc = default_data()
        else:
            self.doc = default_data()

    def persist(self):
        self.rev += 1
        blob = {"_rev": self.rev, "document": self.doc}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(blob, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.path)

    def snapshot(self) -> dict:
        return {"rev": self.rev, "version": DATA_VERSION, "document": self.doc}


store = Store()
TOKEN = _token()

app = FastAPI(title="My Todo List API", version="2.0")


def require_auth(authorization: Optional[str]):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    got = authorization.split(" ", 1)[1].strip()
    if got != TOKEN:
        raise HTTPException(status_code=401, detail="Bad token")


class DocumentPut(BaseModel):
    rev: int = 0
    document: dict
    force: bool = False


class ActionBody(BaseModel):
    op: str
    task_id: Optional[str] = None
    status: Optional[str] = None
    task: Optional[dict] = None


@app.get("/api/health")
def health():
    return {"ok": True, "rev": store.rev, "version": DATA_VERSION}


@app.get("/api/document")
def get_document(authorization: Optional[str] = Header(default=None)):
    require_auth(authorization)
    return store.snapshot()


@app.put("/api/document")
def put_document(body: DocumentPut, authorization: Optional[str] = Header(default=None)):
    require_auth(authorization)
    if not body.force and body.rev != store.rev:
        raise HTTPException(
            status_code=409,
            detail=f"Revision mismatch (client {body.rev}, server {store.rev}). Reload and retry, or force=true.",
        )
    if not isinstance(body.document, dict):
        raise HTTPException(status_code=400, detail="document must be an object")
    store.doc = migrate_data(body.document)
    store.persist()
    return store.snapshot()


@app.post("/api/action")
def run_action(body: ActionBody, authorization: Optional[str] = Header(default=None)):
    require_auth(authorization)
    op = (body.op or "").lower()
    try:
        if op in ("complete", "skip"):
            status = "completed" if op == "complete" else (body.status or "skipped")
            if not body.task_id:
                raise ValueError("task_id required")
            if not complete_or_skip(store.doc, body.task_id, status if op == "skip" else "completed"):
                raise HTTPException(status_code=404, detail="Task not found")
        elif op == "add":
            add_task(store.doc, body.task or {})
        elif op == "delete":
            if not body.task_id or not delete_task(store.doc, body.task_id):
                raise HTTPException(status_code=404, detail="Task not found")
        else:
            raise HTTPException(status_code=400, detail=f"Unknown op '{body.op}'")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    store.persist()
    return store.snapshot()


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
