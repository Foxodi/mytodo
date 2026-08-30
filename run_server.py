"""Host the V2 API + mobile web app.

  pip install -r requirements-online.txt
  python run_server.py

Then on this machine: http://127.0.0.1:8741
On your phone (same wifi): http://<this-pc-ip>:8741
"""
from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    host = os.environ.get("MYTODO_HOST", "0.0.0.0")
    port = int(os.environ.get("MYTODO_PORT", "8741"))
    uvicorn.run("mytodo.server.app:app", host=host, port=port, reload=False)
