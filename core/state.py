"""
core/state.py — Git-style persistent sync state.

Reads/writes .notion_sync_state.json which tracks every synced file's
mtime, size, and Notion page ID. This is what makes incremental sync work:
only files whose mtime or size changed since last sync are re-uploaded.
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import STATE_FILE


def load_state() -> Dict[str, Any]:
    """
    Load the sync state index from disk.

    Returns a dict with three sub-dicts:
      - "files"         : local PC files  {path_str -> {notion_id, mtime, size}}
      - "android_files" : Android files   {adb_path  -> {notion_id, mtime, size}}
      - "folders"       : Notion folder cache {(name, parent_id) -> notion_id}
    """
    search = [
        STATE_FILE,
        Path.home() / ".notion_sync_state.json",
        Path.cwd() / ".notion_sync_state.json",
    ]
    for sp in search:
        if sp.exists():
            try:
                with open(sp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("files", {})
                    data.setdefault("android_files", {})
                    data.setdefault("folders", {})
                    data.setdefault("last_sync", None)
                    return data
            except Exception:
                pass
    return {"files": {}, "android_files": {}, "folders": {}, "last_sync": None}


def save_state(state: Dict[str, Any]):
    """Persist the state dict to disk atomically (write → rename)."""
    state["last_sync"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    paths = [STATE_FILE, Path.home() / ".notion_sync_state.json"]
    for sp in paths:
        tmp = sp.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            tmp.replace(sp)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass


def record_file(
    state: Dict[str, Any],
    path: str,
    notion_id: str,
    mtime: float,
    size: int,
    android: bool = False,
):
    """Record a successfully synced file into the state dict."""
    bucket = "android_files" if android else "files"
    state[bucket][path] = {
        "notion_id": notion_id,
        "mtime": mtime,
        "size": size,
        "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def check_file(
    state: Dict[str, Any],
    path: str,
    mtime: float,
    size: int,
    android: bool = False,
) -> Optional[str]:
    """
    Check whether a file needs syncing.

    Returns:
      - None            → file is up-to-date, skip it
      - "new"           → file has never been synced
      - "modified"      → file changed since last sync (returns existing notion_id via state)
    """
    bucket = "android_files" if android else "files"
    prev = state[bucket].get(path)
    if prev is None:
        return "new"
    if abs(prev.get("mtime", 0) - mtime) > 1.0 or prev.get("size", 0) != size:
        return "modified"
    return None  # up-to-date


def get_notion_id(
    state: Dict[str, Any], path: str, android: bool = False
) -> Optional[str]:
    """Retrieve the Notion page ID for a previously synced file."""
    bucket = "android_files" if android else "files"
    return state[bucket].get(path, {}).get("notion_id")
