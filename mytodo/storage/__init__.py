"""Persistence backends.

V1: JsonFileStore (local tasks.json)
V2: inject ApiStore / SyncStore implementing TodoStore — same load/save contract.
"""
from mytodo.storage.protocol import TodoStore
from mytodo.storage.json_store import JsonFileStore
from mytodo.storage.api_store import ApiStore, load_online_config

__all__ = ["TodoStore", "JsonFileStore", "ApiStore", "load_online_config"]
