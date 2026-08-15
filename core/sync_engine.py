"""
core/sync_engine.py — Git-style differential sync engine.

This is the core of the entire application. It:
  1. Scans source files (local or Android)
  2. Compares against the persistent state index
  3. Uploads ONLY new or modified files to Notion
  4. Skips unchanged files instantly (0 API calls)
  5. Saves progress after every file so sync is always resumable
"""

import os
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from core import filters as F
from core.notion_api import NotionAPI
from core import state as S

# (current: int, total: int, file_item: Optional[FileItem], status_tag: str) → None
ProgressCallback = Callable[[int, int, Any, str], None]


@dataclass
class FileItem:
    """A single file discovered during a scan."""
    path: str          # Absolute local path (Windows) or ADB path (Android)
    name: str          # Filename only
    size: int          # Bytes
    mtime: float       # Unix timestamp
    ext: str           # e.g. ".pdf"
    parent_path: str   # Parent directory path / Android parent ADB path
    is_android: bool = False
    display_path: str = ""     # Human-readable Windows-style path for display
    status_tag: str = ""       # "NEW" | "MODIFIED" | "" (filled by diff step)
    existing_notion_id: Optional[str] = None


@dataclass
class SyncResult:
    uploaded: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    total_scanned: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Local file scanner
# ─────────────────────────────────────────────────────────────────────────────

def scan_local(root: str) -> Generator[FileItem, None, None]:
    """
    Walk a local directory tree and yield FileItem for every non-ignored file.
    Excludes system dirs, temp files, and known noise using core/filters.py.
    """
    root_path = Path(root)
    if not root_path.exists():
        return

    for dirpath, dirs, files in os.walk(root_path):
        # Prune ignored directories in-place so os.walk won't descend into them
        dirs[:] = [
            d for d in dirs
            if not F.should_ignore_dir(d) and not d.startswith(".")
        ]

        for fname in files:
            fpath = Path(dirpath) / fname
            if F.should_ignore_file(fpath):
                continue
            try:
                st = fpath.stat()
                yield FileItem(
                    path=str(fpath),
                    name=fname,
                    size=st.st_size,
                    mtime=st.st_mtime,
                    ext=fpath.suffix.lower(),
                    parent_path=str(fpath.parent),
                    is_android=False,
                    display_path=str(fpath),
                )
            except OSError:
                continue


# ─────────────────────────────────────────────────────────────────────────────
# Android / ADB scanner
# ─────────────────────────────────────────────────────────────────────────────

def scan_android(
    device_id: str,
    adb_root: str,
    win_label: str,
) -> Generator[FileItem, None, None]:
    """
    Scan an Android storage path via ADB and yield FileItem for every file.
    Uses a single `find … stat` command for maximum speed.

    adb_root  : Linux path e.g. "/storage/emulated/0"
    win_label : Windows label for display e.g. "This PC\\OnePlus Nord CE4\\Internal shared storage"
    """
    cmd = (
        f"find '{adb_root}' "
        f"-name '.*' -prune -o "
        f"-path '{adb_root}/Android' -prune -o "
        f"-path '*/LOST.DIR' -prune -o "
        f"-path '*/.trash' -prune -o "
        f"-type f -exec stat -c '%n|%s|%Y' {{}} + 2>/dev/null"
    )
    try:
        proc = subprocess.run(
            ["adb", "-s", device_id, "shell", cmd],
            capture_output=True, text=True, errors="ignore", timeout=60,
        )
    except Exception:
        return

    for line in proc.stdout.splitlines():
        if "|" not in line:
            continue
        parts = line.strip().split("|")
        if len(parts) != 3:
            continue

        fpath_adb, raw_size, raw_mtime = parts
        if F.should_ignore_android_path(fpath_adb):
            continue

        fname = fpath_adb.split("/")[-1]
        ext = "." + fname.rsplit(".", 1)[-1].lower() if "." in fname else ""

        if any(fname.lower().startswith(p) for p in F.IGNORED_PREFIXES):
            continue
        if ext in F.IGNORED_EXTENSIONS:
            continue

        try:
            size = int(raw_size)
            mtime = float(raw_mtime)
        except ValueError:
            continue

        # Build Windows-style display path
        rel = fpath_adb.replace(adb_root, "").replace("/", "\\").lstrip("\\")
        display = f"{win_label}\\{rel}"
        parent_adb = "/".join(fpath_adb.split("/")[:-1])

        yield FileItem(
            path=fpath_adb,
            name=fname,
            size=size,
            mtime=mtime,
            ext=ext,
            parent_path=parent_adb,
            is_android=True,
            display_path=display,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Differential computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_diff(
    items: List[FileItem],
    state: Dict[str, Any],
) -> Tuple[List[FileItem], int]:
    """
    Compare scanned items against the state index.
    Populates each item's status_tag ("NEW" | "MODIFIED") and existing_notion_id.
    Returns (items_to_sync, skipped_count).
    """
    to_sync: List[FileItem] = []
    skipped = 0

    for item in items:
        change = S.check_file(
            state, item.path, item.mtime, item.size, android=item.is_android
        )
        if change is None:
            skipped += 1
            continue
        item.status_tag = "NEW" if change == "new" else "MODIFIED"
        if change == "modified":
            item.existing_notion_id = S.get_notion_id(
                state, item.path, android=item.is_android
            )
        to_sync.append(item)

    return to_sync, skipped


# ─────────────────────────────────────────────────────────────────────────────
# Notion folder path builder
# ─────────────────────────────────────────────────────────────────────────────

def _get_local_folder_parts(file_path: str) -> Tuple[str, str, List[str]]:
    """
    For a local file path, return (root_name, emoji, relative_parts).
    e.g. "C:\\Users\\nitro\\Documents\\report.pdf"
      → ("Local Disk (C:)", "💽", ["Users", "nitro", "Documents"])
    """
    p = Path(file_path)
    drive = p.drive.upper().rstrip(":")   # "C"
    root_name = f"Local Disk ({drive}:)" if drive else "Local Disk (C:)"
    # Parts after the drive root: ("C:\\", "Users", "nitro", ..., "report.pdf")
    # Skip drive root [0] and filename [-1]
    rel_parts = list(p.parts[1:-1])
    return root_name, "💽", rel_parts


def _get_android_folder_parts(
    item: FileItem,
    adb_root: str,
    container_name: str,
    container_emoji: str,
) -> Tuple[str, str, List[str]]:
    """
    For an Android file, return (root_name, emoji, relative_parts).
    """
    parent_adb = item.parent_path
    rel = parent_adb.replace(adb_root, "").lstrip("/")
    parts = [p for p in rel.split("/") if p]
    return container_name, container_emoji, parts


def ensure_notion_path(
    api: NotionAPI,
    item: FileItem,
    adb_root: Optional[str] = None,
    container_name: Optional[str] = None,
    container_emoji: str = "📱",
    server_port: int = 8765,
) -> Optional[str]:
    """
    Ensure all parent folders exist in Notion for the given file item.
    Returns the Notion ID of the deepest parent folder.
    """
    if item.is_android and adb_root and container_name:
        root_name, root_emoji, rel_parts = _get_android_folder_parts(
            item, adb_root, container_name, container_emoji
        )
    else:
        root_name, root_emoji, rel_parts = _get_local_folder_parts(item.path)

    # Ensure root container (Favorite=True so it shows in My Drive)
    root_id = api.ensure_folder(root_name, None, emoji=root_emoji, is_root=True)
    if not root_id:
        return None

    # Build the path down to the immediate parent folder
    return api.build_folder_path(rel_parts, root_id)


# ─────────────────────────────────────────────────────────────────────────────
# File upload to Notion
# ─────────────────────────────────────────────────────────────────────────────

def upload_file(
    api: NotionAPI,
    state: Dict[str, Any],
    item: FileItem,
    parent_notion_id: Optional[str],
    server_port: int = 8765,
) -> bool:
    """
    Upload a single FileItem to Notion (POST for NEW, PATCH for MODIFIED).
    Updates the state dict on success.
    Returns True on success.
    """
    ftype, emoji = F.classify_file(item.ext)
    size_mb = round(item.size / (1024 * 1024), 4)

    # URL to open/view the file via the local web server
    encoded_path = urllib.parse.quote(item.path)
    view_url = f"http://127.0.0.1:{server_port}/view?path={encoded_path}"

    display = item.display_path or item.path

    if item.status_tag == "MODIFIED" and item.existing_notion_id:
        # Update existing Notion page (no duplicate created)
        ok = api.update_page(
            item.existing_notion_id,
            {
                "File Size": {"number": size_mb},
                "Open in Browser": {"url": view_url},
                "Description": {"rich_text": [{"text": {"content": f"Path: {display} (Updated)"}}]},
            },
        )
        if ok:
            S.record_file(
                state, item.path, item.existing_notion_id,
                item.mtime, item.size, android=item.is_android,
            )
        return ok

    else:
        # Create new Notion page
        props: Dict[str, Any] = {
            "Name": {"title": [{"text": {"content": item.name}}]},
            "Type": {"select": {"name": "File"}},
            "File Type": {"select": {"name": ftype}},
            "File Extension": {"rich_text": [{"text": {"content": item.ext}}]},
            "File Size": {"number": size_mb},
            "Open in Browser": {"url": view_url},
            "Description": {"rich_text": [{"text": {"content": f"Path: {display}"}}]},
            "Favorite": {"checkbox": False},
        }
        if parent_notion_id:
            props["Parent Folder"] = {"relation": [{"id": parent_notion_id}]}

        notion_id = api.create_page(props, icon_emoji=emoji)
        if notion_id:
            S.record_file(
                state, item.path, notion_id,
                item.mtime, item.size, android=item.is_android,
            )
            try:
                import notion_server
                notion_server.register_drive_cache_item(
                    notion_id, item.name, "File", item.ext,
                    size_mb, int(item.size), parent_notion_id,
                    item.path, item.mtime
                )
            except Exception:
                pass
        return notion_id is not None


# ─────────────────────────────────────────────────────────────────────────────
# High-level sync runner
# ─────────────────────────────────────────────────────────────────────────────

def run_sync(
    api: NotionAPI,
    state: Dict[str, Any],
    items: List[FileItem],
    on_progress: Optional[ProgressCallback] = None,
    cancel_flag: Optional[Callable[[], bool]] = None,
    server_port: int = 8765,
    # Android-specific (only needed when items are Android files)
    adb_root: Optional[str] = None,
    container_name: Optional[str] = None,
    container_emoji: str = "📱",
) -> SyncResult:
    """
    Upload all items in the list to Notion using the differential engine.
    Saves state incrementally after each file so it's always resumable.
    """
    result = SyncResult(total_scanned=len(items))
    total = len(items)

    for idx, item in enumerate(items):
        if cancel_flag and cancel_flag():
            break

        if on_progress:
            on_progress(idx, total, item, item.status_tag)

        # Resolve parent folder in Notion
        parent_id = ensure_notion_path(
            api, item,
            adb_root=adb_root,
            container_name=container_name,
            container_emoji=container_emoji,
            server_port=server_port,
        )

        ok = upload_file(api, state, item, parent_id, server_port=server_port)

        if ok:
            if item.status_tag == "MODIFIED":
                result.updated += 1
            else:
                result.uploaded += 1
        else:
            result.failed += 1

        # Save state immediately — even if we're interrupted, progress is kept
        S.save_state(state)

    if on_progress:
        on_progress(total, total, None, "DONE")

    return result
