"""
Notion Drive Web GUI File Manager & Edge Browser Bridge
A modern, full-featured Web GUI mirroring Google Drive & OneDrive.
Serves a responsive single-page web app at http://127.0.0.1:8765
Features:
- Full Google Drive UI (Grid/List views, breadcrumbs, search, multi-column sorting)
- Git-Style Persistent State Tracking (.notion_sync_state.json):
  * Reads tracked files and folders on every sync
  * Detects NEW, MODIFIED, and UP-TO-DATE items
  * Skips unchanged files instantaneously (0 API calls wasted)
  * Updates modified files via Notion PATCH without creating duplicate rows
  * Saves state after every single file so progress is 100% resilient and resumable
- Live real-time file structure updates in browser
- Live ADB streaming for OnePlus Nord CE4 & SD card (0 PC disk bytes)
- Exact Windows Explorer Paths: 'This PC\\OnePlus Nord CE4\\Internal shared storage' & 'This PC\\OnePlus Nord CE4\\SD card'
"""

import os
import sys
# Ensure UTF-8 output — prevents UnicodeEncodeError on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

import io
import time
import json
import re
import zipfile
import mimetypes
import hmac
import hashlib
import secrets
import urllib.parse
import html
import threading
import subprocess
import requests
import logging
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, List

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("notion_server")

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def _load_env_credentials():
    env_locations = [
        Path(__file__).parent / ".env",
        Path.home() / ".notion_env",
        Path.home() / ".env"
    ]
    for p in env_locations:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            except Exception:
                pass

_load_env_credentials()

from core import config
from core import filters as F

PORT = int(os.environ.get("PORT", config.LOCAL_SERVER_PORT))
NOTION_VERSION = config.NOTION_VERSION
DEFAULT_API_KEY = config.NOTION_TOKEN
DEFAULT_DB_ID = config.NOTION_DATABASE_ID.replace("-", "")

UPLOADS_DIR = Path.home() / "NotionDrive_Uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
CACHE_FILE = Path.home() / ".notion_drive_cache.json"
STATE_FILE = Path(__file__).parent / ".notion_sync_state.json"


SESSION_SECRET = secrets.token_hex(32)

def compute_auth_token(password: str) -> str:
    return hmac.new(SESSION_SECRET.encode("utf-8"), f"auth_token:{password}".encode("utf-8"), hashlib.sha256).hexdigest()

def check_authenticated(headers) -> bool:
    drive_pw = os.environ.get("DRIVE_PASSWORD", getattr(config, "DRIVE_PASSWORD", "")).strip()
    if not drive_pw:
        return True # Open access if no password configured
    expected_token = compute_auth_token(drive_pw)
    
    # 1. Check Cookie header
    cookie_str = headers.get("Cookie", "")
    if "notion_session=" in cookie_str:
        for c in cookie_str.split(";"):
            c = c.strip()
            if c.startswith("notion_session="):
                token = c.split("=", 1)[1].strip()
                if hmac.compare_digest(token, expected_token):
                    return True
    
    # 2. Check Authorization header
    auth_header = headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if hmac.compare_digest(token, expected_token):
            return True

    return False

DRIVE_CACHE = {
    "items": {},
    "children": {},
    "root_items": [],
    "version": 1
}
CACHE_LOCK = threading.RLock()

# ── Recent Files Ring Buffer ──────────────────────────────────────────────────
RECENT_FILES: List[Dict[str, Any]] = []  # max 100 entries, newest first
RECENT_LOCK = threading.Lock()
RECENT_FILE_PATH = Path.home() / ".notion_recent.json"

def _load_recent_files():
    global RECENT_FILES
    try:
        if RECENT_FILE_PATH.exists():
            with open(RECENT_FILE_PATH, "r", encoding="utf-8") as f:
                RECENT_FILES = json.load(f)
    except Exception:
        RECENT_FILES = []

def _save_recent_files():
    try:
        with open(RECENT_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(RECENT_FILES[:100], f)
    except Exception:
        pass

def _push_recent(item: dict):
    """Add a file to the recent ring buffer; dedup by id."""
    with RECENT_LOCK:
        global RECENT_FILES
        RECENT_FILES = [r for r in RECENT_FILES if r.get("id") != item.get("id")]
        RECENT_FILES.insert(0, item)
        if len(RECENT_FILES) > 100:
            RECENT_FILES.pop()
        _save_recent_files()

# ── SSE Event Queue ───────────────────────────────────────────────────────────
import queue as _queue
_SSE_CLIENTS: List[_queue.SimpleQueue] = []
_SSE_LOCK = threading.Lock()

def _broadcast_sse(event_type: str, data: dict):
    """Push a server-sent event to all subscribed clients."""
    msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    with _SSE_LOCK:
        dead = []
        for q in _SSE_CLIENTS:
            try:
                q.put_nowait(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            _SSE_CLIENTS.remove(q)

def register_drive_cache_item(item_id: str, name: str, item_type: str, ext: str, size_mb: float, size_bytes: int, parent_id: str, local_path: str, mtime: float = 0, starred: bool = False, archived: bool = False):
    """Instantly registers a file or folder in the in-memory cache AND SQLite index, bumps version for live updates."""
    is_new = False
    with CACHE_LOCK:
        existing = DRIVE_CACHE["items"].get(item_id)
        is_new = existing is None
        if existing:
            old_parent = existing.get("parent_id")
            if old_parent != parent_id:
                if old_parent:
                    old_children = DRIVE_CACHE["children"].get(old_parent, [])
                    if item_id in old_children:
                        old_children.remove(item_id)
                else:
                    if item_id in DRIVE_CACHE["root_items"]:
                        DRIVE_CACHE["root_items"].remove(item_id)

        entry = {
            "id": item_id,
            "name": name,
            "type": item_type,
            "extension": ext,
            "size_mb": size_mb,
            "size_bytes": size_bytes,
            "mtime": mtime or time.time(),
            "ctime": mtime or time.time(),
            "created_time": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_edited_time": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "parent_id": parent_id,
            "local_path": local_path,
            "starred": starred,
            "archived": archived
        }
        DRIVE_CACHE["items"][item_id] = entry
        if parent_id:
            children_list = DRIVE_CACHE["children"].setdefault(parent_id, [])
            if item_id not in children_list:
                children_list.append(item_id)
        else:
            if item_id not in DRIVE_CACHE["root_items"]:
                DRIVE_CACHE["root_items"].append(item_id)
        DRIVE_CACHE["version"] = DRIVE_CACHE.get("version", 0) + 1

    # Track recents (files only)
    if item_type == "File":
        _push_recent({
            "id": item_id, "name": name, "extension": ext,
            "size_mb": size_mb, "size_bytes": size_bytes,
            "mtime": mtime or time.time(), "parent_id": parent_id,
            "local_path": local_path, "type": item_type
        })

    # Update SQLite index asynchronously (non-blocking)
    def _update_index():
        try:
            from core.local_index import upsert_item
            index_entry = {
                'id': item_id,
                'name': name,
                'type': item_type,
                'extension': ext,
                'size_mb': size_mb,
                'size_bytes': size_bytes,
                'mtime': mtime or time.time(),
                'ctime': mtime or time.time(),
                'created_time': time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                'last_edited_time': time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                'parent_id': parent_id,
                'local_path': local_path,
                'storage_root': 'Notion Cloud',
                'notion_id': item_id,
                'sync_status': 'synced',
                'starred': 1 if starred else 0,
                'archived': 1 if archived else 0,
                'last_seen': time.time()
            }
            upsert_item(index_entry)
        except Exception as e:
            logger.debug(f"Index update skipped: {e}")
    
    t = threading.Thread(target=_update_index, daemon=True)
    t.start()

    # Broadcast SSE
    _broadcast_sse("file_added" if is_new else "file_updated", {
        "id": item_id, "name": name, "type": item_type,
        "parent_id": parent_id, "version": DRIVE_CACHE.get("version", 0)
    })


def _sync_cache_entry_to_index(entry: dict):
    """Upsert a single DRIVE_CACHE entry into the SQLite index (non-blocking)."""
    try:
        from core.local_index import upsert_item
        index_entry = {
            'id': entry.get('id'),
            'name': entry.get('name', ''),
            'type': entry.get('type', 'File'),
            'extension': entry.get('extension', ''),
            'size_mb': entry.get('size_mb', 0),
            'size_bytes': entry.get('size_bytes', 0),
            'mtime': entry.get('mtime', 0),
            'ctime': entry.get('ctime', 0),
            'created_time': entry.get('created_time', ''),
            'last_edited_time': entry.get('last_edited_time', ''),
            'parent_id': entry.get('parent_id'),
            'local_path': (entry.get('local_path') or '').strip() or None,
            'storage_root': entry.get('storage_root', 'Notion Cloud'),
            'notion_id': entry.get('id'),
            'sync_status': 'synced',
            'starred': 1 if entry.get('starred') else 0,
            'archived': 1 if entry.get('archived') else 0,
            'item_count': entry.get('item_count', 0),
            'last_seen': time.time()
        }
        upsert_item(index_entry)
    except Exception as e:
        logger.debug(f"Index update skipped: {e}")


def _store_uploaded_file(safe_name: str, parent_id: Optional[str], data_bytes: bytes):
    """Persist an uploaded file to disk + create its Notion page + register in the drive cache.
    Returns (ok: bool, new_page_id: Optional[str], error: Optional[str]).
    """
    try:
        safe_rel = Path(safe_name).name
        local_file_path = UPLOADS_DIR / safe_rel
        try:
            local_file_path.resolve().relative_to(UPLOADS_DIR.resolve())
        except ValueError:
            return False, None, "Path traversal detected"
        local_file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(local_file_path, "wb") as f:
            f.write(data_bytes)

        file_size = len(data_bytes)
        size_mb = round(file_size / (1024 * 1024), 4)
        ext = Path(safe_name).suffix.lower()
        ftype, emoji = F.classify_file(ext) if ext else ("Other", "📄")

        api_client = BackgroundSyncRunner(DEFAULT_API_KEY, DEFAULT_DB_ID)
        target_parent_notion_id = parent_id or api_client.ensure_root("Local Disk (C:)")

        payload = {
            "parent": {"database_id": DEFAULT_DB_ID},
            "icon": {"type": "emoji", "emoji": emoji},
            "properties": {
                "Name": {"title": [{"text": {"content": safe_name}}]},
                "Type": {"select": {"name": "File"}},
                "File Type": {"select": {"name": ftype}},
                "File Extension": {"rich_text": [{"text": {"content": ext}}]},
                "File Size": {"number": size_mb},
                "Description": {"rich_text": [{"text": {"content": f"Path: {local_file_path}"}}]},
                "Favorite": {"checkbox": False},
                "Archived": {"checkbox": False}
            }
        }
        if target_parent_notion_id:
            payload["properties"]["Parent Folder"] = {"relation": [{"id": target_parent_notion_id}]}

        res = requests.post("https://api.notion.com/v1/pages", headers=api_client.headers, json=payload, timeout=30)
        if res.status_code == 200:
            new_page_id = res.json()["id"].replace("-", "")
            cloud_url = f"https://www.notion.so/{new_page_id}"
            try:
                requests.patch(f"https://api.notion.com/v1/pages/{new_page_id}", headers=api_client.headers, json={"properties": {"Open in Browser": {"url": cloud_url}}}, timeout=15)
            except Exception:
                pass
            register_drive_cache_item(
                new_page_id, safe_name, "File", ext, size_mb, file_size,
                target_parent_notion_id, str(local_file_path), time.time()
            )
            return True, new_page_id, None
        return False, None, f"Notion error: {res.status_code}"
    except Exception as e:
        logger.error(f"Upload store error: {e}")
        return False, None, str(e)


# ── Path Safety ───────────────────────────────────────────────────────────────
ALLOWED_ROOT_PREFIXES: tuple = ()

def _build_allowed_roots():
    """Collect permitted root prefixes from detected storage."""
    global ALLOWED_ROOT_PREFIXES
    roots = []
    for letter in ("C", "D", "E", "F"):
        p = Path(f"{letter}:/")
        if p.exists():
            roots.append(str(p).lower())
    roots.append(str(UPLOADS_DIR).lower())
    roots.append(str(Path.home()).lower())
    ALLOWED_ROOT_PREFIXES = tuple(roots)

def _safe_resolve_path(requested: str) -> Path:
    """Resolve requested path and reject traversal outside allowed roots."""
    try:
        resolved = Path(requested).resolve()
    except Exception:
        raise PermissionError("Invalid path")
    if ALLOWED_ROOT_PREFIXES:
        low = str(resolved).lower()
        if not any(low.startswith(pfx) for pfx in ALLOWED_ROOT_PREFIXES):
            raise PermissionError(f"Path outside allowed roots: {resolved}")
    return resolved

# ==============================================================================
# GIT-STYLE PERSISTENT STATE MANAGEMENT (.notion_sync_state.json)
# ==============================================================================
def load_sync_state() -> Dict[str, Any]:
    state_paths = [
        STATE_FILE,
        Path.home() / ".notion_sync_state.json",
        Path.cwd() / ".notion_sync_state.json"
    ]
    for sp in state_paths:
        if sp.exists():
            try:
                with open(sp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        data.setdefault("files", {})
                        data.setdefault("android_files", {})
                        data.setdefault("folders", {})
                        return data
            except Exception:
                pass
    return {"files": {}, "android_files": {}, "folders": {}}

def save_sync_state(state: Dict[str, Any]):
    state_paths = [
        STATE_FILE,
        Path.home() / ".notion_sync_state.json"
    ]
    for sp in state_paths:
        try:
            with open(sp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass

# ==============================================================================
# REAL-TIME SYNC STATE & SYNC MANAGER
# ==============================================================================
SYNC_STATE = {
    "is_running": False,
    "current_target": "Idle",
    "current_file": "None",
    "current_path": "",
    "current_size_str": "-",
    "total_files": 0,
    "synced_files": 0,
    "remaining_files": 0,
    "percent": 0,
    "start_time": None,
    "speed_str": "-",
    "status_message": "Ready to sync",
    "queue": [],
    "history": [],
    "logs": []
}

CANCEL_SYNC_FLAG = False
SYNC_LOCK = threading.RLock()

def add_sync_log(msg: str):
    ts = time.strftime("%H:%M:%S")
    log_line = f"[{ts}] {msg}"
    with SYNC_LOCK:
        SYNC_STATE["logs"].append(log_line)
        if len(SYNC_STATE["logs"]) > 200:
            SYNC_STATE["logs"].pop(0)

FILE_TYPE_MAP = {
    ".pdf": "PDF",
    ".doc": "Word", ".docx": "Word",
    ".xls": "Excel", ".xlsx": "Excel", ".csv": "Excel",
    ".ppt": "PowerPoint", ".pptx": "PowerPoint",
    ".jpg": "Image", ".jpeg": "Image", ".png": "Image", ".gif": "Image", ".webp": "Image", ".svg": "Image",
    ".mp4": "Video", ".mkv": "Video", ".mov": "Video", ".avi": "Video",
    ".mp3": "Audio", ".wav": "Audio", ".aac": "Audio", ".opus": "Audio", ".m4a": "Audio",
    ".zip": "ZIP", ".rar": "ZIP", ".7z": "ZIP", ".tar": "ZIP", ".gz": "ZIP",
    ".py": "Code", ".js": "Code", ".ts": "Code", ".html": "Code", ".css": "Code",
    ".java": "Code", ".cpp": "Code", ".c": "Code", ".json": "Code", ".yaml": "Code", ".yml": "Code", ".sql": "Code",
    ".txt": "Other", ".md": "Other"
}

EMOJI_MAP = {
    "PDF": "📕", "Word": "📝", "Excel": "📊", "PowerPoint": "📊",
    "Image": "🖼️", "Video": "🎬", "Audio": "🎵", "ZIP": "📦", "Code": "💻", "Other": "📄"
}

IGNORED_FILE_EXTENSIONS = {
    ".tmp", ".temp", ".log", ".bak", ".swp", ".lock", ".pid", ".cache",
    ".pyc", ".pyo", ".pyd", ".class", ".o", ".obj", ".dll", ".exe", ".so", ".dylib",
    ".sys", ".iso", ".vmdk", ".vdi", ".pdb", ".ilk", ".map"
}

IGNORED_FILE_PREFIXES = {"~", ".~", ".#", "#", "thumbs.db", "desktop.ini", ".ds_store"}

SYSTEM_CRITICAL_IGNORE = {
    "appdata", "application data", "local settings", "$recycle.bin", "system volume information",
    "__pycache__", "node_modules", ".gemini", ".git", "extensions", ".cache", ".gradle",
    ".m2", ".npm", ".rustup", ".cargo", ".nuget", ".venv", "venv", "env", "site-packages",
    "program files", "program files (x86)", "programdata", "windows", "recovery", "$windows.~bt"
}

class BackgroundSyncRunner:
    def __init__(self, api_key: str, db_id: str):
        self.api_key = api_key
        self.db_id = db_id.replace("-", "")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION
        }
        self.folder_cache = {}

    def load_folders(self):
        has_more = True
        start_cursor = None
        while has_more:
            payload = {"page_size": 100}
            if start_cursor:
                payload["start_cursor"] = start_cursor
            try:
                res = requests.post(f"https://api.notion.com/v1/databases/{self.db_id}/query", headers=self.headers, json=payload, timeout=20).json()
                for it in res.get("results", []):
                    nid = it["id"].replace("-", "")
                    props = it.get("properties", {})
                    title_list = props.get("Name", {}).get("title", [])
                    name = title_list[0].get("plain_text", "") if title_list else ""
                    clean_name = name.replace("📁 ", "").replace("📄 ", "").strip()
                    parents = [p["id"].replace("-", "") for p in props.get("Parent Folder", {}).get("relation", [])]
                    parent_id = parents[0] if parents else None
                    item_type = props.get("Type", {}).get("select", {}).get("name", "")
                    if item_type == "Folder" and props.get("Archived", {}).get("checkbox"):
                        continue
                    if item_type == "Folder":
                        self.folder_cache[(clean_name, parent_id)] = nid
                        register_drive_cache_item(nid, clean_name, "Folder", "", 0, 0, parent_id, clean_name)
                has_more = res.get("has_more", False)
                start_cursor = res.get("next_cursor")
            except Exception:
                break

    def ensure_root(self, name: str, emoji: str = "💽") -> str:
        if (name, None) in self.folder_cache:
            nid = self.folder_cache[(name, None)]
            register_drive_cache_item(nid, name, "Folder", "", 0, 0, None, name)
            return nid
        with CACHE_LOCK:
            for did, dit in DRIVE_CACHE.get("items", {}).items():
                if dit.get("type") == "Folder":
                    clean_n = dit.get("name", "").replace("📁 ", "").strip()
                    if clean_n == name and not dit.get("parent_id"):
                        self.folder_cache[(name, None)] = did
                        register_drive_cache_item(did, name, "Folder", "", 0, 0, None, name)
                        return did
        payload = {
            "parent": {"database_id": self.db_id},
            "icon": {"type": "emoji", "emoji": emoji},
            "properties": {
                "Name": {"title": [{"text": {"content": name}}]},
                "Type": {"select": {"name": "Folder"}},
                "Favorite": {"checkbox": True}
            }
        }
        try:
            res = requests.post("https://api.notion.com/v1/pages", headers=self.headers, json=payload, timeout=20)
            if res.status_code == 200:
                nid = res.json()["id"].replace("-", "")
                self.folder_cache[(name, None)] = nid
                register_drive_cache_item(nid, name, "Folder", "", 0, 0, None, name)
                return nid
        except Exception:
            pass
        return None

    def ensure_folder_path(self, folder_str: str) -> str:
        norm = folder_str.replace("/", "\\").strip()
        is_android = ("This PC\\OnePlus Nord CE4" in norm or 
                      "Internal shared storage" in norm or 
                      "Internal Storage" in norm or 
                      "SD card" in norm or 
                      "SD Card" in norm or
                      norm.startswith("/storage") or
                      norm.startswith("/sdcard"))

        if not is_android:
            path_obj = Path(folder_str).resolve()
            drive_letter = path_obj.drive.upper().rstrip(":")
            root_name = f"Local Disk ({drive_letter}:)" if drive_letter else "Local Disk (C:)"
            container_id = self.ensure_root(root_name, "💽")
            rel_parts = [p for p in path_obj.parts[1:] if p]
        else:
            if "SD card" in norm or "SD Card" in norm or "/storage/4A21-0000" in norm:
                root_name = "SD card"
                container_id = self.ensure_root(root_name, "💾")
                clean = norm.replace("This PC\\OnePlus Nord CE4\\SD card", "").replace("SD card", "").replace("SD Card", "").replace("/storage/4A21-0000", "")
                rel_parts = [p for p in clean.replace("/", "\\").split("\\") if p]
            else:
                root_name = "Internal shared storage"
                container_id = self.ensure_root(root_name, "📱")
                clean = norm.replace("This PC\\OnePlus Nord CE4\\Internal shared storage", "").replace("Internal shared storage", "").replace("Internal Storage", "").replace("/storage/emulated/0", "").replace("/sdcard", "")
                rel_parts = [p for p in clean.replace("/", "\\").split("\\") if p]

        curr_id = container_id
        for part in rel_parts:
            cache_k = (part, curr_id)
            if cache_k in self.folder_cache:
                parent_cache_id = curr_id
                curr_id = self.folder_cache[cache_k]
                register_drive_cache_item(curr_id, part, "Folder", "", 0, 0, parent_cache_id, part)
                continue

            found_nid = None
            with CACHE_LOCK:
                for did, dit in DRIVE_CACHE.get("items", {}).items():
                    if dit.get("type") == "Folder":
                        clean_n = dit.get("name", "").replace("📁 ", "").strip()
                        if clean_n == part and dit.get("parent_id") == curr_id:
                            found_nid = did
                            break
            if found_nid:
                self.folder_cache[cache_k] = found_nid
                register_drive_cache_item(found_nid, part, "Folder", "", 0, 0, curr_id, part)
                curr_id = found_nid
                continue

            payload = {
                "parent": {"database_id": self.db_id},
                "icon": {"type": "emoji", "emoji": "📁"},
                "properties": {
                    "Name": {"title": [{"text": {"content": part}}]},
                    "Type": {"select": {"name": "Folder"}},
                    "Parent Folder": {"relation": [{"id": curr_id}]} if curr_id else {"relation": []}
                }
            }
            res = requests.post("https://api.notion.com/v1/pages", headers=self.headers, json=payload, timeout=20)
            if res.status_code == 200:
                nid = res.json()["id"].replace("-", "")
                self.folder_cache[cache_k] = nid
                register_drive_cache_item(nid, part, "Folder", "", 0, 0, curr_id, part)
                curr_id = nid
            else:
                if "Sub-item hierarchy" in res.text:
                    del payload["properties"]["Parent Folder"]
                    res2 = requests.post("https://api.notion.com/v1/pages", headers=self.headers, json=payload, timeout=20)
                    if res2.status_code == 200:
                        nid = res2.json()["id"].replace("-", "")
                        self.folder_cache[cache_k] = nid
                        register_drive_cache_item(nid, part, "Folder", "", 0, 0, curr_id, part)
                        curr_id = nid
                    else:
                        return curr_id
                else:
                    return curr_id
        return curr_id

    def run_sync(self, target: str):
        global CANCEL_SYNC_FLAG
        CANCEL_SYNC_FLAG = False
        add_sync_log(f"⚡ Starting Git-style differential sync for target: {target.upper()}...")

        state = load_sync_state()
        pc_tracked = state.setdefault("files", {})
        android_tracked = state.setdefault("android_files", {})

        with SYNC_LOCK:
            SYNC_STATE["is_running"] = True
            SYNC_STATE["current_target"] = target.upper()
            SYNC_STATE["status_message"] = "Scanning directories & calculating Git-style differential changes..."
            SYNC_STATE["start_time"] = time.time()
            SYNC_STATE["queue"] = []
            SYNC_STATE["synced_files"] = 0
            SYNC_STATE["percent"] = 0

        self.load_folders()
        files_to_sync = []
        skipped_count = 0

        # 1. Local PC scanning (C:)
        if target in ("c", "all"):
            add_sync_log("Scanning Local Disk (C:)...")
            user_dirs = [
                Path.home() / "Desktop",
                Path.home() / "Documents",
                Path.home() / "Downloads"
            ]
            for udir in user_dirs:
                if not udir.exists():
                    continue
                for root, dirs, files in os.walk(udir):
                    dirs[:] = [d for d in dirs if d.lower() not in SYSTEM_CRITICAL_IGNORE and not d.startswith(".")]
                    for f in files:
                        ext = os.path.splitext(f)[1].lower()
                        if ext in IGNORED_FILE_EXTENSIONS or any(f.lower().startswith(p) for p in IGNORED_FILE_PREFIXES):
                            continue
                        fp = Path(root) / f
                        try:
                            st = fp.stat()
                            fpath_str = str(fp)
                            prev = pc_tracked.get(fpath_str)
                            is_new = prev is None
                            is_modified = False
                            if prev:
                                if abs(prev.get("mtime", 0) - st.st_mtime) > 1.0 or prev.get("size", 0) != st.st_size:
                                    is_modified = True
                            
                            if is_new or is_modified:
                                files_to_sync.append({
                                    "fpath": fpath_str,
                                    "display_path": fpath_str,
                                    "name": f,
                                    "size": st.st_size,
                                    "mtime": st.st_mtime,
                                    "ext": ext,
                                    "is_android": False,
                                    "status_tag": "NEW" if is_new else "MODIFIED",
                                    "existing_notion_id": prev.get("notion_id") if prev else None
                                })
                            else:
                                skipped_count += 1
                        except Exception:
                            pass

        # 2. Local PC scanning (D:)
        if target in ("d", "all"):
            d_root = Path("D:/")
            if d_root.exists():
                add_sync_log("Scanning Local Disk (D:)...")
                for root, dirs, files in os.walk(d_root):
                    dirs[:] = [d for d in dirs if d.lower() not in SYSTEM_CRITICAL_IGNORE and not d.startswith(".")]
                    for f in files:
                        ext = os.path.splitext(f)[1].lower()
                        if ext in IGNORED_FILE_EXTENSIONS or any(f.lower().startswith(p) for p in IGNORED_FILE_PREFIXES):
                            continue
                        fp = Path(root) / f
                        try:
                            st = fp.stat()
                            fpath_str = str(fp)
                            prev = pc_tracked.get(fpath_str)
                            is_new = prev is None
                            is_modified = False
                            if prev:
                                if abs(prev.get("mtime", 0) - st.st_mtime) > 1.0 or prev.get("size", 0) != st.st_size:
                                    is_modified = True
                            
                            if is_new or is_modified:
                                files_to_sync.append({
                                    "fpath": fpath_str,
                                    "display_path": fpath_str,
                                    "name": f,
                                    "size": st.st_size,
                                    "mtime": st.st_mtime,
                                    "ext": ext,
                                    "is_android": False,
                                    "status_tag": "NEW" if is_new else "MODIFIED",
                                    "existing_notion_id": prev.get("notion_id") if prev else None
                                })
                            else:
                                skipped_count += 1
                        except Exception:
                            pass

        # 3. Android phone scanning (OnePlus Nord CE4)
        if target in ("phone", "sdcard", "all"):
            add_sync_log("Scanning OnePlus Nord CE4 via ADB USB bridge...")
            try:
                dev_out = subprocess.check_output(["adb", "devices"]).decode("utf-8")
                dev_lines = [l.strip() for l in dev_out.splitlines() if l.strip() and not l.startswith("List of")]
                if dev_lines:
                    dev_id = dev_lines[0].split()[0]
                    add_sync_log(f"Connected Android device: {dev_id} (OnePlus Nord CE4)")
                    
                    phone_targets = []
                    if target in ("phone", "all"):
                        phone_targets.append((
                            "This PC\\OnePlus Nord CE4\\Internal shared storage",
                            "/storage/emulated/0",
                            "Internal shared storage"
                        ))
                    if target in ("sdcard", "all"):
                        try:
                            stor_out = subprocess.check_output(["adb", "-s", dev_id, "shell", "ls", "/storage"]).decode("utf-8")
                            for s in stor_out.split():
                                if s not in ("emulated", "self", "persist", "sdcard0"):
                                    phone_targets.append((
                                        "This PC\\OnePlus Nord CE4\\SD card",
                                        f"/storage/{s}",
                                        "SD card"
                                    ))
                                    break
                        except Exception:
                            pass

                    for win_label, linux_base, container_name in phone_targets:
                        add_sync_log(f"Scanning {container_name} ({linux_base}) including Android/media (excluding private app data)...")
                        cmd = f"find '{linux_base}/' -type f -not -path '*/.*' -not -path '*/Android/data*' -not -path '*/Android/obb*' -not -path '*/Android/sandbox*' -not -path '*/.thumbnails*' -not -path '*/LOST.DIR*' -not -path '*/.trash*' -exec stat -c '%n|%s|%Y' {{}} + 2>/dev/null"
                        try:
                            proc = subprocess.run(["adb", "-s", dev_id, "shell", cmd], capture_output=True, text=True, errors="ignore")
                            for line in proc.stdout.splitlines():
                                if "|" in line:
                                    parts = line.strip().split("|")
                                    if len(parts) == 3:
                                        fpath, fsize, fmtime = parts[0], int(parts[1]), float(parts[2])
                                        fname = fpath.split("/")[-1]
                                        ext = "." + fname.split(".")[-1].lower() if "." in fname else ""
                                        norm_p = fpath.replace("\\", "/")
                                        if "/Android/" in norm_p and "/Android/media" not in norm_p:
                                            continue
                                        if "/." in fpath or "/LOST.DIR" in fpath or "/.thumbnails" in fpath or "/.trash" in fpath:
                                            continue
                                        if any(fname.lower().startswith(p) for p in IGNORED_FILE_PREFIXES):
                                            continue
                                        if ext in IGNORED_FILE_EXTENSIONS:
                                            continue
                                        
                                        rel_path = fpath.replace(linux_base, "").replace("/", "\\").lstrip("\\")
                                        disp_full = f"{win_label}\\{rel_path}"
                                        
                                        prev = android_tracked.get(fpath)
                                        is_new = prev is None
                                        is_modified = False
                                        if prev:
                                            if abs(prev.get("mtime", 0) - fmtime) > 1.0 or prev.get("size", 0) != fsize:
                                                is_modified = True
                                        
                                        if is_new or is_modified:
                                            files_to_sync.append({
                                                "fpath": fpath,
                                                "display_path": disp_full,
                                                "name": fname,
                                                "size": fsize,
                                                "mtime": fmtime,
                                                "ext": ext,
                                                "is_android": True,
                                                "container_name": container_name,
                                                "status_tag": "NEW" if is_new else "MODIFIED",
                                                "existing_notion_id": prev.get("notion_id") if prev else None
                                            })
                                        else:
                                            skipped_count += 1
                        except Exception as e:
                            add_sync_log(f"ADB scan notice for {container_name}: {e}")
                else:
                    add_sync_log("Notice: No Android phone detected over USB.")
            except Exception as e:
                add_sync_log(f"ADB error: {e}")

        total = len(files_to_sync)
        add_sync_log(f"🔍 Git Status: {total} files changed/new ({skipped_count} up-to-date, skipped)")

        if total == 0:
            add_sync_log(f"✨ 100% Up to Date! All {skipped_count} files verified against state index.")
            with SYNC_LOCK:
                SYNC_STATE["is_running"] = False
                SYNC_STATE["total_files"] = skipped_count
                SYNC_STATE["synced_files"] = skipped_count
                SYNC_STATE["remaining_files"] = 0
                SYNC_STATE["percent"] = 100
                SYNC_STATE["status_message"] = f"100% Up to Date ({skipped_count} files verified)"
                SYNC_STATE["queue"] = []
            return

        def build_live_window(current_idx: int, active_item: dict, active_status: str) -> list:
            window = []
            if active_item:
                mb = round(active_item["size"] / (1024 * 1024), 2)
                sz_str = f"{mb} MB" if mb >= 0.1 else f"{round(active_item['size']/1024, 1)} KB"
                window.append({
                    "id": current_idx,
                    "name": active_item["name"],
                    "path": active_item["display_path"],
                    "size_str": sz_str,
                    "status": active_status,
                    "tag": active_item.get("status_tag", "NEW")
                })
            next_items = files_to_sync[current_idx:current_idx + 15]
            for offset, n_it in enumerate(next_items, start=current_idx + 1):
                n_mb = round(n_it["size"] / (1024 * 1024), 2)
                n_sz_str = f"{n_mb} MB" if n_mb >= 0.1 else f"{round(n_it['size']/1024, 1)} KB"
                window.append({
                    "id": offset,
                    "name": n_it["name"],
                    "path": n_it["display_path"],
                    "size_str": n_sz_str,
                    "status": "queued",
                    "tag": n_it.get("status_tag", "NEW")
                })
            return window

        initial_window = build_live_window(0, files_to_sync[0] if files_to_sync else None, "queued")

        with SYNC_LOCK:
            SYNC_STATE["total_files"] = total
            SYNC_STATE["remaining_files"] = total
            SYNC_STATE["queue"] = initial_window
            SYNC_STATE["status_message"] = f"Syncing {total} changes to Notion Cloud ({skipped_count} up-to-date)..."

        start_t = time.time()
        for idx, it in enumerate(files_to_sync, 1):
            if CANCEL_SYNC_FLAG:
                add_sync_log("Sync cancelled by user.")
                with SYNC_LOCK:
                    SYNC_STATE["status_message"] = "Sync Cancelled"
                    SYNC_STATE["is_running"] = False
                    SYNC_STATE["queue"] = []
                return

            mb = round(it["size"] / (1024 * 1024), 2)
            sz_str = f"{mb} MB" if mb >= 0.1 else f"{round(it['size']/1024, 1)} KB"
            
            with SYNC_LOCK:
                SYNC_STATE["current_file"] = it["name"]
                SYNC_STATE["current_path"] = it["display_path"]
                SYNC_STATE["current_size_str"] = sz_str
                SYNC_STATE["synced_files"] = idx - 1
                SYNC_STATE["remaining_files"] = total - (idx - 1)
                SYNC_STATE["percent"] = int(((idx - 1) / total) * 100) if total else 0
                
                elapsed = max(time.time() - start_t, 1)
                rate = round((idx / elapsed) * 60, 1)
                SYNC_STATE["speed_str"] = f"{rate} files/min"

                SYNC_STATE["queue"] = build_live_window(idx, it, "uploading")

            if it["is_android"]:
                parent_folder_str = "\\".join(it["display_path"].split("\\")[:-1])
            else:
                parent_folder_str = str(Path(it["fpath"]).parent)

            parent_notion_id = self.ensure_folder_path(parent_folder_str)

            file_type = FILE_TYPE_MAP.get(it["ext"], "Other")
            emoji = EMOJI_MAP.get(file_type, "📄")
            payload = {
                "parent": {"database_id": self.db_id},
                "icon": {"type": "emoji", "emoji": emoji},
                "properties": {
                    "Name": {"title": [{"text": {"content": it["name"]}}]},
                    "Type": {"select": {"name": "File"}},
                    "File Type": {"select": {"name": file_type}},
                    "File Extension": {"rich_text": [{"text": {"content": it["ext"]}}]},
                    "File Size": {"number": mb},
                    "Description": {"rich_text": [{"text": {"content": f"Path: {it['display_path']}"}}]},
                    "Favorite": {"checkbox": False}
                }
            }
            if parent_notion_id:
                payload["properties"]["Parent Folder"] = {"relation": [{"id": parent_notion_id}]}

            new_page_id = None
            if it.get("existing_notion_id"):
                # MODIFIED: update existing page in Notion
                new_page_id = it["existing_notion_id"]
                payload["properties"]["Open in Browser"] = {"url": f"https://www.notion.so/{new_page_id}"}
                try:
                    res = requests.patch(f"https://api.notion.com/v1/pages/{new_page_id}", headers=self.headers, json={"properties": payload["properties"]}, timeout=30)
                    status_ok = res.status_code == 200
                except Exception:
                    status_ok = False
            else:
                # NEW: create new page in Notion
                try:
                    res = requests.post("https://api.notion.com/v1/pages", headers=self.headers, json=payload, timeout=30)
                    status_ok = res.status_code == 200
                    if status_ok:
                        new_page_id = res.json()["id"].replace("-", "")
                        cloud_url = f"https://www.notion.so/{new_page_id}"
                        requests.patch(f"https://api.notion.com/v1/pages/{new_page_id}", headers=self.headers, json={"properties": {"Open in Browser": {"url": cloud_url}}}, timeout=15)
                except Exception:
                    status_ok = False

            if status_ok and new_page_id:
                # 1. Save to Git-style persistent state after every file
                if it["is_android"]:
                    android_tracked[it["fpath"]] = {
                        "notion_id": new_page_id,
                        "mtime": it["mtime"],
                        "size": it["size"],
                        "display_path": it["display_path"]
                    }
                else:
                    pc_tracked[it["fpath"]] = {
                        "notion_id": new_page_id,
                        "mtime": it["mtime"],
                        "size": it["size"],
                        "display_path": it["display_path"]
                    }
                save_sync_state(state)

                # 2. Register into live browser cache.
                # IMPORTANT: always store ADB path (it["fpath"]) for Android files,
                # NOT the Windows display path. The /view endpoint uses local_path
                # directly in `adb exec-out cat <local_path>` to stream the file.
                register_drive_cache_item(
                    new_page_id,
                    it["name"],
                    "File",
                    it["ext"],
                    mb,
                    int(it["size"]),
                    parent_notion_id,
                    it["fpath"],   # ADB path for Android; Windows path for PC
                    it.get("mtime", 0)
                )

            with SYNC_LOCK:
                SYNC_STATE["history"].insert(0, {
                    "name": it["name"],
                    "path": it["display_path"],
                    "size_str": sz_str,
                    "time": time.strftime("%H:%M:%S"),
                    "status": "success" if status_ok else "failed"
                })
                if len(SYNC_STATE["history"]) > 200:
                    SYNC_STATE["history"].pop()
                next_active = files_to_sync[idx] if idx < total else None
                SYNC_STATE["queue"] = build_live_window(idx + 1, next_active, "uploading" if next_active else "synced")

            add_sync_log(f"Synced ({idx}/{total}) [{it.get('status_tag','NEW')}]: {it['name']} ({sz_str}) -> {'OK (200)' if status_ok else 'Failed'}")

        with SYNC_LOCK:
            SYNC_STATE["synced_files"] = total
            SYNC_STATE["remaining_files"] = 0
            SYNC_STATE["percent"] = 100
            SYNC_STATE["is_running"] = False
            SYNC_STATE["current_file"] = "Completed"
            SYNC_STATE["status_message"] = f"All {total} changes synchronized ({skipped_count} up-to-date)!"
            SYNC_STATE["queue"] = []

        add_sync_log("✨ Differential sync completed successfully! Saved persistent state.")

def trigger_background_sync(target: str):
    if SYNC_STATE["is_running"]:
        return False, "A sync operation is already in progress."
    runner = BackgroundSyncRunner(DEFAULT_API_KEY, DEFAULT_DB_ID)
    t = threading.Thread(target=runner.run_sync, args=(target,), daemon=True)
    t.start()
    return True, f"Sync started for {target.upper()}"

# ==============================================================================
# DISK CACHING & NOTION DATA ENGINE
# ==============================================================================
def enrich_cache_items():
    with CACHE_LOCK:
        for item_id, item in DRIVE_CACHE["items"].items():
            lp = item.get("local_path", "")
            if lp and Path(lp).exists():
                try:
                    st = Path(lp).stat()
                    item["mtime"] = st.st_mtime
                    item["ctime"] = st.st_ctime
                    item["size_bytes"] = st.st_size
                    item["size_mb"] = round(st.st_size / (1024 * 1024), 2)
                except Exception:
                    pass

def save_disk_cache():
    with CACHE_LOCK:
        try:
            cache_paths = [
                CACHE_FILE,
                Path(__file__).parent / ".notion_drive_cache.json"
            ]
            for cp in cache_paths:
                try:
                    with open(cp, "w", encoding="utf-8") as f:
                        json.dump(DRIVE_CACHE, f)
                except Exception:
                    pass
        except Exception as e:
            print(f"[!] Error saving disk cache: {e}")

def relink_local_disk_folders(cached_items, children_map, root_items):
    """Re-link Local Disk (C:) child folders that have no parent_id."""
    c_disk_id = None
    users_id = None
    for it_id, it in cached_items.items():
        name = it.get("name")
        if name == "Local Disk (C:)":
            c_disk_id = it_id
        elif name == "Users":
            users_id = it_id

    if c_disk_id and users_id:
        if not cached_items[users_id].get("parent_id"):
            cached_items[users_id]["parent_id"] = c_disk_id

    if users_id:
        for it_id, it in cached_items.items():
            if (it.get("name") in ("Default", "nitro", "TEMP", "TEMP.SHISHIR0X")
                and not it.get("parent_id")
                and it_id not in (c_disk_id, users_id)):
                it["parent_id"] = users_id
    elif c_disk_id:
        for it_id, it in cached_items.items():
            if (it.get("name") in ("Users", "Default", "nitro", "TEMP", "TEMP.SHISHIR0X")
                and not it.get("parent_id")
                and it_id != c_disk_id):
                it["parent_id"] = c_disk_id

    # Rebuild children_map and root_items
    children_map.clear()
    root_items.clear()
    for it_id, it in cached_items.items():
        pid = it.get("parent_id")
        if pid and pid in cached_items:
            children_map.setdefault(pid, []).append(it_id)
        else:
            root_items.append(it_id)


def _populate_sqlite_from_cache():
    """Bulk-populate the SQLite index from the in-memory DRIVE_CACHE.
    Called after loading the disk cache so the UI works immediately without
    waiting for a Notion API round-trip.
    """
    try:
        from core.local_index import upsert_many
        with CACHE_LOCK:
            items_snapshot = dict(DRIVE_CACHE["items"])
        if not items_snapshot:
            return
        index_items = []
        seen_paths: set = set()
        for it_id, it in items_snapshot.items():
            raw_path = it.get('local_path', '') or ''
            # Normalize empty local_path to None so SQLite UNIQUE index allows
            # multiple items without a local file (NULL != NULL in SQLite UNIQUE)
            local_path = raw_path.strip() if raw_path.strip() else None
            # Skip duplicate non-null paths (keeps first occurrence)
            if local_path and local_path in seen_paths:
                local_path = None  # demote to NULL to avoid constraint failure
            if local_path:
                seen_paths.add(local_path)
            index_items.append({
                'id': it_id,
                'name': it.get('name', ''),
                'type': it.get('type', 'File'),
                'extension': it.get('extension', ''),
                'size_mb': it.get('size_mb', 0),
                'size_bytes': it.get('size_bytes', 0),
                'mtime': it.get('mtime', 0),
                'ctime': it.get('ctime', 0),
                'created_time': it.get('created_time', ''),
                'last_edited_time': it.get('last_edited_time', ''),
                'parent_id': it.get('parent_id'),
                'local_path': local_path,
                'storage_root': 'Notion Cloud',
                'notion_id': it_id,
                'sync_status': 'synced',
                'starred': 1 if it.get('starred') else 0,
                'archived': 1 if it.get('archived') else 0,
                'last_seen': time.time()
            })
        if index_items:
            upsert_many(index_items)
            logger.info(f"[+] SQLite index populated with {len(index_items)} items from disk cache.")
    except Exception as e:
        logger.error(f"SQLite populate from cache error: {e}")


def load_disk_cache():
    global DRIVE_CACHE
    cache_paths = [
        CACHE_FILE,
        Path(__file__).parent / ".notion_drive_cache.json"
    ]
    for cp in cache_paths:
        if cp.exists():
            try:
                with open(cp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("items"):
                        with CACHE_LOCK:
                            DRIVE_CACHE.update(data)
                        # Re-link Local Disk (C:) folders even when loaded from disk cache
                        relink_local_disk_folders(
                            DRIVE_CACHE["items"],
                            DRIVE_CACHE["children"],
                            DRIVE_CACHE["root_items"]
                        )
                        DRIVE_CACHE["version"] = DRIVE_CACHE.get("version", 0) + 1
                        enrich_cache_items()
                        save_disk_cache()
                        print(f"[+] Loaded {len(DRIVE_CACHE['items'])} items from disk cache!")
                        # Populate SQLite in background so server starts immediately
                        _sqlite_pop_thread = threading.Thread(target=_populate_sqlite_from_cache, daemon=True)
                        _sqlite_pop_thread.start()
                        return True
            except Exception as e:
                print(f"[!] Error loading disk cache: {e}")
    return False

_NOTION_PAGE_CONTENT_CACHE = {}
_NOTION_CONTENT_LOCK = threading.Lock()

def fetch_notion_page_content(page_id: str):
    """
    Fetch Notion page blocks / properties and return (text_content, binary_bytes, redirect_url).
    Caches results for 5 minutes to avoid redundant Notion API roundtrips.
    """
    token = DEFAULT_API_KEY or os.environ.get("NOTION_TOKEN")
    if not token or not page_id:
        return None, None, None

    now = time.time()
    with _NOTION_CONTENT_LOCK:
        if page_id in _NOTION_PAGE_CONTENT_CACHE:
            cached_res, ts = _NOTION_PAGE_CONTENT_CACHE[page_id]
            if now - ts < 300:
                return cached_res

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION
    }
    res = (None, None, None)
    try:
        # 1. Check page properties for Files property
        r_page = requests.get(f"https://api.notion.com/v1/pages/{page_id}", headers=headers, timeout=4)
        if r_page.status_code == 200:
            pdata = r_page.json()
            for prop in pdata.get("properties", {}).values():
                if prop.get("type") == "files":
                    files_list = prop.get("files", [])
                    if files_list:
                        f_item = files_list[0]
                        f_url = f_item.get("file", {}).get("url") or f_item.get("external", {}).get("url")
                        if f_url:
                            res = (None, None, f_url)
                            with _NOTION_CONTENT_LOCK:
                                _NOTION_PAGE_CONTENT_CACHE[page_id] = (res, now)
                            return res

        # 2. Check blocks for file / image / video / code
        r = requests.get(f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100", headers=headers, timeout=4)
        if r.status_code == 200:
            data = r.json()
            text_chunks = []
            for b in data.get("results", []):
                btype = b.get("type")
                if btype in ("code", "paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item", "numbered_list_item", "quote"):
                    rich_texts = b.get(btype, {}).get("rich_text", [])
                    for rt in rich_texts:
                        text_chunks.append(rt.get("plain_text", ""))
                    text_chunks.append("\n")
                elif btype in ("image", "file", "pdf", "video"):
                    file_obj = b.get(btype, {})
                    file_url = None
                    if file_obj.get("type") == "file":
                        file_url = file_obj.get("file", {}).get("url")
                    elif file_obj.get("type") == "external":
                        file_url = file_obj.get("external", {}).get("url")
                    if file_url:
                        res = (None, None, file_url)
                        with _NOTION_CONTENT_LOCK:
                            _NOTION_PAGE_CONTENT_CACHE[page_id] = (res, now)
                        return res
            if text_chunks:
                res = ("".join(text_chunks), None, None)
    except Exception:
        pass

    with _NOTION_CONTENT_LOCK:
        _NOTION_PAGE_CONTENT_CACHE[page_id] = (res, now)
    return res


def populate_cache_from_notion(is_background: bool = False):
    global DRIVE_CACHE
    headers = {
        "Authorization": f"Bearer {DEFAULT_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION
    }

    state = load_sync_state()
    pc_tracked = state.setdefault("files", {})
    android_tracked = state.setdefault("android_files", {})

    try:
        cached_items = {}
        children_map = {}
        root_items = []
        index_items = []  # For SQLite batch insert

        # 1. Fetch ALL Folders first to guarantee 100% accurate hierarchy
        folder_payload = {
            "page_size": 100,
            "filter": {"property": "Type", "select": {"equals": "Folder"}}
        }
        has_more_folders = True
        folder_cursor = None
        while has_more_folders:
            p = dict(folder_payload)
            if folder_cursor:
                p["start_cursor"] = folder_cursor
            res = requests.post(f"https://api.notion.com/v1/databases/{DEFAULT_DB_ID}/query", headers=headers, json=p, timeout=20)
            if res.status_code == 200:
                data = res.json()
                for it in data.get("results", []):
                    it_id = it["id"].replace("-", "")
                    props = it.get("properties", {})
                    title_list = props.get("Name", {}).get("title", [])
                    name = title_list[0].get("plain_text", "") if title_list else ""
                    clean_name = name.replace("📁 ", "").replace("📄 ", "").strip()
                    parents = [pr["id"].replace("-", "") for pr in props.get("Parent Folder", {}).get("relation", [])]
                    parent_id = parents[0] if parents else None

                    starred = bool(props.get("Favorite", {}).get("checkbox"))
                    archived = bool(props.get("Archived", {}).get("checkbox"))

                    cached_items[it_id] = {
                        "id": it_id,
                        "name": clean_name,
                        "type": "Folder",
                        "extension": "",
                        "size_mb": 0,
                        "size_bytes": 0,
                        "mtime": 0,
                        "ctime": 0,
                        "created_time": it.get("created_time", ""),
                        "last_edited_time": it.get("last_edited_time", ""),
                        "parent_id": parent_id,
                        "local_path": "",
                        "starred": starred,
                        "archived": archived
                    }
                    
                    # Add to SQLite index
                    index_items.append({
                        'id': it_id,
                        'name': clean_name,
                        'type': 'Folder',
                        'parent_id': parent_id,
                        'created_time': it.get("created_time", ""),
                        'last_edited_time': it.get("last_edited_time", ""),
                        'storage_root': 'Notion Cloud',
                        'starred': 1 if starred else 0,
                        'archived': 1 if archived else 0
                    })
                has_more_folders = data.get("has_more", False)
                folder_cursor = data.get("next_cursor")
            else:
                break

        # 2. Fetch File items
        file_payload = {
            "page_size": 100,
            "filter": {"property": "Type", "select": {"equals": "File"}}
        }
        has_more_files = True
        file_cursor = None
        while has_more_files:
            p = dict(file_payload)
            if file_cursor:
                p["start_cursor"] = file_cursor
            res = None
            for attempt in range(4):
                try:
                    r = requests.post(f"https://api.notion.com/v1/databases/{DEFAULT_DB_ID}/query", headers=headers, json=p, timeout=20)
                    if r.status_code == 200:
                        res = r.json()
                        break
                    elif r.status_code == 429:
                        time.sleep(1 + attempt)
                except Exception:
                    time.sleep(1)
            if not res:
                break

            for it in res.get("results", []):
                it_id = it["id"].replace("-", "")
                props = it.get("properties", {})
                title_list = props.get("Name", {}).get("title", [])
                name = title_list[0].get("plain_text", "") if title_list else ""
                clean_name = name.replace("📁 ", "").replace("📄 ", "").strip()
                ext_list = props.get("File Extension", {}).get("rich_text", [])
                ext = ext_list[0].get("plain_text", "") if ext_list else ""
                size_mb = props.get("File Size", {}).get("number", 0) or 0
                parents = [pr["id"].replace("-", "") for pr in props.get("Parent Folder", {}).get("relation", [])]
                parent_id = parents[0] if parents else None

                starred = bool(props.get("Favorite", {}).get("checkbox"))
                archived = bool(props.get("Archived", {}).get("checkbox"))

                desc_list = props.get("Description", {}).get("rich_text", [])
                desc = desc_list[0].get("plain_text", "") if desc_list else ""
                local_p = desc.replace("Path: ", "").replace("Local: ", "").replace(" (Updated)", "").replace(" (Modified)", "").strip()

                created_iso = it.get("created_time", "")
                edited_iso = it.get("last_edited_time", "")
                
                # Check existing cache to preserve exact local metadata if available
                existing = DRIVE_CACHE["items"].get(it_id)
                mtime = existing.get("mtime", 0) if existing else 0
                ctime = existing.get("ctime", 0) if existing else 0
                size_bytes = existing.get("size_bytes", int(size_mb * 1024 * 1024)) if existing else int(size_mb * 1024 * 1024)

                if local_p:
                    if "This PC\\OnePlus Nord CE4" in local_p or local_p.startswith("/storage") or local_p.startswith("/sdcard"):
                        android_tracked.setdefault(local_p, {
                            "notion_id": it_id,
                            "mtime": 0,
                            "size": size_bytes,
                            "display_path": local_p
                        })
                    elif Path(local_p).exists():
                        try:
                            stat = Path(local_p).stat()
                            mtime = stat.st_mtime
                            ctime = stat.st_ctime
                            size_bytes = stat.st_size
                            size_mb = round(size_bytes / (1024 * 1024), 2)
                            pc_tracked[local_p] = {
                                "notion_id": it_id,
                                "mtime": mtime,
                                "size": size_bytes,
                                "display_path": local_p
                            }
                        except Exception:
                            pass

                cached_items[it_id] = {
                    "id": it_id,
                    "name": clean_name,
                    "type": "File",
                    "extension": ext,
                    "size_mb": size_mb,
                    "size_bytes": size_bytes,
                    "mtime": mtime,
                    "ctime": ctime,
                    "created_time": created_iso,
                    "last_edited_time": edited_iso,
                    "parent_id": parent_id,
                    "local_path": local_p,
                    "starred": starred,
                    "archived": archived
                }
                
                # Add to SQLite index
                index_items.append({
                    'id': it_id,
                    'name': clean_name,
                    'type': 'File',
                    'extension': ext,
                    'size_mb': size_mb,
                    'size_bytes': size_bytes,
                    'mtime': mtime,
                    'ctime': ctime,
                    'created_time': created_iso,
                    'last_edited_time': edited_iso,
                    'parent_id': parent_id,
                    'local_path': local_p,
                    'storage_root': 'Notion Cloud',
                    'notion_id': it_id,
                    'starred': 1 if starred else 0,
                    'archived': 1 if archived else 0
                })

            has_more_files = res.get("has_more", False)
            file_cursor = res.get("next_cursor")

        if not cached_items:
            return

        # Batch update SQLite index (much faster than individual inserts)
        if index_items:
            try:
                from core.local_index import upsert_many
                upsert_many(index_items)
            except Exception as e:
                logger.error(f"Failed to update SQLite index: {e}")

        with CACHE_LOCK:
            old_item_ids = set(DRIVE_CACHE["items"].keys())
            new_item_ids = set(cached_items.keys())
            deleted_ids = old_item_ids - new_item_ids
            added_ids = new_item_ids - old_item_ids

            has_changes = bool(deleted_ids or added_ids)

            # Re-link Local Disk (C:) and Users if needed
            c_disk_id = None
            for it_id, it in cached_items.items():
                if it["name"] == "Local Disk (C:)":
                    c_disk_id = it_id
                    break

            if c_disk_id:
                for it_id, it in cached_items.items():
                    if it.get("name") in ("Users", "Default", "nitro", "TEMP", "TEMP.SHISHIR0X") and not it.get("parent_id") and it_id != c_disk_id:
                        it["parent_id"] = c_disk_id

            # Rebuild children_map and root_items
            for it_id, it in cached_items.items():
                pid = it.get("parent_id")
                if pid and pid in cached_items:
                    children_map.setdefault(pid, []).append(it_id)
                else:
                    root_items.append(it_id)

            DRIVE_CACHE["items"] = cached_items
            DRIVE_CACHE["children"] = children_map
            DRIVE_CACHE["root_items"] = root_items
            DRIVE_CACHE["version"] = DRIVE_CACHE.get("version", 0) + 1

        enrich_cache_items()
        save_disk_cache()

        # Handle Real-Time Deletions in Sync State & Recent Files
        if deleted_ids:
            state_changed = False
            for bucket in ("files", "android_files", "folders"):
                bdict = state.get(bucket, {})
                for path, info in list(bdict.items()):
                    if info.get("notion_id") in deleted_ids:
                        bdict.pop(path, None)
                        state_changed = True
            if state_changed:
                save_sync_state(state)

            with RECENT_LOCK:
                global RECENT_FILES
                RECENT_FILES = [r for r in RECENT_FILES if r.get("id") not in deleted_ids]
                _save_recent_files()
            
            # Remove deleted items from SQLite index
            try:
                from core.local_index import delete_items_by_notion_id
                for nid in deleted_ids:
                    delete_items_by_notion_id(nid)
            except Exception as e:
                logger.error(f"Failed to clean index: {e}")

            _broadcast_sse("cache_updated", {
                "version": DRIVE_CACHE["version"],
                "deleted_count": len(deleted_ids),
                "deleted_ids": list(deleted_ids)
            })
            logger.info(f"[⚡ Real-time Sync] Detected {len(deleted_ids)} deleted item(s) in Notion DB. Reflected in browser!")
        elif added_ids and is_background:
            _broadcast_sse("cache_updated", {
                "version": DRIVE_CACHE["version"],
                "added_count": len(added_ids)
            })
            logger.info(f"[⚡ Real-time Sync] Detected {len(added_ids)} new item(s) in Notion DB. Reflected in browser!")
        elif not is_background:
            logger.info(f"[+] Notion cache refreshed: {len(cached_items)} items.")

    except Exception as e:
        if not is_background:
            logger.error(f"[!] Notion sync error: {e}")


_WATCHER_THREAD = None
_WATCHER_STOP = False

def start_notion_watcher():
    """Start background watcher thread that auto-syncs Notion database changes every 8 seconds."""
    global _WATCHER_THREAD
    if _WATCHER_THREAD and _WATCHER_THREAD.is_alive():
        return
    
    def _run():
        time.sleep(3)
        while not _WATCHER_STOP:
            try:
                populate_cache_from_notion(is_background=True)
            except Exception:
                pass
            time.sleep(8)

    _WATCHER_THREAD = threading.Thread(target=_run, daemon=True, name="NotionDBWatcher")
    _WATCHER_THREAD.start()
    print("[+] Real-time Notion Database Watcher active (8s auto-sync)")


# ==============================================================================
# HTTP SERVER ROUTING
# ==============================================================================


# ── ADB Caching & Concurrency Control ──────────────────────────────────────────
_SDCARD_ID_CACHE = {"id": "4A21-0000", "ts": 0}
ADB_SEMAPHORE = threading.Semaphore(2)  # Max 2 concurrent ADB file transfers

def get_cached_sdcard_id() -> str:
    """Return cached SD card volume ID without spawning a subprocess every time."""
    now = time.time()
    if now - _SDCARD_ID_CACHE["ts"] < 60:
        return _SDCARD_ID_CACHE["id"]
    try:
        out = subprocess.check_output(["adb", "shell", "ls", "/storage"], timeout=2).decode("utf-8", errors="ignore")
        for s in out.split():
            if s not in ("emulated", "self", "persist", "sdcard0"):
                _SDCARD_ID_CACHE["id"] = s
                _SDCARD_ID_CACHE["ts"] = now
                return s
    except Exception:
        pass
    return _SDCARD_ID_CACHE["id"]


def resolve_android_path(p: str) -> str:
    """Canonicalize any Android path (display or ADB) into a valid ADB Linux path."""
    clean = re.sub(r"[\\/]+", "/", p.strip())
    # Already canonical ADB path
    if clean.startswith("/storage/emulated/0/"):
        return clean
    if clean.startswith("/sdcard/"):
        return "/storage/emulated/0/" + clean[len("/sdcard/"):]
    
    # SD card canonical
    m = re.match(r"^/storage/([0-9A-Fa-f\-]+)/(.+)$", clean)
    if m and m.group(1) not in ("emulated", "self"):
        return clean

    # SD card display path
    if "sd card" in clean.lower() or "4a21-0000" in clean.lower():
        rel = re.sub(r"^.*?(?:sd card|4a21-0000)/?", "", clean, flags=re.IGNORECASE).lstrip("/")
        sd_id = get_cached_sdcard_id()
        return f"/storage/{sd_id}/{rel}"

    # Internal storage display path
    rel = re.sub(r"^.*?(?:internal shared storage|internal storage|storage/emulated/0)/?", "", clean, flags=re.IGNORECASE).lstrip("/")
    return f"/storage/emulated/0/{rel}"


class NotionServerHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Suppress standard HTTP request logs to avoid Unicode/BrokenPipe errors
        # on Windows when filenames contain emojis or non-Latin characters.
        pass

    def log_request(self, code='-', size='-'):
        pass

    def log_error(self, fmt, *args):
        # Log errors safely, suppressing normal client disconnects (e.g. WinError 10053 / 10054)
        try:
            msg = (fmt % args) if args else str(fmt)
            if any(k in msg for k in ("10053", "10054", "10058", "Broken pipe", "ConnectionResetError", "ConnectionAbortedError")):
                return
            msg_safe = msg.encode('ascii', errors='replace').decode('ascii')
            print(f"[!] Server: {msg_safe}", file=sys.stderr)
        except Exception:
            pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass  # Client disconnected — normal during browser tab switches
        except UnicodeEncodeError:
            pass  # Emoji in path — suppress silently
        except Exception as e:
            try:
                err_str = str(e).encode('ascii', errors='replace').decode('ascii')
                if not any(k in err_str for k in ("10053", "10054", "10058", "Broken pipe", "abort")):
                    print(f"[!] Request handler error: {err_str}", file=sys.stderr)
            except Exception:
                pass


    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        # ── Auth Endpoints (Always Public) ──────────────────────────────────
        if parsed.path == "/api/auth/login":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body.decode("utf-8"))
                entered_pw = data.get("password", "")
                drive_pw = os.environ.get("DRIVE_PASSWORD", getattr(config, "DRIVE_PASSWORD", "")).strip()
                if not drive_pw or entered_pw == drive_pw:
                    token = compute_auth_token(drive_pw if drive_pw else "open")
                    resp = json.dumps({"success": True, "token": token}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(resp)))
                    self.send_header("Set-Cookie", f"notion_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")
                    self.end_headers()
                    self.wfile.write(resp)
                    return
                else:
                    resp = json.dumps({"success": False, "error": "Incorrect password. Access denied."}).encode("utf-8")
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                    return
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                return

        if parsed.path == "/api/auth/logout":
            resp = json.dumps({"success": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.send_header("Set-Cookie", "notion_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            self.end_headers()
            self.wfile.write(resp)
            return

        # ── Security Gate: Require Auth for all other actions ───────────────
        if parsed.path.startswith("/api/") and parsed.path != "/api/sync/update":
            if not check_authenticated(self.headers):
                resp = json.dumps({"error": "Unauthorized. Please enter password."}).encode("utf-8")
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
                return

        if parsed.path == "/api/sync/start":
            target = params.get("target", ["all"])[0].lower()
            ok, msg = trigger_background_sync(target)
            resp_bytes = json.dumps({"success": ok, "message": msg}).encode("utf-8")
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        if parsed.path == "/api/sync/cancel":
            global CANCEL_SYNC_FLAG
            CANCEL_SYNC_FLAG = True
            add_sync_log("Sync cancellation requested by user.")
            resp_bytes = b'{"success": true, "message": "Cancelled"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            return

        if parsed.path == "/api/sync/update":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body.decode("utf-8"))
                with SYNC_LOCK:
                    for k, v in data.items():
                        if k in SYNC_STATE and k not in ("history", "logs"):
                            SYNC_STATE[k] = v
                    if "is_syncing" in data:
                        SYNC_STATE["is_running"] = bool(data["is_syncing"])
                    if "current_index" in data:
                        SYNC_STATE["synced_files"] = data["current_index"]
                    if "log_message" in data and data["log_message"]:
                        add_sync_log(data["log_message"])
                    if "history_item" in data and data["history_item"]:
                        SYNC_STATE["history"].insert(0, data["history_item"])
                        if len(SYNC_STATE["history"]) > 100:
                            SYNC_STATE["history"].pop()
                resp_bytes = b'{"success": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp_bytes)))
                self.end_headers()
                self.wfile.write(resp_bytes)
                return
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                return

        if parsed.path == "/api/upload":
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > 100 * 1024 * 1024:  # 100 MB limit
                    raise ValueError("File too large (max 100 MB)")
                body = self.rfile.read(length)
                upload_req = json.loads(body.decode("utf-8"))
                
                # upload_req: {"name": str, "rel_path": str, "data_b64": str, "parent_folder_id": str}
                import base64
                file_name = upload_req.get("name", "untitled")
                rel_path = upload_req.get("rel_path", file_name)
                parent_id = upload_req.get("parent_folder_id")
                data_bytes = base64.b64decode(upload_req.get("data_b64", ""))
                
                # Sanitize filename and prevent path traversal
                safe_name = Path(file_name).name  # strips any directory components
                if not safe_name:
                    raise ValueError("Invalid filename")
                
                # Build safe relative path - prevent traversal outside UPLOADS_DIR
                safe_rel = Path(rel_path).name  # Just use filename, ignore any path components
                local_file_path = UPLOADS_DIR / safe_rel
                # Final safety check: ensure resolved path is within UPLOADS_DIR
                try:
                    local_file_path.resolve().relative_to(UPLOADS_DIR.resolve())
                except ValueError:
                    raise ValueError("Path traversal detected")
                
                local_file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(local_file_path, "wb") as f:
                    f.write(data_bytes)
                
                file_size = len(data_bytes)
                size_mb = round(file_size / (1024 * 1024), 4)
                ext = Path(safe_name).suffix.lower()
                ftype, emoji = F.classify_file(ext) if ext else ("Other", "📄")
                
                # Create Notion page
                api_client = BackgroundSyncRunner(DEFAULT_API_KEY, DEFAULT_DB_ID)
                target_parent_notion_id = parent_id
                
                # If rel_path contains subfolders, build them in Notion
                parts = [p for p in rel_path.replace("\\", "/").split("/")[:-1] if p]
                if parts:
                    target_parent_notion_id = api_client.build_folder_path(parts, parent_id or api_client.ensure_root("Local Disk (C:)"))
                elif not target_parent_notion_id:
                    target_parent_notion_id = api_client.ensure_root("Local Disk (C:)")

                payload = {
                    "parent": {"database_id": DEFAULT_DB_ID},
                    "icon": {"type": "emoji", "emoji": emoji},
                    "properties": {
                        "Name": {"title": [{"text": {"content": safe_name}}]},
                        "Type": {"select": {"name": "File"}},
                        "File Type": {"select": {"name": ftype}},
                        "File Extension": {"rich_text": [{"text": {"content": ext}}]},
                        "File Size": {"number": size_mb},
                        "Description": {"rich_text": [{"text": {"content": f"Path: {local_file_path}"}}]},
                        "Favorite": {"checkbox": False}
                    }
                }
                if target_parent_notion_id:
                    payload["properties"]["Parent Folder"] = {"relation": [{"id": target_parent_notion_id}]}

                res = requests.post("https://api.notion.com/v1/pages", headers=api_client.headers, json=payload, timeout=30)
                if res.status_code == 200:
                    new_page_id = res.json()["id"].replace("-", "")
                    cloud_url = f"https://www.notion.so/{new_page_id}"
                    requests.patch(f"https://api.notion.com/v1/pages/{new_page_id}", headers=api_client.headers, json={"properties": {"Open in Browser": {"url": cloud_url}}}, timeout=15)
                    register_drive_cache_item(
                        new_page_id, safe_name, "File", ext, size_mb, file_size,
                        target_parent_notion_id, str(local_file_path), time.time()
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True, "id": new_page_id, "name": safe_name}).encode("utf-8"))
                    return

                self.send_response(500)
                self.end_headers()
                return
            except Exception as e:
                import logging
                logging.getLogger("notion_server").error(f"Upload error: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if parsed.path == "/api/file/delete":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body.decode("utf-8"))
                file_id = data.get("id", "").replace("-", "")
                local_path = data.get("path", "")
                
                if not file_id and not local_path:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"success": false, "error": "Missing id or path"}')
                    return
                
                # 1. Archive page in Notion if ID is present
                if file_id:
                    try:
                        runner = BackgroundSyncRunner(DEFAULT_API_KEY, DEFAULT_DB_ID)
                        del_res = requests.patch(
                            f"https://api.notion.com/v1/pages/{file_id}",
                            headers=runner.headers,
                            json={"properties": {"Archived": {"checkbox": True}}},
                            timeout=15
                        )
                        if del_res.status_code != 200:
                            # "Archived" may not exist on older databases → native archive fallback
                            requests.patch(
                                f"https://api.notion.com/v1/pages/{file_id}",
                                headers=runner.headers,
                                json={"archived": True},
                                timeout=15
                            )
                    except Exception as e:
                        logger.warning(f"Failed to archive Notion page {file_id}: {e}")
                
                # 2. Soft-delete in SQLite index (item is kept for Trash / Restore)
                try:
                    from core.local_index import set_archived
                    if file_id:
                        set_archived(file_id, True)
                except Exception as e:
                    logger.warning(f"Index archive warning: {e}")
                
                # 3. Mark archived in-memory DRIVE_CACHE
                with CACHE_LOCK:
                    if file_id and file_id in DRIVE_CACHE["items"]:
                        DRIVE_CACHE["items"][file_id]["archived"] = True
                    DRIVE_CACHE["version"] = DRIVE_CACHE.get("version", 0) + 1
                
                save_disk_cache()
                
                # 4. Broadcast SSE deletion event
                _broadcast_sse("file_deleted", {
                    "id": file_id,
                    "path": local_path,
                    "version": DRIVE_CACHE["version"]
                })
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "id": file_id}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        # ── Trash listing ──────────────────────────────────────────────────────
        if parsed.path == "/api/trash":
            try:
                from core.local_index import get_trash
                items = get_trash(limit=200)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"items": items}).encode("utf-8"))
                return
            except Exception as e:
                logger.error(f"Trash query error: {e}")
                # Fallback: scan in-memory cache for archived items
                with CACHE_LOCK:
                    items = [dict(it) for it in DRIVE_CACHE["items"].values() if it.get("archived")]
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"items": items}).encode("utf-8"))
                return

        # ── Folder & file management endpoints (used by the Next.js web app) ──
        if parsed.path == "/api/folder/create":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body.decode("utf-8"))
                name = str(data.get("name", "")).strip()
                parent_id = (data.get("parent_folder_id") or "").replace("-", "") or None
                if not name:
                    raise ValueError("Folder name is required")
                safe_name = Path(name).name.strip()
                if not safe_name:
                    raise ValueError("Invalid folder name")

                runner = BackgroundSyncRunner(DEFAULT_API_KEY, DEFAULT_DB_ID)
                payload = {
                    "parent": {"database_id": DEFAULT_DB_ID},
                    "icon": {"type": "emoji", "emoji": "📁"},
                    "properties": {
                        "Name": {"title": [{"text": {"content": safe_name}}]},
                        "Type": {"select": {"name": "Folder"}},
                        "Favorite": {"checkbox": False},
                        "Archived": {"checkbox": False}
                    }
                }
                if parent_id:
                    payload["properties"]["Parent Folder"] = {"relation": [{"id": parent_id}]}

                res = requests.post("https://api.notion.com/v1/pages", headers=runner.headers, json=payload, timeout=20)
                if res.status_code == 200:
                    nid = res.json()["id"].replace("-", "")
                    register_drive_cache_item(nid, safe_name, "Folder", "", 0, 0, parent_id, "")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True, "id": nid, "name": safe_name, "parent_folder_id": parent_id}).encode("utf-8"))
                    return
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": f"Notion error: {res.status_code}"}).encode("utf-8"))
                return
            except Exception as e:
                logger.error(f"Folder create error: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if parsed.path == "/api/file/rename":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body.decode("utf-8"))
                item_id = data.get("id", "").replace("-", "")
                new_name = str(data.get("name", "")).strip()
                if not item_id or not new_name:
                    raise ValueError("Missing id or name")
                safe_name = Path(new_name).name.strip()
                if not safe_name:
                    raise ValueError("Invalid name")

                runner = BackgroundSyncRunner(DEFAULT_API_KEY, DEFAULT_DB_ID)
                res = requests.patch(
                    f"https://api.notion.com/v1/pages/{item_id}",
                    headers=runner.headers,
                    json={"properties": {"Name": {"title": [{"text": {"content": safe_name}}]}}},
                    timeout=15,
                )
                if res.status_code == 200:
                    with CACHE_LOCK:
                        entry = DRIVE_CACHE["items"].get(item_id)
                        if entry:
                            entry["name"] = safe_name
                            entry["last_edited_time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
                            DRIVE_CACHE["version"] = DRIVE_CACHE.get("version", 0) + 1
                    if entry:
                        _sync_cache_entry_to_index(entry)
                    _broadcast_sse("file_updated", {"id": item_id, "name": safe_name, "version": DRIVE_CACHE.get("version", 0)})
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True, "id": item_id, "name": safe_name}).encode("utf-8"))
                    return
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": f"Notion error: {res.status_code}"}).encode("utf-8"))
                return
            except Exception as e:
                logger.error(f"Rename error: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if parsed.path == "/api/file/move":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body.decode("utf-8"))
                item_id = data.get("id", "").replace("-", "")
                parent_id = (data.get("parent_folder_id") or "").replace("-", "") or None
                if not item_id:
                    raise ValueError("Missing id")

                runner = BackgroundSyncRunner(DEFAULT_API_KEY, DEFAULT_DB_ID)
                props = {"Parent Folder": {"relation": [{"id": parent_id}]} if parent_id else {"relation": []}}
                res = requests.patch(
                    f"https://api.notion.com/v1/pages/{item_id}",
                    headers=runner.headers,
                    json={"properties": props},
                    timeout=15,
                )
                if res.status_code == 200:
                    with CACHE_LOCK:
                        entry = DRIVE_CACHE["items"].get(item_id)
                        if entry:
                            entry = dict(entry)
                            entry["parent_id"] = parent_id
                            entry["last_edited_time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if entry:
                        register_drive_cache_item(
                            item_id, entry.get("name", ""), entry.get("type", "File"), entry.get("extension", ""),
                            entry.get("size_mb", 0), entry.get("size_bytes", 0), parent_id,
                            entry.get("local_path", ""), entry.get("mtime", 0),
                            starred=bool(entry.get("starred")), archived=bool(entry.get("archived")),
                        )
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True, "id": item_id, "parent_folder_id": parent_id}).encode("utf-8"))
                    return
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": f"Notion error: {res.status_code}"}).encode("utf-8"))
                return
            except Exception as e:
                logger.error(f"Move error: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if parsed.path == "/api/file/star":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body.decode("utf-8"))
                item_id = data.get("id", "").replace("-", "")
                starred = bool(data.get("starred", True))
                if not item_id:
                    raise ValueError("Missing id")

                runner = BackgroundSyncRunner(DEFAULT_API_KEY, DEFAULT_DB_ID)
                res = requests.patch(
                    f"https://api.notion.com/v1/pages/{item_id}",
                    headers=runner.headers,
                    json={"properties": {"Favorite": {"checkbox": starred}}},
                    timeout=15,
                )
                if res.status_code == 200:
                    with CACHE_LOCK:
                        entry = DRIVE_CACHE["items"].get(item_id)
                        if entry:
                            entry["starred"] = starred
                            entry["last_edited_time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
                            DRIVE_CACHE["version"] = DRIVE_CACHE.get("version", 0) + 1
                    if entry:
                        _sync_cache_entry_to_index(entry)
                    _broadcast_sse("file_updated", {"id": item_id, "starred": starred, "version": DRIVE_CACHE.get("version", 0)})
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True, "id": item_id, "starred": starred}).encode("utf-8"))
                    return
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": f"Notion error: {res.status_code}"}).encode("utf-8"))
                return
            except Exception as e:
                logger.error(f"Star error: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if parsed.path == "/api/file/restore":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body.decode("utf-8"))
                item_id = data.get("id", "").replace("-", "")
                if not item_id:
                    raise ValueError("Missing id")

                runner = BackgroundSyncRunner(DEFAULT_API_KEY, DEFAULT_DB_ID)
                res = requests.patch(
                    f"https://api.notion.com/v1/pages/{item_id}",
                    headers=runner.headers,
                    json={"properties": {"Archived": {"checkbox": False}}},
                    timeout=15,
                )
                if res.status_code == 200:
                    with CACHE_LOCK:
                        entry = DRIVE_CACHE["items"].get(item_id)
                        if entry:
                            entry["archived"] = False
                            entry["last_edited_time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
                            DRIVE_CACHE["version"] = DRIVE_CACHE.get("version", 0) + 1
                    if entry:
                        _sync_cache_entry_to_index(entry)
                    else:
                        try:
                            from core.local_index import set_archived
                            set_archived(item_id, False)
                        except Exception:
                            pass
                    _broadcast_sse("file_updated", {"id": item_id, "archived": False, "version": DRIVE_CACHE.get("version", 0)})
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True, "id": item_id}).encode("utf-8"))
                    return
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": f"Notion error: {res.status_code}"}).encode("utf-8"))
                return
            except Exception as e:
                logger.error(f"Restore error: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if parsed.path == "/api/file/delete-permanent":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body.decode("utf-8"))
                item_id = data.get("id", "").replace("-", "")
                if not item_id:
                    raise ValueError("Missing id")

                runner = BackgroundSyncRunner(DEFAULT_API_KEY, DEFAULT_DB_ID)
                try:
                    requests.patch(
                        f"https://api.notion.com/v1/pages/{item_id}",
                        headers=runner.headers,
                        json={"archived": True},
                        timeout=15,
                    )
                except Exception as e:
                    logger.warning(f"Native archive failed: {e}")

                try:
                    from core.local_index import delete_item
                    delete_item(item_id)
                except Exception as e:
                    logger.warning(f"Index delete warning: {e}")

                with CACHE_LOCK:
                    DRIVE_CACHE["items"].pop(item_id, None)
                    for pid, cids in DRIVE_CACHE["children"].items():
                        if item_id in cids:
                            cids.remove(item_id)
                    if item_id in DRIVE_CACHE["root_items"]:
                        DRIVE_CACHE["root_items"].remove(item_id)
                    DRIVE_CACHE["version"] = DRIVE_CACHE.get("version", 0) + 1

                save_disk_cache()
                _broadcast_sse("file_deleted", {"id": item_id, "permanent": True, "version": DRIVE_CACHE.get("version", 0)})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "id": item_id}).encode("utf-8"))
                return
            except Exception as e:
                logger.error(f"Permanent delete error: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if parsed.path == "/api/upload-multipart":
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > 100 * 1024 * 1024:  # 100 MB limit
                    raise ValueError("File too large (max 100 MB)")
                body = self.rfile.read(length)

                from email import policy
                from email.parser import BytesParser
                # BaseHTTPRequestHandler strips request headers, so the email parser
                # never sees the Content-Type that carries the multipart boundary.
                # Reconstruct the message headers before parsing the body.
                content_type = self.headers.get("Content-Type", "multipart/form-data")
                msg = BytesParser(policy=policy.default).parsebytes(
                    f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
                )

                file_name = None
                file_bytes = b""
                parent_id = None
                for part in msg.iter_parts():
                    filename = part.get_filename()
                    if filename:
                        file_name = filename
                        file_bytes = part.get_payload(decode=True) or b""
                    elif part.get_param("name", header="content-disposition") == "folder_id":
                        raw = part.get_payload(decode=True) or b""
                        parent_id = raw.decode("utf-8", errors="ignore").strip().replace("-", "") or None

                if not file_name:
                    raise ValueError("No file part in multipart upload")
                safe_name = Path(file_name).name
                if not safe_name:
                    raise ValueError("Invalid filename")

                ok, new_id, err = _store_uploaded_file(safe_name, parent_id, file_bytes)
                if ok:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True, "id": new_id, "name": safe_name}).encode("utf-8"))
                else:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "error": err}).encode("utf-8"))
                return
            except Exception as e:
                logger.error(f"Multipart upload error: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return

        if parsed.path == "/api/db/optimize":
            try:
                from core.local_index import get_connection
                conn = get_connection()
                conn.execute("PRAGMA optimize;")
                conn.execute("VACUUM;")
                conn.close()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"success": true, "message": "Database optimized and vacuumed."}')
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode("utf-8"))
                return
        
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/api/auth/status":
            drive_pw = os.environ.get("DRIVE_PASSWORD", getattr(config, "DRIVE_PASSWORD", "")).strip()
            is_auth = check_authenticated(self.headers)
            resp = json.dumps({"protected": bool(drive_pw), "authenticated": is_auth}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return

        # HTML GUI removed — UI served by Next.js app on port 3000
        if parsed.path == "/" or parsed.path == "/index.html":
            resp = json.dumps({"service": "notion-drive-api", "status": "running", "ui": "http://localhost:3000"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return

        # ── Security Gate: Require Auth for all data routes ─────────────────
        if not check_authenticated(self.headers):
            if parsed.path.startswith("/api/"):
                resp = json.dumps({"error": "Unauthorized"}).encode("utf-8")
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
                return
            else:
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return

        if parsed.path == "/api/sync/status":
            with SYNC_LOCK:
                st_copy = {
                    "is_running": SYNC_STATE.get("is_running", False),
                    "current_target": SYNC_STATE.get("current_target", "ALL"),
                    "total_files": SYNC_STATE.get("total_files", 0),
                    "synced_files": SYNC_STATE.get("synced_files", 0),
                    "remaining_files": SYNC_STATE.get("remaining_files", 0),
                    "percent": SYNC_STATE.get("percent", 0),
                    "current_file": SYNC_STATE.get("current_file", "None"),
                    "current_path": SYNC_STATE.get("current_path", ""),
                    "current_size_str": SYNC_STATE.get("current_size_str", "0 KB"),
                    "speed_str": SYNC_STATE.get("speed_str", "0 files/min"),
                    "status_message": SYNC_STATE.get("status_message", "Ready to sync"),
                    "queue": list(SYNC_STATE.get("queue", [])),
                    "history": list(SYNC_STATE.get("history", [])),
                    "logs": list(SYNC_STATE.get("logs", []))[-50:],
                }
            with CACHE_LOCK:
                st_copy["cache_version"] = DRIVE_CACHE.get("version", 0)
            try:
                payload = json.dumps(st_copy).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception:
                pass
            return

        if parsed.path == "/api/drive":
            folder_id = params.get("folder_id", [None])[0]
            if folder_id:
                folder_id = folder_id.replace("-", "")

            # Pagination + sort params
            try:
                offset = int(params.get("offset", ["0"])[0])
            except ValueError:
                offset = 0
            try:
                limit = min(int(params.get("limit", ["200"])[0]), 500)
            except ValueError:
                limit = 200
            sort_key = params.get("sort", ["name"])[0]
            sort_order = params.get("order", ["asc"])[0]  # asc | desc
            type_filter = params.get("type", [""])[0].lower()  # folder|file|''

            # Use SQLite index for scalable queries (handles 10k+ items efficiently)
            # Fall back to in-memory cache when SQLite has no data yet
            _use_fallback = False
            try:
                from core.local_index import get_children, get_breadcrumbs
                
                folders, total_folders, _ = get_children(
                    parent_id=folder_id,
                    offset=0,
                    limit=500,  # Always return all folders (usually small number)
                    sort=sort_key,
                    order=sort_order,
                    type_filter='Folder'
                )
                
                files, total_files, has_more = get_children(
                    parent_id=folder_id,
                    offset=offset,
                    limit=limit,
                    sort=sort_key,
                    order=sort_order,
                    type_filter='File'
                )

                # If both are empty but cache has data, fall back to in-memory cache
                # (SQLite not yet populated from cache, happens during first ~2s after startup)
                if not folders and not files:
                    with CACHE_LOCK:
                        cache_has_data = bool(DRIVE_CACHE["items"])
                    if cache_has_data:
                        _use_fallback = True
                        # Also trigger async SQLite population if not already done
                        _sqlite_pop_thread = threading.Thread(target=_populate_sqlite_from_cache, daemon=True)
                        _sqlite_pop_thread.start()
                
                if not _use_fallback:
                    breadcrumbs = []
                    if folder_id:
                        breadcrumbs = get_breadcrumbs(folder_id)
                    
                    resp_data = {
                        "folders": folders,
                        "files": files,
                        "total_files": total_files,
                        "total_folders": total_folders,
                        "offset": offset,
                        "limit": limit,
                        "has_more": has_more,
                        "breadcrumbs": breadcrumbs,
                        "version": DRIVE_CACHE.get("version", 0)
                    }
                
            except Exception as e:
                logger.error(f"Index query error, falling back to cache: {e}")
                _use_fallback = True

            if _use_fallback:
                # Fallback to in-memory cache
                with CACHE_LOCK:
                    if folder_id:
                        child_ids = list(DRIVE_CACHE["children"].get(folder_id, []))
                    else:
                        ROOT_DEVICE_NAMES = {
                            "Local Disk (C:)", "Local Disk (D:)",
                            "Internal shared storage", "SD card",
                            "Internal Storage", "SD Card"
                        }
                        child_ids = [cid for cid, it in DRIVE_CACHE["items"].items()
                                     if it.get("name") in ROOT_DEVICE_NAMES and not it.get("parent_id")]
                        if not child_ids:
                            child_ids = list(DRIVE_CACHE["root_items"])

                    folders = []
                    files = []
                    for cid in child_ids:
                        item = DRIVE_CACHE["items"].get(cid)
                        if not item or item.get("archived"):
                            continue
                        if item.get("type") == "Folder":
                            sub_count = len(DRIVE_CACHE["children"].get(cid, []))
                            item_copy = dict(item)
                            item_copy["item_count"] = sub_count
                            folders.append(item_copy)
                        else:
                            files.append(dict(item))

                    reverse = sort_order == "desc"
                    def _sort_val(x):
                        v = x.get(sort_key, "")
                        return v.lower() if isinstance(v, str) else (v or 0)
                    folders.sort(key=_sort_val, reverse=reverse)
                    files.sort(key=_sort_val, reverse=reverse)

                    if type_filter == "folder":
                        files = []
                    elif type_filter == "file":
                        folders = []

                    total_files = len(files)
                    paged_files = files[offset:offset + limit]
                    has_more = (offset + limit) < total_files

                    breadcrumbs = []
                    curr = folder_id
                    seen = set()
                    while curr:
                        if curr in seen:
                            break
                        seen.add(curr)
                        c_item = DRIVE_CACHE["items"].get(curr)
                        if not c_item:
                            break
                        breadcrumbs.insert(0, {"id": curr, "name": c_item["name"]})
                        curr = c_item.get("parent_id")

                    resp_data = {
                        "folders": folders,
                        "files": paged_files,
                        "total_files": total_files,
                        "total_folders": len(folders),
                        "offset": offset,
                        "limit": limit,
                        "has_more": has_more,
                        "breadcrumbs": breadcrumbs,
                        "version": DRIVE_CACHE.get("version", 0)
                    }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp_data).encode("utf-8"))
            return

        if parsed.path == "/api/search":
            query = params.get("q", [""])[0].strip()
            category = params.get("cat", ["all"])[0].strip().lower()
            matching_files = []
            matching_folders = []
            
            # Use SQLite index for fast search with category filtering
            try:
                from core.local_index import search_items
                matching_folders, matching_files = search_items(query, category=category, limit=120)
            except Exception as e:
                logger.error(f"Search index error, falling back to cache: {e}")
                with CACHE_LOCK:
                    for it in DRIVE_CACHE["items"].values():
                        if not query or query.lower() in it["name"].lower():
                            if it["type"] == "Folder":
                                if category in ('all', 'folder'):
                                    matching_folders.append(dict(it))
                            else:
                                if category == 'all':
                                    matching_files.append(dict(it))
                                elif category == 'image' and it.get("extension", "").lower().replace(".", "") in ('jpg', 'jpeg', 'png', 'webp', 'gif', 'svg'):
                                    matching_files.append(dict(it))
                                elif category == 'document' and it.get("extension", "").lower().replace(".", "") in ('pdf', 'doc', 'docx', 'txt', 'xls', 'xlsx', 'csv', 'ppt', 'pptx'):
                                    matching_files.append(dict(it))
                                elif category == 'video' and it.get("extension", "").lower().replace(".", "") in ('mp4', 'mkv', 'mov', 'webm'):
                                    matching_files.append(dict(it))
                                elif category == 'audio' and it.get("extension", "").lower().replace(".", "") in ('mp3', 'wav', 'ogg', 'm4a', 'opus'):
                                    matching_files.append(dict(it))
                                elif category == 'code' and it.get("extension", "").lower().replace(".", "") in ('py', 'js', 'ts', 'html', 'css', 'json', 'yaml', 'sh', 'sql'):
                                    matching_files.append(dict(it))

            bc_title = f"Search: '{query}'" if query else f"Filter: {category.capitalize()}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "folders": matching_folders[:40],
                "files": matching_files[:80],
                "total_files": len(matching_files),
                "breadcrumbs": [{"id": None, "name": bc_title}]
            }).encode("utf-8"))
            return

        if parsed.path == "/api/stats":
            # Use SQLite index for fast stats
            try:
                from core.local_index import get_stats
                stats = get_stats()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "total_mb": stats["total_size_mb"],
                    "total_files": stats["total_files"]
                }).encode("utf-8"))
            except Exception as e:
                logger.error(f"Stats error: {e}")
                with CACHE_LOCK:
                    total_size_mb = sum(it.get("size_mb", 0) for it in DRIVE_CACHE["items"].values() if it["type"] == "File")
                    total_files = sum(1 for it in DRIVE_CACHE["items"].values() if it["type"] == "File")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "total_mb": round(total_size_mb, 2),
                    "total_files": total_files
                }).encode("utf-8"))
            return

        if parsed.path == "/api/recent":
            # Use SQLite index for recent files
            try:
                from core.local_index import get_recent
                recent_items = get_recent(limit=50)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"files": recent_items}).encode("utf-8"))
            except Exception as e:
                logger.error(f"Recent files error: {e}")
                with RECENT_LOCK:
                    recent_copy = list(RECENT_FILES[:50])
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"files": recent_copy}).encode("utf-8"))
            return

        if parsed.path == "/api/starred":
            # Use SQLite index for starred items
            try:
                from core.local_index import get_starred
                starred = get_starred()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"items": starred[:100]}).encode("utf-8"))
            except Exception as e:
                logger.error(f"Starred error: {e}")
                starred = []
                with CACHE_LOCK:
                    for it in DRIVE_CACHE["items"].values():
                        if it.get("starred") or it.get("type") == "Folder" and it.get("name") in (
                            "Local Disk (C:)", "Local Disk (D:)",
                            "Internal shared storage", "SD card", "Internal Storage", "SD Card"
                        ):
                            starred.append(dict(it))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"items": starred[:100]}).encode("utf-8"))
            return

        if parsed.path == "/api/trash":
            # List soft-deleted (archived) items for the trash view
            try:
                from core.local_index import get_trash
                items = get_trash(limit=200)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"items": items}).encode("utf-8"))
            except Exception as e:
                logger.error(f"Trash GET error: {e}")
                with CACHE_LOCK:
                    items = [dict(it) for it in DRIVE_CACHE["items"].values() if it.get("archived")]
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"items": items[:200]}).encode("utf-8"))
            return

        if parsed.path == "/api/storage":
            devices = []
            for letter in ("C", "D", "E", "F"):
                p = Path(f"{letter}:/")
                if p.exists():
                    try:
                        import shutil
                        total, used, free = shutil.disk_usage(p)
                        devices.append({
                            "name": f"Local Disk ({letter}:)",
                            "type": "local",
                            "icon": "hard-drive",
                            "total_gb": round(total / (1024**3), 1),
                            "used_gb": round(used / (1024**3), 1),
                            "free_gb": round(free / (1024**3), 1),
                            "percent": round(used / total * 100, 1) if total else 0
                        })
                    except Exception:
                        devices.append({"name": f"Local Disk ({letter}:)", "type": "local", "icon": "hard-drive"})
            # Android (estimate from cache)
            with CACHE_LOCK:
                android_files = [it for it in DRIVE_CACHE["items"].values()
                                 if it["type"] == "File" and it.get("local_path", "").startswith("/storage")]
            if android_files:
                android_size_mb = sum(f.get("size_mb", 0) for f in android_files)
                devices.append({
                    "name": "Mobile Storage",
                    "type": "android",
                    "icon": "mobile-screen-button",
                    "synced_files": len(android_files),
                    "synced_mb": round(android_size_mb, 1)
                })
            with CACHE_LOCK:
                cloud_files = sum(1 for it in DRIVE_CACHE["items"].values() if it["type"] == "File")
                cloud_mb = sum(it.get("size_mb", 0) for it in DRIVE_CACHE["items"].values() if it["type"] == "File")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "devices": devices,
                "cloud_files": cloud_files,
                "cloud_mb": round(cloud_mb, 2)
            }).encode("utf-8"))
            return

        if parsed.path == "/api/events":
            # Server-Sent Events endpoint
            q: _queue.SimpleQueue = _queue.SimpleQueue()
            with _SSE_LOCK:
                _SSE_CLIENTS.append(q)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                # Send initial heartbeat
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=25)
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                    except _queue.Empty:
                        # Keepalive ping
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with _SSE_LOCK:
                    if q in _SSE_CLIENTS:
                        _SSE_CLIENTS.remove(q)
            return

        if parsed.path == "/api/refresh":
            def _bg_refresh():
                populate_cache_from_notion(is_background=False)
            threading.Thread(target=_bg_refresh, daemon=True).start()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","message":"Refreshing from Notion Cloud..."}')
            return

        # 1. Resolve item from ID or Path
        file_id = params.get("id", [None])[0]
        file_path_str = (params.get("path", [None])[0] or 
                         params.get("file", [None])[0] or 
                         params.get("p", [None])[0] or 
                         params.get("url", [None])[0] or 
                         params.get("target", [None])[0])
        
        target_item = None
        with CACHE_LOCK:
            if file_id and file_id in DRIVE_CACHE["items"]:
                target_item = dict(DRIVE_CACHE["items"][file_id])
            elif file_path_str:
                clean_lookup = urllib.parse.unquote(file_path_str).strip()
                for it in DRIVE_CACHE["items"].values():
                    if it.get("local_path") == clean_lookup or it.get("id") == clean_lookup or it.get("name") == clean_lookup:
                        target_item = dict(it)
                        file_id = it.get("id")
                        break

        # 2. Check if local file exists on disk
        clean_path_str = (file_path_str or (target_item.get("local_path") if target_item else "") or "").strip()
        clean_path_str = urllib.parse.unquote(clean_path_str).replace("Local: ", "").replace("Path: ", "").strip()
        norm_str = clean_path_str.replace("/", "\\")
        is_android = ("This PC\\OnePlus Nord CE4" in norm_str or 
                      "Internal shared storage" in norm_str or 
                      "Internal Storage" in norm_str or 
                      "SD card" in norm_str or 
                      "SD Card" in norm_str or
                      clean_path_str.startswith("/storage") or
                      clean_path_str.startswith("/sdcard"))

        # Folder ZIP Download
        if parsed.path == "/download-folder" and clean_path_str:
            target_path = Path(clean_path_str).resolve()
            if target_path.exists() and target_path.is_dir():
                try:
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                        for root, _, files in os.walk(target_path):
                            for file in files:
                                file_full_path = Path(root) / file
                                rel_path = file_full_path.relative_to(target_path)
                                zip_file.write(file_full_path, arcname=str(rel_path))
                    zip_data = zip_buffer.getvalue()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(len(zip_data)))
                    self.send_header("Content-Disposition", f'attachment; filename="{target_path.name}.zip"')
                    self.end_headers()
                    self.wfile.write(zip_data)
                    return
                except Exception as e:
                    self.send_error(500, f"Error creating ZIP: {e}")
                    return

        # Case A: Local PC file that exists on disk
        if not is_android and clean_path_str:
            try:
                p_obj = Path(clean_path_str).resolve()
                if p_obj.exists() and p_obj.is_file():
                    mime, _ = mimetypes.guess_type(str(p_obj))
                    mime = mime or "application/octet-stream"
                    file_size = p_obj.stat().st_size
                    
                    range_header = self.headers.get("Range")
                    if range_header and range_header.startswith("bytes="):
                        range_val = range_header[6:].strip()
                        parts = range_val.split("-")
                        start = int(parts[0]) if parts[0] else 0
                        end = int(parts[1]) if len(parts) > 1 and parts[1] else (file_size - 1)
                        if start >= file_size:
                            self.send_error(416, "Requested Range Not Satisfiable")
                            return
                        end = min(end, file_size - 1)
                        chunk_length = (end - start) + 1
                        
                        self.send_response(206)
                        self.send_header("Content-Type", mime)
                        self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                        self.send_header("Content-Length", str(chunk_length))
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Cache-Control", "public, max-age=3600")
                        self.end_headers()
                        
                        with open(p_obj, "rb") as f:
                            f.seek(start)
                            bytes_remaining = chunk_length
                            while bytes_remaining > 0:
                                read_sz = min(bytes_remaining, 65536)
                                buf = f.read(read_sz)
                                if not buf:
                                    break
                                self.wfile.write(buf)
                                bytes_remaining -= len(buf)
                        return
                    else:
                        self.send_response(200)
                        self.send_header("Content-Type", mime)
                        self.send_header("Content-Length", str(file_size))
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Cache-Control", "public, max-age=3600")
                        if parsed.path == "/download":
                            self.send_header("Content-Disposition", f'attachment; filename="{p_obj.name}"')
                        else:
                            self.send_header("Content-Disposition", "inline")
                        self.end_headers()
                        import shutil
                        with open(p_obj, "rb") as f:
                            shutil.copyfileobj(f, self.wfile, length=65536)
                        return
            except Exception:
                pass

        # Case B: Android file with ADB connected
        if is_android and clean_path_str:
            phone_path = resolve_android_path(clean_path_str)
            fname = phone_path.split("/")[-1]
            mime, _ = mimetypes.guess_type(fname)
            mime = mime or "application/octet-stream"
            acquired = ADB_SEMAPHORE.acquire(timeout=1.5)
            if acquired:
                try:
                    proc = subprocess.Popen(
                        ["adb", "exec-out", "cat", phone_path],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                    content, err = proc.communicate(timeout=6.0)
                    if proc.returncode == 0 and content and not content.startswith(b"cat: "):
                        file_size = len(content)
                        range_header = self.headers.get("Range")
                        if range_header and range_header.startswith("bytes="):
                            range_val = range_header[6:].strip()
                            parts = range_val.split("-")
                            start = int(parts[0]) if parts[0] else 0
                            end = int(parts[1]) if len(parts) > 1 and parts[1] else (file_size - 1)
                            end = min(end, file_size - 1)
                            chunk_length = (end - start) + 1
                            self.send_response(206)
                            self.send_header("Content-Type", mime)
                            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                            self.send_header("Content-Length", str(chunk_length))
                            self.send_header("Accept-Ranges", "bytes")
                            self.end_headers()
                            self.wfile.write(content[start:end+1])
                            return
                        else:
                            self.send_response(200)
                            self.send_header("Content-Type", mime)
                            self.send_header("Content-Length", str(file_size))
                            self.send_header("Accept-Ranges", "bytes")
                            if parsed.path == "/download":
                                self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                            else:
                                self.send_header("Content-Disposition", "inline")
                            self.end_headers()
                            self.wfile.write(content)
                            return
                except Exception:
                    pass
                finally:
                    ADB_SEMAPHORE.release()

        # Case C: CLOUD RETRIEVAL (Always retrieve from Notion Cloud)
        notion_id = file_id or (target_item.get("id") if target_item else None)
        item_name = (target_item.get("name") if target_item else None) or (Path(clean_path_str).name if clean_path_str else "Notion Cloud File")
        item_ext = (target_item.get("extension", "") if target_item else Path(item_name).suffix).lower()
        size_bytes = target_item.get("size_bytes", 0) if target_item else 0

        if notion_id:
            notion_cloud_url = f"https://www.notion.so/{notion_id}"
            
            # If downloading, redirect directly to Notion Cloud
            if parsed.path == "/download":
                self.send_response(302)
                self.send_header("Location", notion_cloud_url)
                self.end_headers()
                return

            # Check if Notion page has embedded content / code / text / files
            text_content, bin_content, file_redirect = fetch_notion_page_content(notion_id)
            if file_redirect:
                self.send_response(302)
                self.send_header("Location", file_redirect)
                self.end_headers()
                return

            # Text viewer HTML removed — redirect to Notion or return JSON
            if text_content:
                # Return text as plain response (UI renders it)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(text_content.encode("utf-8"))))
                self.end_headers()
                self.wfile.write(text_content.encode("utf-8"))
                return

            # Direct seamless transition to Notion Database page
            self.send_response(302)
            self.send_header("Location", notion_cloud_url)
            self.end_headers()
            return

        self.send_response(302)
        self.send_header("Location", "/")
        self.end_headers()
        return

def start_server():
    _load_recent_files()
    _build_allowed_roots()
    if not load_disk_cache():
        print("[+] Initializing cache from Notion DB...")
        populate_cache_from_notion()
    
    start_notion_watcher()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), NotionServerHandler)
    server.daemon_threads = True
    print(f"🚀 Notion Drive active on http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Server stopped.")

if __name__ == "__main__":
    start_server()

