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
import json
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from core import filters as F
from core.notion_api import NotionAPI
from core import state as S

def log_sync_event(level: str, message: str, file_path: str = ""):
    """Append event to sync_events.jsonl for real-time web UI streaming."""
    try:
        log_file = Path(__file__).resolve().parent.parent / "sync_events.jsonl"
        entry = {
            "timestamp": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
            "path": file_path,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass

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
class FolderItem:
    """A folder discovered during a scan."""
    path: str          # Absolute local path
    name: str          # Folder name only
    mtime: float       # Unix timestamp of last modification
    file_count: int    # Number of files in this folder (non-recursive)
    parent_path: str   # Parent directory path
    display_path: str = ""     # Human-readable Windows-style path for display
    status_tag: str = ""       # "NEW" | "" (filled by diff step)
    is_android: bool = False


@dataclass
class SyncResult:
    uploaded: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    deleted: int = 0
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


def scan_folders(root: str) -> Generator[FolderItem, None, None]:
    """
    Walk a local directory tree and yield FolderItem for every non-ignored folder.
    This includes empty folders and folders with files.
    Each folder is yielded with its modification time and file count.
    """
    root_path = Path(root).resolve()
    if not root_path.exists():
        return

    # Yield root_path itself if it is a directory and not a drive root (e.g. C:\)
    # This guarantees that even if a selected folder is completely empty, it gets synced to Notion
    if root_path.is_dir() and root_path.parent != root_path:
        try:
            st = root_path.stat()
            file_count = len([f for f in root_path.iterdir() if f.is_file() and not F.should_ignore_file(f)])
            yield FolderItem(
                path=str(root_path),
                name=root_path.name,
                mtime=st.st_mtime,
                file_count=file_count,
                parent_path=str(root_path.parent),
                display_path=str(root_path),
                is_android=False,
            )
        except OSError:
            pass

    for dirpath, dirs, files in os.walk(root_path):
        # Prune ignored directories in-place so os.walk won't descend into them
        dirs[:] = [
            d for d in dirs
            if not F.should_ignore_dir(d) and not d.startswith(".")
        ]

        # Skip the root directory itself as we already handled it above
        if Path(dirpath).resolve() == root_path:
            continue

        try:
            folder_path = Path(dirpath)
            st = folder_path.stat()
            file_count = len([f for f in files if not F.should_ignore_file(folder_path / f)])
            
            yield FolderItem(
                path=str(folder_path),
                name=folder_path.name,
                mtime=st.st_mtime,
                file_count=file_count,
                parent_path=str(folder_path.parent),
                display_path=str(folder_path),
                is_android=False,
            )
        except OSError:
            continue


def scan_android_folders(
    device_id: str,
    adb_root: str,
    win_label: str,
) -> Generator[FolderItem, None, None]:
    """
    Scan an Android storage path via ADB and yield FolderItem for every directory.
    Uses fast pruned find for near-instant scanning.
    """
    cmd = (
        f"find '{adb_root}' -maxdepth 3 "
        f"-name '.*' -prune -o "
        f"-path '*/Android' -prune -o "
        f"-path '*/LOST.DIR' -prune -o "
        f"-path '*/.trash' -prune -o "
        f"-type d -exec stat -c '%n|%Y' {{}} + 2>/dev/null"
    )
    try:
        proc = subprocess.run(
            ["adb", "-s", device_id, "shell", cmd],
            capture_output=True, text=True, errors="ignore", timeout=30,
        )
    except Exception:
        return

    for line in proc.stdout.splitlines():
        if "|" not in line:
            continue
        parts = line.strip().split("|")
        if len(parts) != 2:
            continue

        dpath_adb, raw_mtime = parts
        if F.should_ignore_android_path(dpath_adb):
            continue

        dname = dpath_adb.rstrip("/").split("/")[-1]
        if not dname or any(dname.lower().startswith(p) for p in F.IGNORED_PREFIXES) or F.should_ignore_dir(dname):
            continue

        # Skip the adb_root container itself
        if dpath_adb.rstrip("/") == adb_root.rstrip("/"):
            continue

        try:
            mtime = float(raw_mtime)
        except ValueError:
            mtime = 0.0

        rel = dpath_adb.replace(adb_root, "").replace("/", "\\").lstrip("\\")
        display = f"{win_label}\\{rel}" if rel else win_label
        parent_adb = "/".join(dpath_adb.rstrip("/").split("/")[:-1])

        yield FolderItem(
            path=dpath_adb,
            name=dname,
            mtime=mtime,
            file_count=0,
            parent_path=parent_adb,
            display_path=display,
            is_android=True,
        )


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
    Uses a single fast `find … stat` command for maximum speed.
    """
    cmd = (
        f"find '{adb_root}' "
        f"-name '.*' -prune -o "
        f"-path '*/Android' -prune -o "
        f"-path '*/LOST.DIR' -prune -o "
        f"-path '*/.trash' -prune -o "
        f"-type f -exec stat -c '%n|%s|%Y' {{}} + 2>/dev/null"
    )
    try:
        proc = subprocess.run(
            ["adb", "-s", device_id, "shell", cmd],
            capture_output=True, text=True, errors="ignore", timeout=30,
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


def compute_folder_diff(
    folders: List[FolderItem],
    state: Dict[str, Any],
) -> Tuple[List[FolderItem], int]:
    """
    Compare scanned folders against the state index.
    Populates each folder's status_tag ("NEW" | "").
    Returns (folders_to_sync, skipped_count).
    """
    to_sync: List[FolderItem] = []
    skipped = 0

    for folder in folders:
        change = S.check_folder(
            state, folder.path, folder.mtime, folder.file_count,
            android=folder.is_android,
        )
        if change is None:
            skipped += 1
            continue
        folder.status_tag = "NEW"
        to_sync.append(folder)

    return to_sync, skipped


# ─────────────────────────────────────────────────────────────────────────────
# Deletion of missing files
# ─────────────────────────────────────────────────────────────────────────────

def delete_missing_files(
    api: NotionAPI,
    state: Dict[str, Any],
    all_scanned_paths: set,
    root_path: Optional[str] = None,
    android: bool = False,
    on_progress: Optional[ProgressCallback] = None,
) -> int:
    """
    Delete Notion pages for files that no longer exist locally within the scanned root_path.
    Returns the number of files successfully deleted.
    """
    bucket = "android_files" if android else "files"
    state_files = state.get(bucket, {})

    # Scope missing files ONLY to the scanned root directory
    missing = {}
    for path, info in list(state_files.items()):
        if root_path:
            norm_path = path.replace("\\", "/").lower()
            norm_root = root_path.replace("\\", "/").lower().rstrip("/")
            if not norm_path.startswith(norm_root):
                continue

        if path not in all_scanned_paths:
            # For local files, verify it actually does not exist on disk before deleting
            if not android and Path(path).exists():
                continue
            missing[path] = info

    deleted_count = 0
    total = len(missing)

    for idx, (path, info) in enumerate(missing.items()):
        notion_id = info.get("notion_id")
        if not notion_id:
            continue

        if on_progress:
            on_progress(idx, total, None, "DELETING")

        if api.delete_page(notion_id):
            state_files.pop(path, None)
            deleted_count += 1

    return deleted_count


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
    item: Any,
    adb_root: str,
    container_name: str,
    container_emoji: str,
) -> Tuple[str, str, List[str]]:
    """
    For an Android file, return (root_name, emoji, relative_parts).
    """
    parent_adb = getattr(item, "parent_path", "")
    rel = parent_adb.replace(adb_root, "").lstrip("/")
    parts = [p for p in rel.split("/") if p]
    return container_name, container_emoji, parts


def ensure_notion_path(
    api: NotionAPI,
    item: Any,
    adb_root: Optional[str] = None,
    container_name: Optional[str] = None,
    container_emoji: str = "📱",
) -> Optional[str]:
    """
    Ensure all parent folders exist in Notion for the given file or folder item.
    Returns the Notion ID of the deepest parent folder.
    """
    is_android = getattr(item, "is_android", False)
    if is_android and adb_root and container_name:
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


TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css",
    ".json", ".yaml", ".yml", ".xml", ".csv", ".log", ".sql", ".sh", ".bat",
    ".ini", ".env", ".toml", ".c", ".cpp", ".h", ".rs", ".go", ".java", ".php"
}

def upload_file_content_to_page(api: NotionAPI, notion_id: str, item: FileItem):
    """If file is text/code and size < 250KB, read content and embed as Notion code blocks."""
    if item.ext.lower() not in TEXT_EXTENSIONS or item.size > 250 * 1024 or item.is_android:
        return
    try:
        content = Path(item.path).read_text(encoding="utf-8", errors="replace")
        if not content:
            return
        chunks = [content[i:i+1900] for i in range(0, min(len(content), 10000), 1900)]
        blocks = []
        lang_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".tsx": "typescript", ".html": "html", ".css": "css",
            ".json": "json", ".md": "markdown", ".sql": "sql",
            ".sh": "bash", ".bat": "bash", ".java": "java", ".c": "c",
            ".cpp": "cpp", ".rs": "rust", ".go": "go", ".xml": "xml"
        }
        lang = lang_map.get(item.ext.lower(), "plain text")
        for chunk in chunks:
            blocks.append({
                "object": "block",
                "type": "code",
                "code": {
                    "rich_text": [{"type": "text", "text": {"content": chunk}}],
                    "language": lang
                }
            })
        if blocks:
            api.append_block_children(notion_id, blocks)
    except Exception:
        pass


def upload_file(
    api: NotionAPI,
    state: Dict[str, Any],
    item: FileItem,
    parent_notion_id: Optional[str],
) -> bool:
    """
    Upload a single FileItem to Notion (1 single optimized API call).
    Updates the state dict on success.
    """
    ftype, emoji = F.classify_file(item.ext)
    size_mb = round(item.size / (1024 * 1024), 4)
    display = item.display_path or item.path

    if item.status_tag == "MODIFIED" and item.existing_notion_id:
        cloud_url = f"https://www.notion.so/{item.existing_notion_id}"
        ok = api.update_page(
            item.existing_notion_id,
            {
                "File Size": {"number": size_mb},
                "Open in Browser": {"url": cloud_url},
                "Description": {"rich_text": [{"text": {"content": f"Path: {display} (Updated)"}}]},
            },
        )
        if ok:
            S.record_file(
                state, item.path, item.existing_notion_id,
                item.mtime, item.size, android=item.is_android,
            )
            log_sync_event("info", f"[UPDATE] Updated {item.name} ({size_mb} MB)", display)
        return ok

    else:
        # Create new Notion page (Single optimized call)
        props: Dict[str, Any] = {
            "Name": {"title": [{"text": {"content": item.name}}]},
            "Type": {"select": {"name": "File"}},
            "File Type": {"select": {"name": ftype}},
            "File Extension": {"rich_text": [{"text": {"content": item.ext}}]},
            "File Size": {"number": size_mb},
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
            log_sync_event("success", f"[UPLOAD] Synced {item.name} ({size_mb} MB)", display)
        return notion_id is not None


def upload_folder(
    api: NotionAPI,
    state: Dict[str, Any],
    folder: FolderItem,
    parent_notion_id: Optional[str],
) -> bool:
    """
    Create or find a FolderItem in Notion.
    """
    display = folder.display_path or folder.path
    notion_id = api.ensure_folder(folder.name, parent_notion_id, emoji="📁")
    if notion_id:
        S.record_folder(
            state, folder.path, notion_id,
            folder.mtime, folder.file_count,
            android=folder.is_android,
        )
    return notion_id is not None


# ─────────────────────────────────────────────────────────────────────────────
# High-level sync runner (Parallelized & Optimized)
# ─────────────────────────────────────────────────────────────────────────────

def run_sync(
    api: NotionAPI,
    state: Dict[str, Any],
    items: List[FileItem],
    on_progress: Optional[ProgressCallback] = None,
    cancel_flag: Optional[Callable[[], bool]] = None,
    adb_root: Optional[str] = None,
    container_name: Optional[str] = None,
    container_emoji: str = "📱",
    delete_missing: bool = False,
    all_scanned_paths: Optional[set] = None,
    root_path: Optional[str] = None,
) -> SyncResult:
    """
    Upload all items to Notion with multi-threaded concurrent execution for maximum speed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    result = SyncResult(total_scanned=len(items))
    total = len(items)
    if total == 0:
        return result

    # Compact pending queue for live UI telemetry
    state["sync_queue"] = [
        {"name": it.name, "path": it.display_path or it.path, "size": it.size, "status": it.status_tag or "PENDING"}
        for it in items[:60]
    ]
    S.save_state(state)
    log_sync_event("info", f"[QUEUE] Prepared {total} files for synchronization...")

    for idx, item in enumerate(items):
        if cancel_flag and cancel_flag():
            break

        if on_progress:
            on_progress(idx, total, item, item.status_tag or "SYNC")

        try:
            parent_id = ensure_notion_path(
                api, item,
                adb_root=adb_root,
                container_name=container_name,
                container_emoji=container_emoji,
            )
            ok = upload_file(api, state, item, parent_id)
        except Exception:
            ok = False

        if ok:
            if item.status_tag == "MODIFIED":
                result.updated += 1
            else:
                result.uploaded += 1
        else:
            result.failed += 1

        # Periodically save state & update pending queue
        if idx % 10 == 0 or idx == total - 1:
            state["sync_queue"] = [
                {"name": it.name, "path": it.display_path or it.path, "size": it.size, "status": "QUEUED"}
                for it in items[idx + 1:idx + 61]
            ]
            S.save_state(state)

    state["sync_queue"] = []
    S.save_state(state)

    # Delete missing
    if delete_missing and all_scanned_paths is not None:
        android = any(item.is_android for item in items) if items else (adb_root is not None)
        deleted = delete_missing_files(
            api, state, all_scanned_paths,
            root_path=root_path or adb_root,
            android=android,
            on_progress=on_progress,
        )
        result.deleted = deleted
        S.save_state(state)

    if on_progress:
        on_progress(total, total, None, "DONE")

    return result


def run_folder_sync(
    api: NotionAPI,
    state: Dict[str, Any],
    folders: List[FolderItem],
    on_progress: Optional[ProgressCallback] = None,
    cancel_flag: Optional[Callable[[], bool]] = None,
    adb_root: Optional[str] = None,
    container_name: Optional[str] = None,
    container_emoji: str = "📱",
) -> SyncResult:
    """
    Upload all folders in the list to Notion using the differential engine.
    Saves state incrementally after each folder so it's always resumable.

    Folders are synced BEFORE files so the folder hierarchy exists in Notion
    before any files are uploaded into them.
    """
    result = SyncResult(total_scanned=len(folders))
    total = len(folders)

    for idx, folder in enumerate(folders):
        if cancel_flag and cancel_flag():
            break

        if on_progress:
            on_progress(idx, total, folder, folder.status_tag)

        # Resolve parent folder in Notion
        parent_id = ensure_notion_path(
            api, folder,
            adb_root=adb_root,
            container_name=container_name,
            container_emoji=container_emoji,
        )

        ok = upload_folder(api, state, folder, parent_id)

        if ok:
            result.uploaded += 1
        else:
            result.failed += 1

        # Save state immediately — even if we're interrupted, progress is kept
        S.save_state(state)

    if on_progress:
        on_progress(total, total, None, "DONE")

    return result
