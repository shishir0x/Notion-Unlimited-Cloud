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


def _normalize_android_path(p: str) -> str:
    """Normalize any Android file path into canonical ADB format."""
    clean = p.replace("\\", "/").strip()
    if clean.startswith("/sdcard/"):
        return "/storage/emulated/0/" + clean[len("/sdcard/"):]
    if "Internal shared storage" in clean:
        rel = clean.split("Internal shared storage")[-1].lstrip("/")
        return f"/storage/emulated/0/{rel}"
    if "Internal Storage" in clean:
        rel = clean.split("Internal Storage")[-1].lstrip("/")
        return f"/storage/emulated/0/{rel}"
    if "SD card" in clean or "SD Card" in clean:
        rel = clean.replace("This PC/OnePlus Nord CE4/SD card", "").replace("SD card", "").replace("SD Card", "").lstrip("/")
        return f"/storage/4A21-0000/{rel}"
    return clean


def load_state() -> Dict[str, Any]:
    """
    Load the sync state index from disk.

    Returns a dict with sub-dicts:
      - "files"           : local PC files  {path_str -> {notion_id, mtime, size}}
      - "android_files"   : Android files   {adb_path  -> {notion_id, mtime, size}}
      - "folders"         : local folder cache {path -> {notion_id, mtime, file_count}}
      - "android_folders" : Android folder cache {adb_path -> {notion_id, mtime, file_count}}
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
                    raw_af = data.setdefault("android_files", {})
                    # Auto-normalize all Android keys to canonical ADB paths
                    norm_af = {}
                    for k, v in raw_af.items():
                        norm_af[_normalize_android_path(k)] = v
                    data["android_files"] = norm_af
                    data.setdefault("folders", {})
                    data.setdefault("android_folders", {})  # CRITICAL: ensure bucket exists
                    data.setdefault("last_sync", None)
                    data.setdefault("sync_queue", [])  # persistent sync queue
                    return data
            except Exception:
                pass
    return {"files": {}, "android_files": {}, "folders": {}, "android_folders": {}, "last_sync": None, "sync_queue": []}


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


def record_folder(
    state: Dict[str, Any],
    path: str,
    notion_id: str,
    mtime: float,
    file_count: int = 0,
    android: bool = False,
):
    """Record a successfully synced folder into the state dict."""
    bucket = "android_folders" if android else "folders"
    state.setdefault(bucket, {})
    state[bucket][path] = {
        "notion_id": notion_id,
        "mtime": mtime,
        "file_count": file_count,
        "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def check_folder(
    state: Dict[str, Any],
    path: str,
    mtime: float,
    file_count: int,
    android: bool = False,
) -> Optional[str]:
    """
    Check whether a folder needs syncing.

    Returns:
      - None            → folder is already synced to Notion (up-to-date, skip upload)
      - "new"           → folder has never been synced
    """
    bucket = "android_folders" if android else "folders"
    folders = state.get(bucket, {})
    prev = folders.get(path)
    if prev is not None and prev.get("notion_id"):
        return None  # Already in Notion database, do not re-upload
    return "new"


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
