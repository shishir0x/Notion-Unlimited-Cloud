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
# HTML & JS FRONTEND TEMPLATE
# ==============================================================================

LOCK_SCREEN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Notion Drive — Protected Access</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {
            --bg-body: #131314;
            --bg-card: #1E1F20;
            --accent-blue: #8AB4F8;
            --accent-blue-hover: #A8C7FA;
            --text-main: #E3E3E3;
            --text-muted: #9AA0A6;
            --border-color: #3C4043;
            --font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: var(--font-family); }
        body {
            background: radial-gradient(circle at top right, #1A2744 0%, #131314 60%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            color: var(--text-main);
        }
        .lock-card {
            background: rgba(30, 31, 32, 0.85);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 24px;
            padding: 40px;
            width: 100%;
            max-width: 420px;
            box-shadow: 0 20px 50px rgba(0,0,0,0.5);
            text-align: center;
            animation: fadeIn 0.4s ease-out;
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(12px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .lock-logo {
            width: 64px;
            height: 64px;
            background: rgba(138, 180, 248, 0.12);
            border: 1px solid rgba(138, 180, 248, 0.3);
            border-radius: 20px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 28px;
            color: var(--accent-blue);
            margin-bottom: 20px;
            box-shadow: 0 0 30px rgba(138, 180, 248, 0.2);
        }
        h1 { font-size: 22px; font-weight: 600; margin-bottom: 8px; }
        p { font-size: 13px; color: var(--text-muted); margin-bottom: 28px; line-height: 1.5; }
        .input-group {
            position: relative;
            margin-bottom: 20px;
            text-align: left;
        }
        .input-group input {
            width: 100%;
            background: rgba(0, 0, 0, 0.25);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 14px 44px 14px 16px;
            font-size: 15px;
            color: #fff;
            outline: none;
            transition: all 0.2s;
        }
        .input-group input:focus {
            border-color: var(--accent-blue);
            box-shadow: 0 0 0 3px rgba(138, 180, 248, 0.2);
        }
        .toggle-pw {
            position: absolute;
            right: 14px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-muted);
            cursor: pointer;
            font-size: 16px;
        }
        .btn-unlock {
            width: 100%;
            background: var(--accent-blue);
            color: #041E49;
            border: none;
            border-radius: 12px;
            padding: 14px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            transition: all 0.2s;
        }
        .btn-unlock:hover {
            background: var(--accent-blue-hover);
            transform: translateY(-1px);
        }
        .err-msg {
            color: #F28B82;
            font-size: 13px;
            margin-top: 14px;
            display: none;
            background: rgba(234, 67, 53, 0.1);
            padding: 10px;
            border-radius: 8px;
            border: 1px solid rgba(234, 67, 53, 0.2);
        }
        .shake {
            animation: shake 0.4s cubic-bezier(.36,.07,.19,.97) both;
        }
        @keyframes shake {
            10%, 90% { transform: translate3d(-1px, 0, 0); }
            20%, 80% { transform: translate3d(2px, 0, 0); }
            30%, 50%, 70% { transform: translate3d(-4px, 0, 0); }
            40%, 60% { transform: translate3d(4px, 0, 0); }
        }
    </style>
</head>
<body>
    <div class="lock-card" id="lockCard">
        <div class="lock-logo">
            <i class="fa-solid fa-lock"></i>
        </div>
        <h1>Notion Cloud Drive</h1>
        <p>This private storage is protected. Enter your password or PIN to unlock your files.</p>
        <form onsubmit="handleUnlock(event)">
            <div class="input-group">
                <input type="password" id="pwInput" placeholder="Enter access password" autofocus autocomplete="current-password" required>
                <i class="fa-solid fa-eye toggle-pw" id="eyeIcon" onclick="togglePasswordVisibility()"></i>
            </div>
            <button type="submit" class="btn-unlock" id="btnUnlock">
                <i class="fa-solid fa-lock-open"></i> Unlock Drive
            </button>
            <div class="err-msg" id="errMsg"></div>
        </form>
    </div>
    <script>
        function togglePasswordVisibility() {
            const input = document.getElementById('pwInput');
            const eye = document.getElementById('eyeIcon');
            if (input.type === 'password') {
                input.type = 'text';
                eye.className = 'fa-solid fa-eye-slash toggle-pw';
            } else {
                input.type = 'password';
                eye.className = 'fa-solid fa-eye toggle-pw';
            }
        }
        async function handleUnlock(e) {
            e.preventDefault();
            const pw = document.getElementById('pwInput').value;
            const btn = document.getElementById('btnUnlock');
            const err = document.getElementById('errMsg');
            const card = document.getElementById('lockCard');
            
            btn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Verifying...';
            btn.disabled = true;
            err.style.display = 'none';

            try {
                const res = await fetch('/api/auth/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({password: pw})
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    btn.innerHTML = '<i class="fa-solid fa-check"></i> Unlocked!';
                    setTimeout(() => { window.location.reload(); }, 300);
                } else {
                    card.classList.add('shake');
                    setTimeout(() => card.classList.remove('shake'), 500);
                    btn.innerHTML = '<i class="fa-solid fa-lock-open"></i> Unlock Drive';
                    btn.disabled = false;
                }
            } catch (errExp) {
                err.innerText = 'Network error. Please try again.';
                err.style.display = 'block';
                btn.innerHTML = '<i class="fa-solid fa-lock-open"></i> Unlock Drive';
                btn.disabled = false;
            }
        }
    </script>
</body>
</html>
"""

DRIVE_GUI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Notion Drive — My Drive</title>
    <meta name="description" content="Notion Cloud File Manager — unified drive for Windows, Android, and Notion Cloud.">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {
            --bg-main: #111213;
            --bg-sidebar: #181A1B;
            --bg-card: #222426;
            --bg-card-hover: #2C2F31;
            --bg-selected: #1A3660;
            --text-main: #E8EAED;
            --text-muted: #9AA0A6;
            --accent-blue: #A8C7FA;
            --accent-primary: #1A73E8;
            --accent-green: #34A853;
            --accent-orange: #FBBC04;
            --accent-red: #EA4335;
            --border-color: #303234;
            --item-radius: 12px;
            --sidebar-width: 240px;
            --bottom-nav-height: 60px;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; -webkit-tap-highlight-color: transparent; }
        body { background: var(--bg-main); color: var(--text-main); display: flex; height: 100vh; overflow: hidden; }

        /* ── Sidebar ──────────────────────────────────────────────────────── */
        .sidebar {
            width: var(--sidebar-width);
            background: var(--bg-sidebar);
            display: flex;
            flex-direction: column;
            border-right: 1px solid var(--border-color);
            padding: 12px 8px;
            flex-shrink: 0;
            overflow-y: auto;
            z-index: 1200;
        }
        .logo {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 10px 18px;
            font-size: 17px;
            font-weight: 700;
            color: var(--text-main);
            letter-spacing: -0.3px;
        }
        .logo i { color: #4285F4; font-size: 22px; }

        .nav-group { margin-bottom: 6px; }
        .nav-group-label {
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            color: var(--text-muted);
            padding: 6px 14px 4px;
        }
        .nav-item {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 9px 14px;
            border-radius: 24px;
            font-size: 13.5px;
            font-weight: 500;
            color: var(--text-main);
            cursor: pointer;
            transition: background 0.12s;
            position: relative;
            user-select: none;
        }
        .nav-item:hover { background: var(--bg-card); }
        .nav-item.active { background: var(--bg-selected); color: var(--accent-blue); }
        .nav-item i { font-size: 15px; width: 18px; text-align: center; opacity: 0.85; }
        .nav-badge {
            margin-left: auto;
            font-size: 10px;
            font-weight: 700;
            padding: 1px 7px;
            border-radius: 10px;
            text-transform: uppercase;
        }
        .nav-badge.idle { background: rgba(255,255,255,0.07); color: var(--text-muted); }
        .nav-badge.running { background: var(--bg-selected); color: var(--accent-blue); animation: pulse 1.5s infinite; }

        .sidebar-bottom {
            margin-top: auto;
            padding-top: 12px;
            border-top: 1px solid var(--border-color);
        }
        .storage-info {
            padding: 14px;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--item-radius);
            margin: 6px;
        }
        .storage-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; font-size: 12px; }
        .storage-label { color: var(--text-muted); font-weight: 500; }
        .storage-badge { background: rgba(168,199,250,0.12); color: var(--accent-blue); padding: 2px 7px; border-radius: 10px; font-size: 10px; font-weight: 600; }
        .storage-bar { background: rgba(255,255,255,0.06); height: 5px; border-radius: 3px; overflow: hidden; margin-bottom: 8px; }
        .storage-fill { height: 100%; width: 100%; background: linear-gradient(90deg, #1A73E8, #34A853); border-radius: 3px; transition: width 0.4s; }
        .storage-text { font-size: 11.5px; color: var(--text-main); font-weight: 500; }
        .storage-sub { font-size: 10.5px; color: var(--text-muted); margin-top: 2px; }

        /* ── Main Container ───────────────────────────────────────────────── */
        .main { flex: 1; display: flex; flex-direction: column; overflow: hidden; position: relative; }

        /* ── Topbar ───────────────────────────────────────────────────────── */
        .topbar {
            height: 60px;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            align-items: center;
            padding: 0 16px;
            gap: 12px;
            flex-shrink: 0;
            background: var(--bg-sidebar);
            z-index: 10;
        }
        .search-wrap {
            flex: 1;
            max-width: 640px;
            position: relative;
        }
        .search-wrap i { position: absolute; left: 14px; top: 50%; transform: translateY(-50%); color: var(--text-muted); font-size: 14px; pointer-events: none; }
        .search-input {
            width: 100%;
            background: var(--bg-card);
            border: 1px solid transparent;
            border-radius: 28px;
            padding: 9px 18px 9px 40px;
            font-size: 13.5px;
            color: var(--text-main);
            outline: none;
            transition: all 0.2s;
            font-family: inherit;
        }
        .search-input:focus { background: #1E2022; border-color: var(--accent-blue); box-shadow: 0 0 0 3px rgba(168,199,250,0.12); }
        .search-input::placeholder { color: var(--text-muted); }

        .topbar-actions { display: flex; align-items: center; gap: 8px; }
        .btn-primary {
            background: var(--accent-primary);
            color: #fff;
            border: none;
            border-radius: 20px;
            padding: 8px 16px;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 7px;
            transition: opacity 0.15s;
            font-family: inherit;
            white-space: nowrap;
        }
        .btn-primary:hover { opacity: 0.88; }
        .btn-icon {
            background: transparent;
            border: none;
            color: var(--text-muted);
            cursor: pointer;
            padding: 7px 10px;
            border-radius: 20px;
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 13px;
            font-family: inherit;
            transition: all 0.12s;
        }
        .btn-icon:hover { background: var(--bg-card); color: var(--text-main); }
        .btn-danger { background: rgba(234,67,53,0.1); color: #F28B82; border: 1px solid rgba(234,67,53,0.2); border-radius: 20px; padding: 7px 12px; }

        /* ── Search Category Filters ─────────────────────────────────────── */
        .search-filters {
            display: none;
            gap: 8px;
            padding: 8px 16px;
            background: var(--bg-sidebar);
            border-bottom: 1px solid var(--border-color);
            overflow-x: auto;
            flex-shrink: 0;
        }
        .search-filters::-webkit-scrollbar { display: none; }
        .filter-chip {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            color: var(--text-muted);
            padding: 5px 12px;
            border-radius: 16px;
            font-size: 12px;
            cursor: pointer;
            white-space: nowrap;
            display: flex;
            align-items: center;
            gap: 6px;
            transition: all 0.12s;
        }
        .filter-chip:hover { color: var(--text-main); border-color: #5F6368; }
        .filter-chip.active { background: var(--bg-selected); color: var(--accent-blue); border-color: var(--accent-blue); }

        /* ── View panel ───────────────────────────────────────────────────── */
        .view-panel { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
        .view-panel-inner { flex: 1; display: none; flex-direction: column; overflow: hidden; }
        .view-panel-inner.active { display: flex; }

        /* ── Content Header (breadcrumbs + controls) ──────────────────────── */
        .content-header {
            padding: 12px 16px 8px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            flex-wrap: wrap;
            flex-shrink: 0;
        }
        .breadcrumbs { display: flex; align-items: center; gap: 6px; font-size: 14.5px; font-weight: 600; flex: 1; flex-wrap: wrap; }
        .bc-item { color: var(--text-muted); cursor: pointer; display: flex; align-items: center; gap: 5px; border-radius: 6px; padding: 2px 4px; transition: color 0.12s; }
        .bc-item:hover { color: var(--text-main); }
        .bc-item.active { color: var(--text-main); cursor: default; }
        .bc-sep { color: var(--text-muted); font-size: 10px; }

        .toolbar { display: flex; align-items: center; gap: 10px; }
        .sort-pill {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 4px 10px;
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 12px;
            color: var(--text-main);
        }
        .sort-pill select {
            background: transparent;
            border: none;
            color: var(--text-main);
            font-size: 12px;
            outline: none;
            cursor: pointer;
            font-family: inherit;
        }
        .sort-pill select option { background: #222426; }
        .view-switch { display: flex; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 20px; overflow: hidden; }
        .view-btn { background: transparent; border: none; color: var(--text-muted); padding: 5px 10px; cursor: pointer; font-size: 13px; display: flex; align-items: center; transition: all 0.12s; }
        .view-btn.active { background: var(--bg-selected); color: var(--accent-blue); }

        /* ── Scroll area ──────────────────────────────────────────────────── */
        .scroll-area { flex: 1; overflow-y: auto; padding: 10px 16px 20px; }
        .scroll-area::-webkit-scrollbar { width: 6px; }
        .scroll-area::-webkit-scrollbar-track { background: transparent; }
        .scroll-area::-webkit-scrollbar-thumb { background: #3C4043; border-radius: 3px; }

        /* ── Dashboard (My Drive root) ────────────────────────────────────── */
        .section-title {
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            color: var(--text-muted);
            margin: 18px 0 10px;
        }

        /* Device cards */
        .device-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
        .device-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 14px;
            padding: 16px;
            cursor: pointer;
            transition: all 0.15s;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        .device-card:hover { background: var(--bg-card-hover); border-color: #5F6368; transform: translateY(-1px); box-shadow: 0 4px 16px rgba(0,0,0,0.3); }
        .device-icon-wrap { width: 44px; height: 44px; background: rgba(168,199,250,0.1); border-radius: 12px; display: flex; align-items: center; justify-content: center; }
        .device-icon-wrap i { font-size: 20px; color: var(--accent-blue); }
        .device-name { font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .device-meta { font-size: 11.5px; color: var(--text-muted); display: flex; justify-content: space-between; }

        /* Quick Access / Recent items */
        .recent-scroller { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 4px; }
        .recent-scroller::-webkit-scrollbar { height: 4px; }
        .recent-scroller::-webkit-scrollbar-thumb { background: #3C4043; border-radius: 2px; }
        .recent-chip {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 10px 14px;
            display: flex;
            align-items: center;
            gap: 10px;
            cursor: pointer;
            white-space: nowrap;
            transition: all 0.12s;
            min-width: 180px;
            max-width: 230px;
        }
        .recent-chip:hover { background: var(--bg-card-hover); border-color: #5F6368; }
        .recent-chip i { font-size: 18px; color: var(--accent-blue); flex-shrink: 0; }
        .recent-chip-info { overflow: hidden; flex: 1; }
        .recent-chip-name { font-size: 13px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; }
        .recent-chip-meta { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

        /* ── Folder/File grids (browse view) ──────────────────────────────── */
        .grid-view { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }

        .folder-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--item-radius);
            padding: 12px 14px;
            display: flex;
            align-items: center;
            gap: 11px;
            cursor: pointer;
            transition: all 0.12s;
            user-select: none;
        }
        .folder-card:hover { background: var(--bg-card-hover); border-color: #5F6368; transform: translateY(-1px); }
        .folder-card i { font-size: 19px; color: var(--accent-blue); flex-shrink: 0; }
        .folder-card-info { overflow: hidden; flex: 1; }
        .folder-card-name { font-size: 13.5px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .folder-card-count { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

        .file-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--item-radius);
            padding: 10px;
            cursor: pointer;
            transition: all 0.12s;
            display: flex;
            flex-direction: column;
            gap: 8px;
            position: relative;
        }
        .file-card:hover { background: var(--bg-card-hover); border-color: #5F6368; transform: translateY(-1px); }
        .thumb {
            height: 108px;
            background: #1A1C1E;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 32px;
            color: var(--text-muted);
            overflow: hidden;
            position: relative;
        }
        .thumb img { width: 100%; height: 100%; object-fit: cover; transition: opacity 0.25s; }
        .thumb .skeleton { position: absolute; inset: 0; background: linear-gradient(90deg,#222426 25%,#2c2f31 50%,#222426 75%); background-size: 200%; animation: shimmer 1.4s infinite; border-radius: 8px; }
        @keyframes shimmer { 0%{background-position:200% 0} 100%{background-position:-200% 0} }
        
        /* Rich Thumbnails */
        .thumb-pdf { background: radial-gradient(circle, #2A1717 0%, #161010 100%); border: 1px solid rgba(234,67,53,0.25); }
        .thumb-pdf .pdf-icon { font-size: 38px; color: #EA4335; }
        .thumb-pdf .pdf-tag { position: absolute; bottom: 8px; left: 8px; background: rgba(234,67,53,0.25); color: #F28B82; font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 4px; }

        .thumb-video { background: radial-gradient(circle, #1A2234 0%, #10141D 100%); border: 1px solid rgba(138,180,248,0.25); }
        .thumb-video .play-btn { width: 42px; height: 42px; border-radius: 50%; background: rgba(138,180,248,0.25); display: flex; align-items: center; justify-content: center; color: #A8C7FA; font-size: 17px; transition: all 0.15s; }
        .file-card:hover .thumb-video .play-btn { transform: scale(1.15); background: var(--accent-blue); color: #041E49; }

        .thumb-code { background: radial-gradient(circle, #1A271E 0%, #0F1612 100%); border: 1px solid rgba(52,168,83,0.25); font-family: monospace; }
        .thumb-code .code-badge { font-size: 18px; font-weight: 700; color: #81C995; letter-spacing: 0.5px; }

        .thumb-audio { background: radial-gradient(circle, #2B2114 0%, #15110B 100%); border: 1px solid rgba(251,188,4,0.25); }
        .thumb-audio i { font-size: 36px; color: #FDD663; }

        .file-card-footer { display: flex; align-items: center; gap: 8px; }
        .file-card-footer i { font-size: 14px; color: var(--accent-blue); flex-shrink: 0; }
        .file-card-name { font-size: 12.5px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; }
        .file-card-meta { font-size: 11px; color: var(--text-muted); display: flex; justify-content: space-between; align-items: center; }
        .card-more-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 4px 6px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
        }
        .card-more-btn:hover { color: var(--text-main); background: rgba(255,255,255,0.1); }

        /* ── List View ────────────────────────────────────────────────────── */
        .drive-table { width: 100%; border-collapse: collapse; font-size: 13px; }
        .drive-table th { padding: 9px 14px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); font-weight: 500; font-size: 11.5px; cursor: pointer; user-select: none; text-align: left; }
        .drive-table th:hover { color: var(--text-main); }
        .drive-table td { padding: 11px 14px; border-bottom: 1px solid rgba(255,255,255,0.04); }
        .drive-table tr:hover td { background: var(--bg-card-hover); }
        .drive-table tr { cursor: pointer; }
        .tname { display: flex; align-items: center; gap: 11px; font-weight: 500; }
        .tname i { font-size: 15px; color: var(--accent-blue); width: 16px; text-align: center; }

        /* Mobile list item */
        .mobile-list-item {
            display: none;
            padding: 12px 14px;
            border-bottom: 1px solid var(--border-color);
            align-items: center;
            gap: 12px;
            cursor: pointer;
        }
        .mobile-list-item:hover { background: var(--bg-card-hover); }
        .mli-icon { font-size: 20px; color: var(--accent-blue); width: 24px; text-align: center; }
        .mli-content { flex: 1; min-width: 0; }
        .mli-name { font-size: 13.5px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .mli-meta { font-size: 11px; color: var(--text-muted); margin-top: 3px; }

        /* ── Load-more sentinel ───────────────────────────────────────────── */
        .load-sentinel { height: 60px; display: flex; align-items: center; justify-content: center; }
        .load-spinner { font-size: 20px; color: var(--text-muted); animation: spin 0.8s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }

        /* ── File Preview Modal ───────────────────────────────────────────── */
        .modal-overlay {
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.85);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 3000;
            backdrop-filter: blur(8px);
        }
        .modal-overlay.open { display: flex; }
        .modal-box {
            background: var(--bg-sidebar);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            width: 94%;
            max-width: 950px;
            max-height: 90vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            box-shadow: 0 12px 40px rgba(0,0,0,0.7);
            animation: fadeUp 0.22s ease-out;
            position: relative;
        }
        @keyframes fadeUp { from { opacity:0; transform:translateY(14px); } to { opacity:1; transform:translateY(0); } }
        .modal-hdr { padding: 13px 18px; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; gap: 12px; }
        .modal-title { font-weight: 600; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
        .modal-actions { display: flex; gap: 8px; flex-shrink: 0; align-items: center; }
        .modal-counter { font-size: 12px; color: var(--text-muted); padding: 2px 8px; border-radius: 10px; background: rgba(255,255,255,0.06); font-weight: 500; }
        .modal-body { flex: 1; overflow: auto; display: flex; align-items: center; justify-content: center; background: #0D0E0F; min-height: 300px; }

        .modal-nav-btn {
            position: absolute;
            top: 50%;
            transform: translateY(-50%);
            width: 44px;
            height: 44px;
            border-radius: 50%;
            background: rgba(30, 31, 32, 0.88);
            border: 1px solid var(--border-color);
            color: #E8EAED;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            z-index: 100;
            backdrop-filter: blur(6px);
            transition: all 0.15s;
            box-shadow: 0 4px 16px rgba(0,0,0,0.6);
        }
        .modal-nav-btn:hover {
            background: rgba(50, 54, 58, 0.98);
            color: var(--accent-blue);
            transform: translateY(-50%) scale(1.1);
        }
        .modal-prev-btn { left: 16px; }
        .modal-next-btn { right: 16px; }

        .action-btn {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            color: var(--text-main);
            padding: 5px 11px;
            border-radius: 14px;
            cursor: pointer;
            font-size: 12px;
            display: flex;
            align-items: center;
            gap: 5px;
            text-decoration: none;
            font-family: inherit;
            transition: all 0.12s;
        }
        .action-btn:hover { background: var(--bg-card-hover); color: var(--accent-blue); }

        /* ── Action Sheet / Context Bottom Sheet ─────────────────────────── */
        .action-sheet-overlay {
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.6);
            display: none;
            align-items: flex-end;
            justify-content: center;
            z-index: 4000;
            backdrop-filter: blur(4px);
        }
        .action-sheet-overlay.open { display: flex; }
        .action-sheet {
            background: var(--bg-sidebar);
            border: 1px solid var(--border-color);
            border-radius: 20px 20px 0 0;
            width: 100%;
            max-width: 500px;
            padding: 16px;
            box-shadow: 0 -8px 32px rgba(0,0,0,0.6);
            animation: sheetUp 0.25s cubic-bezier(0.1,0.9,0.2,1);
        }
        @keyframes sheetUp { from { transform: translateY(100%); } to { transform: translateY(0); } }
        .as-header { display: flex; align-items: center; gap: 12px; padding-bottom: 14px; border-bottom: 1px solid var(--border-color); margin-bottom: 10px; }
        .as-icon { font-size: 24px; color: var(--accent-blue); width: 36px; text-align: center; }
        .as-info { flex: 1; min-width: 0; }
        .as-title { font-size: 14px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .as-subtitle { font-size: 11.5px; color: var(--text-muted); margin-top: 2px; }
        .as-close { background: transparent; border: none; color: var(--text-muted); font-size: 16px; cursor: pointer; padding: 6px; }
        .as-body { display: flex; flex-direction: column; gap: 4px; }
        .as-item {
            background: transparent;
            border: none;
            color: var(--text-main);
            padding: 12px 14px;
            border-radius: 12px;
            font-size: 13.5px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 14px;
            cursor: pointer;
            text-decoration: none;
            transition: background 0.1s;
            text-align: left;
        }
        .as-item:hover { background: var(--bg-card-hover); }
        .as-item i { width: 18px; text-align: center; font-size: 15px; color: var(--accent-blue); }
        .as-item.text-danger { color: #F28B82; }
        .as-item.text-danger i { color: #F28B82; }

        /* ── Mobile Bottom Navigation ─────────────────────────────────────── */
        .mobile-bottom-nav {
            display: none;
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            height: var(--bottom-nav-height);
            background: var(--bg-sidebar);
            border-top: 1px solid var(--border-color);
            align-items: center;
            justify-content: space-around;
            z-index: 1100;
            padding: 0 4px;
        }
        .mbn-item {
            background: transparent;
            border: none;
            color: var(--text-muted);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 4px;
            font-size: 10px;
            font-weight: 500;
            cursor: pointer;
            flex: 1;
            padding: 6px 0;
            transition: color 0.12s;
        }
        .mbn-item i { font-size: 18px; }
        .mbn-item.active { color: var(--accent-blue); }
        .mbn-new-circle {
            width: 38px;
            height: 38px;
            background: var(--accent-primary);
            color: #fff;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
            box-shadow: 0 2px 8px rgba(26,115,232,0.4);
            margin-top: -6px;
        }

        /* ── Drawer Backdrop ──────────────────────────────────────────────── */
        .drawer-backdrop {
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.6);
            display: none;
            z-index: 1150;
            backdrop-filter: blur(2px);
        }
        .drawer-backdrop.open { display: block; }

        /* ── Empty state ──────────────────────────────────────────────────── */
        .empty-state { text-align: center; padding: 60px 20px; color: var(--text-muted); }
        .empty-state i { font-size: 40px; margin-bottom: 14px; display: block; opacity: 0.5; }
        .empty-state p { font-size: 14px; }

        /* ── Sync Center ──────────────────────────────────────────────────── */
        #viewSync { padding: 16px; overflow-y: auto; }
        .sync-hero {
            background: linear-gradient(135deg, #1E2022 0%, #222426 100%);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 20px;
            margin-bottom: 16px;
            box-shadow: 0 4px 24px rgba(0,0,0,0.3);
        }
        .sync-hdr-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; flex-wrap: wrap; gap: 10px; }
        .sync-pulse { width: 11px; height: 11px; border-radius: 50%; background: var(--accent-green); box-shadow: 0 0 10px var(--accent-green); flex-shrink: 0; }
        .sync-pulse.running { background: var(--accent-blue); box-shadow: 0 0 12px var(--accent-blue); animation: pulse 1s infinite; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
        .sync-title-area { display: flex; align-items: center; gap: 10px; }
        .sync-controls { display: flex; gap: 8px; flex-wrap: wrap; }
        .btn-sync-action {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            color: var(--text-main);
            padding: 8px 14px;
            border-radius: 20px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 7px;
            transition: all 0.15s;
            font-family: inherit;
        }
        .btn-sync-action:hover { background: var(--bg-card-hover); border-color: var(--accent-blue); color: var(--accent-blue); }
        .btn-sync-action.primary { background: var(--accent-primary); border-color: var(--accent-primary); color: #fff; }
        .btn-sync-action.primary:hover { opacity: 0.88; }
        .btn-sync-action.danger { background: rgba(234,67,53,0.12); border-color: rgba(234,67,53,0.3); color: #F28B82; }
        .btn-sync-action.danger:hover { background: rgba(234,67,53,0.25); }

        .progress-wrap { margin-bottom: 16px; }
        .progress-labels { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 7px; font-weight: 500; }
        .progress-track { background: rgba(255,255,255,0.07); height: 8px; border-radius: 4px; overflow: hidden; }
        .progress-bar { height: 100%; width: 0%; background: linear-gradient(90deg, #1A73E8, #34A853); border-radius: 4px; transition: width 0.35s cubic-bezier(0.4,0,0.2,1); }

        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin-bottom: 16px; }
        .stat-box { background: #1A1C1E; border: 1px solid var(--border-color); border-radius: 12px; padding: 12px 14px; }
        .stat-lbl { font-size: 10.5px; color: var(--text-muted); margin-bottom: 4px; }
        .stat-val { font-size: 17px; font-weight: 700; }

        .active-file-box {
            background: rgba(0,74,119,0.2);
            border: 1px solid rgba(168,199,250,0.25);
            border-radius: 11px;
            padding: 12px 16px;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .active-file-box i { font-size: 20px; color: var(--accent-blue); flex-shrink: 0; }
        .afb-details { flex: 1; min-width: 0; }
        .afb-name { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .afb-path { font-size: 11px; color: var(--text-muted); word-break: break-all; margin-top: 2px; }
        .afb-size { font-size: 12.5px; font-weight: 600; color: var(--accent-blue); flex-shrink: 0; }

        .sync-tabs { display: flex; gap: 6px; border-bottom: 1px solid var(--border-color); margin: 16px 0 12px; }
        .sync-tab-btn { background: transparent; border: none; color: var(--text-muted); padding: 8px 12px; font-size: 12.5px; font-weight: 500; cursor: pointer; border-bottom: 2px solid transparent; display: flex; align-items: center; gap: 7px; font-family: inherit; transition: all 0.12s; }
        .sync-tab-btn.active { color: var(--accent-blue); border-bottom-color: var(--accent-blue); }
        .tab-count { background: rgba(255,255,255,0.09); padding: 1px 6px; border-radius: 9px; font-size: 10px; }

        .sync-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
        .sync-table th { padding: 9px 12px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); font-size: 11px; text-align: left; }
        .sync-table td { padding: 11px 12px; border-bottom: 1px solid rgba(255,255,255,0.04); }

        .pill { display: inline-flex; align-items: center; gap: 5px; padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 600; text-transform: uppercase; }
        .pill.queued { background: rgba(255,255,255,0.07); color: var(--text-muted); }
        .pill.uploading { background: var(--bg-selected); color: var(--accent-blue); animation: pulse 1.2s infinite; }
        .pill.synced { background: rgba(52,168,83,0.15); color: #81C995; }
        .pill.failed { background: rgba(234,67,53,0.15); color: #F28B82; }
        .tag { padding: 1px 5px; border-radius: 4px; font-size: 9.5px; font-weight: 700; text-transform: uppercase; margin-right: 5px; }
        .tag.NEW { background: rgba(52,168,83,0.18); color: #81C995; }
        .tag.MODIFIED { background: rgba(251,188,4,0.18); color: #FDD663; }

        .console-box { background: #0E0F10; border: 1px solid var(--border-color); border-radius: 11px; padding: 13px; font-family: 'Consolas', monospace; font-size: 11.5px; color: #A8C7FA; height: 300px; overflow-y: auto; line-height: 1.7; }
        .log-line { margin-bottom: 1px; }

        /* ── Responsive Media Queries ────────────────────────────────────── */
        @media (max-width: 1024px) {
            .sidebar { width: 210px; }
            .device-grid { grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); }
            .grid-view { grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); }
        }
        
        @media (max-width: 768px) {
            .sidebar {
                position: fixed; left: -270px; top: 0; bottom: 0; z-index: 1200;
                transition: left 0.28s cubic-bezier(0.1,0.9,0.2,1);
            }
            .sidebar.open { left: 0; box-shadow: 4px 0 24px rgba(0,0,0,0.6); }
            .main { margin-left: 0; }
            .topbar { padding: 0 12px; }
            .search-wrap { max-width: 100%; }
            .device-grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px; }
            .grid-view { grid-template-columns: repeat(2, 1fr); gap: 8px; }
            .content-header { padding: 10px 12px 6px; }
            .scroll-area { padding: 8px 12px calc(var(--bottom-nav-height) + 16px); }
            .mobile-bottom-nav { display: flex; }
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
            .sync-controls { flex-direction: column; width: 100%; }
            .btn-sync-action { width: 100%; justify-content: center; }
            .drive-table { display: none; }
            .mobile-list-item { display: flex; }
            .search-filters { display: flex; }
        }
        
        @media (max-width: 480px) {
            .grid-view { grid-template-columns: repeat(2, 1fr); gap: 6px; }
            .file-card { padding: 7px; border-radius: 10px; }
            .thumb { height: 86px; }
            .folder-card { padding: 9px 11px; }
            .topbar-actions { gap: 4px; }
            .btn-primary span { display: none; }
            .btn-primary { padding: 8px 11px; }
            .btn-icon { padding: 6px 8px; }
        }

        /* Mobile menu toggle button */
        .mobile-menu-btn {
            display: none;
            background: transparent;
            border: none;
            color: var(--text-main);
            font-size: 18px;
            cursor: pointer;
            padding: 8px 10px 8px 0;
        }
        @media (max-width: 768px) {
            .mobile-menu-btn { display: flex; align-items: center; }
        }

        /* ── Upload & Drop Overlay ────────────────────────────────────────── */
        #dropOverlay {
            position: fixed; inset: 0;
            background: rgba(13,14,15,0.9);
            border: 3px dashed var(--accent-blue);
            border-radius: 16px;
            display: none; flex-direction: column; align-items: center; justify-content: center; gap: 14px;
            z-index: 9999; pointer-events: none; backdrop-filter: blur(6px);
        }
        #dropOverlay.on { display: flex; }
        #dropOverlay i { font-size: 52px; color: var(--accent-blue); animation: bounce 0.9s infinite alternate; }
        @keyframes bounce { from{transform:translateY(0)} to{transform:translateY(-12px)} }
        #dropOverlay h2 { font-size: 20px; color: var(--text-main); }
        #dropOverlay p { font-size: 13px; color: var(--text-muted); }

        #uploadToast {
            position: fixed; bottom: calc(var(--bottom-nav-height) + 16px); right: 16px;
            background: var(--bg-card); border: 1px solid var(--border-color);
            border-radius: 14px; padding: 14px 18px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.6);
            width: 320px; max-width: calc(100vw - 32px); display: none; flex-direction: column; gap: 9px; z-index: 5000;
        }
        .ut-header { display: flex; justify-content: space-between; font-size: 13px; font-weight: 600; }
        .ut-file { font-size: 11.5px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .ut-bar-bg { background: #3C4043; height: 5px; border-radius: 3px; overflow: hidden; }
        .ut-bar { background: var(--accent-green); height: 100%; width: 0%; border-radius: 3px; transition: width 0.2s; }

        /* ── New menu dropdown ────────────────────────────────────────────── */
        .new-btn-wrap { position: relative; margin: 0 4px 14px; }
        .btn-new {
            background: #333537; color: var(--text-main); border: 1px solid var(--border-color);
            border-radius: 16px; padding: 11px 18px; font-size: 13.5px; font-weight: 500;
            display: flex; align-items: center; gap: 11px; cursor: pointer; width: 100%;
            transition: all 0.15s; font-family: inherit; box-shadow: 0 1px 3px rgba(0,0,0,0.25);
        }
        .btn-new:hover { background: #3E4042; }
        .btn-new .plus { color: var(--accent-blue); font-size: 16px; }
        .dropdown {
            position: absolute; top: 48px; left: 0;
            background: #2A2C2E; border: 1px solid var(--border-color);
            border-radius: 12px; box-shadow: 0 8px 28px rgba(0,0,0,0.5);
            width: 210px; padding: 6px 0; display: none; flex-direction: column; z-index: 1000;
        }
        .dropdown.open { display: flex; }
        .dd-item { padding: 10px 14px; display: flex; align-items: center; gap: 11px; font-size: 13px; cursor: pointer; transition: background 0.1s; }
        .dd-item:hover { background: #333537; color: var(--accent-blue); }
        .dd-item i { width: 16px; text-align: center; font-size: 14px; }
        .dd-divider { height: 1px; background: var(--border-color); margin: 5px 0; }

        .sse-dot { width: 7px; height: 7px; border-radius: 50%; background: #3C4043; display: inline-block; margin-right: 4px; transition: background 0.3s; }
        .sse-dot.connected { background: var(--accent-green); box-shadow: 0 0 6px var(--accent-green); }
    </style>
</head>
<body>

<!-- Mobile Drawer Backdrop -->
<div class="drawer-backdrop" id="drawerBackdrop" onclick="closeMobileMenu()"></div>

<!-- Left Sidebar (Desktop + Mobile Drawer) -->
<nav class="sidebar" id="sidebar">
    <div class="logo"><i class="fa-brands fa-google-drive"></i><span>Notion Drive</span></div>

    <!-- New button -->
    <div class="new-btn-wrap">
        <button class="btn-new" id="btnNew" onclick="toggleNewMenu(event)">
            <i class="fa-solid fa-plus plus"></i> New
        </button>
        <div class="dropdown" id="newDropdown">
            <div class="dd-item" onclick="triggerFileInput()"><i class="fa-solid fa-file-arrow-up"></i> Upload files</div>
            <div class="dd-item" onclick="triggerFolderInput()"><i class="fa-solid fa-folder-arrow-up"></i> Upload folder</div>
            <div class="dd-divider"></div>
            <div class="dd-item" onclick="startSync('all')"><i class="fa-solid fa-arrows-rotate"></i> Sync all devices</div>
            <div class="dd-item" onclick="startSync('c')"><i class="fa-solid fa-hard-drive"></i> Sync Local Disk (C:)</div>
            <div class="dd-item" onclick="startSync('phone')"><i class="fa-solid fa-mobile-screen-button"></i> Sync Phone</div>
        </div>
    </div>

    <div class="nav-group">
        <div class="nav-item active" id="navDrive" onclick="switchTab('drive'); closeMobileMenu();"><i class="fa-solid fa-folder"></i> My Drive</div>
        <div class="nav-item" id="navRecent" onclick="switchTab('recent'); closeMobileMenu();"><i class="fa-solid fa-clock-rotate-left"></i> Recent</div>
        <div class="nav-item" id="navStarred" onclick="switchTab('starred'); closeMobileMenu();"><i class="fa-solid fa-star"></i> Starred</div>
    </div>

    <div class="nav-group" style="margin-top: 6px;">
        <div class="nav-group-label">Tools</div>
        <div class="nav-item" id="navSync" onclick="switchTab('sync'); closeMobileMenu();">
            <i class="fa-solid fa-arrows-rotate" id="syncNavIcon"></i> Sync Activity
            <span class="nav-badge idle" id="syncNavBadge">Idle</span>
        </div>
        <div class="nav-item" onclick="openSettingsModal(); closeMobileMenu();"><i class="fa-solid fa-sliders"></i> Optimization Settings</div>
        <div class="nav-item" onclick="window.open('https://app.notion.com/p/3bd3d81b2f368055902aeee41736ae89','_blank')"><i class="fa-solid fa-arrow-up-right-from-square"></i> Open in Notion</div>
    </div>

    <div class="sidebar-bottom">
        <div class="storage-info">
            <div class="storage-header">
                <span class="storage-label">Storage</span>
                <span class="storage-badge"><i class="fa-solid fa-infinity"></i> Unlimited</span>
            </div>
            <div class="storage-bar"><div class="storage-fill" id="storageFill"></div></div>
            <div class="storage-text" id="storageDetail">Loading...</div>
            <div class="storage-sub">Student / Plus Unlimited Plan · <span class="sse-dot" id="sseDot"></span><span id="sseLabel">Connecting...</span></div>
        </div>
    </div>
</nav>

<!-- Main Area -->
<div class="main">
    <!-- Topbar -->
    <div class="topbar">
        <button class="mobile-menu-btn" onclick="toggleMobileMenu()" aria-label="Toggle Navigation"><i class="fa-solid fa-bars"></i></button>
        <div class="search-wrap">
            <i class="fa-solid fa-magnifying-glass"></i>
            <input type="text" class="search-input" id="searchInput" placeholder="Search in My Drive..." oninput="handleSearch()">
        </div>
        <div class="topbar-actions">
            <button class="btn-icon" onclick="openSettingsModal()" title="Optimization Settings">
                <i class="fa-solid fa-sliders"></i>
            </button>
            <button class="btn-primary" onclick="refreshDrive()">
                <i class="fa-solid fa-arrows-rotate" id="refreshIcon"></i> <span>Sync Notion</span>
            </button>
            <button class="btn-icon btn-danger" id="btnLock" onclick="lockDrive()" style="display:none;">
                <i class="fa-solid fa-lock"></i>
            </button>
        </div>
    </div>

    <!-- Search category filters (Visible on search / mobile) -->
    <div class="search-filters" id="searchFilters">
        <button class="filter-chip active" onclick="setSearchCategory('all', this)"><i class="fa-solid fa-asterisk"></i> All</button>
        <button class="filter-chip" onclick="setSearchCategory('image', this)"><i class="fa-solid fa-image"></i> Images</button>
        <button class="filter-chip" onclick="setSearchCategory('document', this)"><i class="fa-solid fa-file-lines"></i> Docs</button>
        <button class="filter-chip" onclick="setSearchCategory('video', this)"><i class="fa-solid fa-video"></i> Video</button>
        <button class="filter-chip" onclick="setSearchCategory('audio', this)"><i class="fa-solid fa-music"></i> Audio</button>
        <button class="filter-chip" onclick="setSearchCategory('code', this)"><i class="fa-solid fa-code"></i> Code</button>
        <button class="filter-chip" onclick="setSearchCategory('folder', this)"><i class="fa-solid fa-folder"></i> Folders</button>
    </div>

    <!-- View panels -->
    <div class="view-panel">

        <!-- ═══ 1. MY DRIVE VIEW ═══════════════════════════════════════════ -->
        <div class="view-panel-inner active" id="viewDrive">
            <!-- Content header -->
            <div class="content-header">
                <div class="breadcrumbs" id="breadcrumbs">
                    <span class="bc-item active" onclick="goRoot()"><i class="fa-solid fa-hard-drive"></i> My Drive</span>
                </div>
                <div class="toolbar" id="toolbar" style="display:none;">
                    <div class="sort-pill">
                        <span style="color:var(--text-muted);font-size:11px;">Sort:</span>
                        <select id="sortSelect" onchange="changeSort(this.value)">
                            <option value="name">Name</option>
                            <option value="mtime">Modified</option>
                            <option value="size_bytes">Size</option>
                        </select>
                        <button class="btn-icon" style="padding:2px 4px;" onclick="toggleSortDir()" title="Reverse sort">
                            <i class="fa-solid fa-arrow-down-short-wide" id="sortDirIcon"></i>
                        </button>
                    </div>
                    <div class="view-switch">
                        <button class="view-btn active" id="btnGrid" onclick="setView('grid')" title="Grid"><i class="fa-solid fa-table-cells-large"></i></button>
                        <button class="view-btn" id="btnList" onclick="setView('list')" title="List"><i class="fa-solid fa-list-ul"></i></button>
                    </div>
                </div>
            </div>

            <!-- Scroll area -->
            <div class="scroll-area" id="driveScroll">
                <!-- Dashboard (shown at root, hidden in subfolders) -->
                <div id="dashboardView">
                    <div class="section-title">Storage Devices & Drives</div>
                    <div class="device-grid" id="deviceGrid"></div>

                    <div class="section-title" id="quickAccessTitle" style="display:none;">Quick Access Folders</div>
                    <div class="recent-scroller" id="quickAccessRow" style="display:none;"></div>
                </div>

                <!-- Browser (shown in subfolders) -->
                <div id="browserView" style="display:none;">
                    <div id="foldersSection">
                        <div class="section-title" style="margin-top:6px;">Folders</div>
                        <div class="grid-view" id="foldersGrid"></div>
                    </div>
                    <div id="filesSection">
                        <div class="section-title">Files</div>
                        <div class="grid-view" id="filesGrid"></div>
                        <div id="listView" style="display:none;">
                            <table class="drive-table">
                                <thead><tr>
                                    <th onclick="tableSort('name')">Name <i class="fa-solid fa-sort" id="th-name"></i></th>
                                    <th onclick="tableSort('type')">Type</th>
                                    <th onclick="tableSort('mtime')">Modified</th>
                                    <th onclick="tableSort('size_bytes')">Size</th>
                                    <th>Actions</th>
                                </tr></thead>
                                <tbody id="tableBody"></tbody>
                            </table>
                            <div id="mobileListContainer"></div>
                        </div>
                    </div>
                    <div class="empty-state" id="emptyMsg" style="display:none;">
                        <i class="fa-regular fa-folder-open"></i>
                        <p>This folder is empty</p>
                    </div>
                    <!-- Load-more sentinel for virtual scroll -->
                    <div class="load-sentinel" id="loadSentinel" style="display:none;">
                        <i class="fa-solid fa-circle-notch load-spinner"></i>
                    </div>
                </div>
            </div>
        </div>

        <!-- ═══ 2. RECENT VIEW ════════════════════════════════════════════ -->
        <div class="view-panel-inner" id="viewRecent">
            <div class="content-header">
                <div class="breadcrumbs"><span class="bc-item active"><i class="fa-solid fa-clock-rotate-left"></i> Recent</span></div>
            </div>
            <div class="scroll-area">
                <div class="section-title">Recently synced files</div>
                <div class="grid-view" id="recentGrid"></div>
                <div class="empty-state" id="recentEmpty" style="display:none;">
                    <i class="fa-regular fa-clock"></i>
                    <p>No recent files yet — sync a device to get started</p>
                </div>
            </div>
        </div>

        <!-- ═══ 3. STARRED VIEW ═══════════════════════════════════════════ -->
        <div class="view-panel-inner" id="viewStarred">
            <div class="content-header">
                <div class="breadcrumbs"><span class="bc-item active"><i class="fa-solid fa-star"></i> Starred</span></div>
            </div>
            <div class="scroll-area">
                <div class="section-title">Starred devices &amp; folders</div>
                <div class="device-grid" id="starredGrid"></div>
            </div>
        </div>

        <!-- ═══ 4. SYNC CENTER ════════════════════════════════════════════ -->
        <div class="view-panel-inner" id="viewSync">
            <div id="viewSyncInner" style="padding:16px; overflow-y:auto; flex:1;">
                <div class="sync-hero">
                    <div class="sync-hdr-row">
                        <div class="sync-title-area">
                            <div class="sync-pulse" id="syncPulse"></div>
                            <div>
                                <h2 style="font-size:16px;font-weight:700;" id="syncMainTitle">Sync Activity</h2>
                                <div style="font-size:11.5px;color:var(--text-muted);" id="syncSubtitle">Differential sync active • Live Notion Cloud mirroring</div>
                            </div>
                        </div>
                        <div class="sync-controls">
                            <button class="btn-sync-action primary" onclick="startSync('all')"><i class="fa-solid fa-arrows-rotate"></i> Sync All</button>
                            <button class="btn-sync-action" onclick="startSync('c')"><i class="fa-solid fa-hard-drive"></i> C:</button>
                            <button class="btn-sync-action" onclick="startSync('phone')"><i class="fa-solid fa-mobile-screen-button"></i> Phone</button>
                            <button class="btn-sync-action danger" id="btnCancel" onclick="cancelSync()" style="display:none;"><i class="fa-solid fa-xmark"></i> Cancel</button>
                        </div>
                    </div>

                    <div class="progress-wrap">
                        <div class="progress-labels">
                            <span id="progressLabel">Progress: 0%</span>
                            <span id="progressDetail">0 / 0 files</span>
                        </div>
                        <div class="progress-track"><div class="progress-bar" id="progressBar"></div></div>
                    </div>

                    <div class="stats-grid">
                        <div class="stat-box"><div class="stat-lbl">Target</div><div class="stat-val" id="statTarget" style="font-size:14px;color:var(--accent-blue);">—</div></div>
                        <div class="stat-box"><div class="stat-lbl">Uploaded</div><div class="stat-val" id="statUploaded">0</div></div>
                        <div class="stat-box"><div class="stat-lbl">Remaining</div><div class="stat-val" id="statRemaining">0</div></div>
                        <div class="stat-box"><div class="stat-lbl">Speed</div><div class="stat-val" id="statSpeed" style="font-size:14px;">—</div></div>
                    </div>

                    <div class="active-file-box">
                        <i class="fa-solid fa-cloud-arrow-up fa-fade"></i>
                        <div class="afb-details">
                            <div class="afb-name" id="afbName">Waiting for sync to start...</div>
                            <div class="afb-path" id="afbPath">Run a sync command or tap one of the buttons above.</div>
                        </div>
                        <div class="afb-size" id="afbSize">—</div>
                    </div>
                </div>

                <!-- Sub tabs -->
                <div class="sync-tabs">
                    <button class="sync-tab-btn active" id="tabQueue" onclick="syncTab('queue')"><i class="fa-solid fa-list-check"></i> Queue <span class="tab-count" id="badgeQueue">0</span></button>
                    <button class="sync-tab-btn" id="tabHistory" onclick="syncTab('history')"><i class="fa-solid fa-clock-rotate-left"></i> History <span class="tab-count" id="badgeHistory">0</span></button>
                    <button class="sync-tab-btn" id="tabLogs" onclick="syncTab('logs')"><i class="fa-solid fa-terminal"></i> Logs</button>
                </div>

                <div id="subQueue">
                    <table class="sync-table">
                        <thead><tr><th>Change</th><th>File</th><th>Path</th><th>Size</th><th>Status</th></tr></thead>
                        <tbody id="queueBody"><tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:22px;">No files in queue.</td></tr></tbody>
                    </table>
                </div>
                <div id="subHistory" style="display:none;">
                    <table class="sync-table">
                        <thead><tr><th>File</th><th>Path</th><th>Size</th><th>Time</th><th>Result</th></tr></thead>
                        <tbody id="historyBody"><tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:22px;">No history yet.</td></tr></tbody>
                    </table>
                </div>
                <div id="subLogs" style="display:none;">
                    <div class="console-box" id="consoleBox"></div>
                </div>
            </div>
        </div>

    </div><!-- /.view-panel -->
</div><!-- /.main -->

<!-- Mobile Bottom Navigation -->
<nav class="mobile-bottom-nav" id="mobileBottomNav">
    <button class="mbn-item active" id="mbnDrive" onclick="switchTab('drive'); goRoot();">
        <i class="fa-solid fa-folder"></i>
        <span>Drive</span>
    </button>
    <button class="mbn-item" id="mbnRecent" onclick="switchTab('recent');">
        <i class="fa-solid fa-clock-rotate-left"></i>
        <span>Recent</span>
    </button>
    <button class="mbn-item" onclick="triggerFileInput();">
        <div class="mbn-new-circle"><i class="fa-solid fa-plus"></i></div>
        <span>Upload</span>
    </button>
    <button class="mbn-item" id="mbnStarred" onclick="switchTab('starred');">
        <i class="fa-solid fa-star"></i>
        <span>Starred</span>
    </button>
    <button class="mbn-item" id="mbnSync" onclick="switchTab('sync');">
        <i class="fa-solid fa-arrows-rotate"></i>
        <span>Sync</span>
    </button>
</nav>

<!-- File Context Action Sheet (Mobile & Desktop) -->
<div class="action-sheet-overlay" id="actionSheetOverlay" onclick="closeActionSheet(event)">
    <div class="action-sheet" id="actionSheet" onclick="event.stopPropagation()">
        <div class="as-header">
            <div class="as-icon" id="asIcon"><i class="fa-solid fa-file"></i></div>
            <div class="as-info">
                <div class="as-title" id="asTitle">filename.ext</div>
                <div class="as-subtitle" id="asSubtitle">Size • Date</div>
            </div>
            <button class="as-close" onclick="closeActionSheet()"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <div class="as-body">
            <button class="as-item" id="asBtnPreview" onclick="handleAction('preview')"><i class="fa-solid fa-eye"></i> Preview</button>
            <a class="as-item" id="asBtnBrowser" target="_blank"><i class="fa-solid fa-globe"></i> Open via Browser</a>
            <a class="as-item" id="asBtnNotion" target="_blank"><i class="fa-solid fa-cloud"></i> Open in Notion</a>
            <a class="as-item" id="asBtnDownload" target="_blank"><i class="fa-solid fa-download"></i> Download</a>
            <button class="as-item" onclick="handleAction('copy_browser_link')"><i class="fa-solid fa-link"></i> Copy Browser Link</button>
            <button class="as-item text-danger" onclick="handleAction('delete')"><i class="fa-solid fa-trash"></i> Delete from Cloud &amp; Drive</button>
        </div>
    </div>
</div>

<!-- Preview Modal -->
<div class="modal-overlay" id="previewModal" onclick="closeModal(event)">
    <div class="modal-box" onclick="event.stopPropagation()">
        <button class="modal-nav-btn modal-prev-btn" id="modalPrevBtn" onclick="navigatePreview(-1)" title="Previous (Left Arrow)"><i class="fa-solid fa-chevron-left"></i></button>
        <button class="modal-nav-btn modal-next-btn" id="modalNextBtn" onclick="navigatePreview(1)" title="Next (Right Arrow)"><i class="fa-solid fa-chevron-right"></i></button>
        <div class="modal-hdr">
            <span class="modal-title" id="modalTitle">Preview</span>
            <div class="modal-actions">
                <span class="modal-counter" id="modalCounter">1 / 1</span>
                <a class="action-btn" id="modalBrowser" target="_blank" title="Open via Browser" style="color:var(--accent-blue);"><i class="fa-solid fa-globe"></i> Open via Browser</a>
                <a class="action-btn" id="modalNotion" target="_blank" title="Open in Notion Cloud"><i class="fa-solid fa-cloud"></i> Open in Notion</a>
                <a class="action-btn" id="modalDl" title="Download"><i class="fa-solid fa-download"></i></a>
                <button class="action-btn" onclick="closeModal(event)"><i class="fa-solid fa-xmark"></i></button>
            </div>
        </div>
        <div class="modal-body" id="modalBody"></div>
    </div>
</div>

<!-- Optimization Settings Modal -->
<div class="modal-overlay" id="settingsModal" onclick="closeSettingsModal(event)">
    <div class="modal-box" style="max-width:560px;" onclick="event.stopPropagation()">
        <div class="modal-hdr">
            <span class="modal-title"><i class="fa-solid fa-sliders" style="color:var(--accent-blue);margin-right:8px;"></i> Optimization &amp; Performance Settings</span>
            <button class="action-btn" onclick="closeSettingsModal(event)"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <div class="modal-body" style="flex-direction:column;align-items:stretch;padding:20px;background:var(--bg-sidebar);gap:14px;">
            <div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:12px;padding:14px;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <div style="font-weight:600;font-size:13px;"><i class="fa-solid fa-globe" style="color:var(--accent-blue);margin-right:6px;"></i> Direct Browser Viewing</div>
                        <div style="font-size:11.5px;color:var(--text-muted);margin-top:3px;">Open cloud files directly in browser with 1 click</div>
                    </div>
                    <span style="background:rgba(52,168,83,0.15);color:#81C995;padding:3px 10px;border-radius:10px;font-size:11px;font-weight:600;"><i class="fa-solid fa-circle-check"></i> Active</span>
                </div>
            </div>

            <div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:12px;padding:14px;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <div style="font-weight:600;font-size:13px;"><i class="fa-solid fa-bolt" style="color:var(--accent-orange);margin-right:6px;"></i> 64KB Chunk Buffer Streaming</div>
                        <div style="font-size:11.5px;color:var(--text-muted);margin-top:3px;">Instant streaming playback without RAM buffering</div>
                    </div>
                    <span style="background:rgba(52,168,83,0.15);color:#81C995;padding:3px 10px;border-radius:10px;font-size:11px;font-weight:600;"><i class="fa-solid fa-gauge-high"></i> High Speed</span>
                </div>
            </div>

            <div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:12px;padding:14px;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <div style="font-weight:600;font-size:13px;"><i class="fa-solid fa-database" style="color:var(--accent-blue);margin-right:6px;"></i> SQLite Index &amp; WAL Cache</div>
                        <div style="font-size:11.5px;color:var(--text-muted);margin-top:3px;">Sub-millisecond queries for 10,000+ items</div>
                    </div>
                    <span style="background:rgba(168,199,250,0.15);color:var(--accent-blue);padding:3px 10px;border-radius:10px;font-size:11px;font-weight:600;">10,799 Indexed</span>
                </div>
                <div style="margin-top:12px;display:flex;gap:10px;align-items:center;">
                    <button class="btn-primary" id="btnOptDb" onclick="optimizeDatabase()" style="font-size:12px;padding:6px 14px;">
                        <i class="fa-solid fa-wand-magic-sparkles"></i> Vacuum &amp; Optimize DB
                    </button>
                    <span id="optDbStatus" style="font-size:11.5px;color:var(--text-muted);"></span>
                </div>
            </div>

            <div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:12px;padding:14px;">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <div style="font-weight:600;font-size:13px;"><i class="fa-solid fa-arrows-rotate" style="color:var(--accent-green);margin-right:6px;"></i> Real-Time Notion DB Watcher</div>
                        <div style="font-size:11.5px;color:var(--text-muted);margin-top:3px;">Auto-detects Notion Cloud deletions and syncs to browser</div>
                    </div>
                    <span style="background:rgba(52,168,83,0.15);color:#81C995;padding:3px 10px;border-radius:10px;font-size:11px;font-weight:600;"><i class="fa-solid fa-satellite-dish"></i> 8s Poller</span>
                </div>
            </div>
        </div>
    </div>
</div>

<!-- Drag-drop overlay -->
<div id="dropOverlay">
    <i class="fa-solid fa-cloud-arrow-up"></i>
    <h2>Drop to upload to Notion Cloud</h2>
    <p>Files will be synced to your Notion database</p>
</div>

<!-- Upload toast -->
<div id="uploadToast">
    <div class="ut-header"><span id="utTitle">Uploading...</span><span id="utPct">0%</span></div>
    <div class="ut-file" id="utFile"></div>
    <div class="ut-bar-bg"><div class="ut-bar" id="utBar"></div></div>
</div>

<!-- Hidden file inputs -->
<input type="file" id="fileInput" multiple style="display:none;" onchange="handleFiles(event)">
<input type="file" id="folderInput" webkitdirectory multiple style="display:none;" onchange="handleFiles(event)">

<script>
// ═══════════════════════════════════════════════════════════════════════════
// STATE MANAGEMENT
// ═══════════════════════════════════════════════════════════════════════════
const STATE = {
    tab: 'drive',          // drive | recent | starred | sync
    folderId: null,        // null = root
    driveData: { folders: [], files: [], breadcrumbs: [], total_files: 0, has_more: false },
    viewMode: 'grid',
    sortKey: 'name',
    sortDir: 1,
    // Search
    searchQuery: '',
    searchCategory: 'all',
    // Virtualization / Chunked Scroll
    loadedFiles: [],
    pageOffset: 0,
    fetchingMore: false,
    totalFiles: 0,
    // Context Action Sheet
    selectedItem: null,
    // Sync sub-tab
    syncSubTab: 'queue',
    lastCacheVer: -1,
};

const IMG_EXTS = new Set(['jpg','jpeg','png','webp','gif','svg','bmp','ico']);
const fileIcons = {
    pdf:'fa-file-pdf', doc:'fa-file-word', docx:'fa-file-word', txt:'fa-file-lines',
    xls:'fa-file-excel', xlsx:'fa-file-excel', csv:'fa-file-excel',
    ppt:'fa-file-powerpoint', pptx:'fa-file-powerpoint',
    jpg:'fa-file-image', jpeg:'fa-file-image', png:'fa-file-image',
    webp:'fa-file-image', gif:'fa-file-image', svg:'fa-file-image',
    mp4:'fa-file-video', mkv:'fa-file-video', mov:'fa-file-video', webm:'fa-file-video',
    mp3:'fa-file-audio', wav:'fa-file-audio', m4a:'fa-file-audio', opus:'fa-file-audio',
    zip:'fa-file-zipper', rar:'fa-file-zipper', '7z':'fa-file-zipper',
    py:'fa-file-code', js:'fa-file-code', ts:'fa-file-code',
    html:'fa-file-code', css:'fa-file-code', json:'fa-file-code'
};

function getFileIcon(ext) { return fileIcons[(ext||'').replace('.','').toLowerCase()] || 'fa-file'; }
function getFolderIcon(name) {
    if (!name) return 'fa-folder';
    const n = name.toLowerCase();
    if (n.includes('local disk') || n.includes('(c:)') || n.includes('(d:)')) return 'fa-hard-drive';
    if (n.includes('internal shared storage') || n.includes('internal storage') || n.includes('phone') || n.includes('nord')) return 'fa-mobile-screen-button';
    if (n.includes('sd card') || n.includes('sdcard')) return 'fa-sd-card';
    return 'fa-folder';
}
function isAndroid(p) {
    if (!p) return false;
    return p.includes('Internal shared storage') || p.includes('SD card') || p.startsWith('/storage') || p.startsWith('/sdcard');
}
function fmtBytes(b) {
    if (!b) return '0 B';
    const k=1024, s=['B','KB','MB','GB'];
    const i=Math.min(Math.floor(Math.log(b)/Math.log(k)), s.length-1);
    return (b/Math.pow(k,i)).toFixed(1)+' '+s[i];
}
function fmtDate(ts) {
    if (!ts || ts===0) return '—';
    return new Date(ts*1000).toLocaleDateString(undefined,{year:'numeric',month:'short',day:'numeric'});
}
function esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ═══════════════════════════════════════════════════════════════════════════
// NAVIGATION & TABS
// ═══════════════════════════════════════════════════════════════════════════
const NAV_IDS = { drive:'navDrive', recent:'navRecent', starred:'navStarred', sync:'navSync' };
const MBN_IDS = { drive:'mbnDrive', recent:'mbnRecent', starred:'mbnStarred', sync:'mbnSync' };
const VIEW_IDS = { drive:'viewDrive', recent:'viewRecent', starred:'viewStarred', sync:'viewSync' };

function switchTab(tab) {
    STATE.tab = tab;
    Object.entries(NAV_IDS).forEach(([k,id]) => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('active', k === tab);
    });
    Object.entries(MBN_IDS).forEach(([k,id]) => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('active', k === tab);
    });
    Object.entries(VIEW_IDS).forEach(([k,id]) => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('active', k === tab);
    });
    if (tab === 'drive') fetchDrive(STATE.folderId);
    else if (tab === 'recent') loadRecent();
    else if (tab === 'starred') loadStarred();
    else if (tab === 'sync') pollSync();
}

function goRoot() {
    STATE.folderId = null;
    STATE.searchQuery = '';
    const si = document.getElementById('searchInput');
    if (si) si.value = '';
    document.getElementById('searchFilters').style.display = 'none';
    fetchDrive(null);
}

// ═══════════════════════════════════════════════════════════════════════════
// DRIVE FETCH + VIRTUAL SCROLL (DCIM / Camera lag-free)
// ═══════════════════════════════════════════════════════════════════════════
const VIRTUAL_CHUNK = 80;

function fetchDrive(folderId, reset = true) {
    STATE.folderId = folderId;
    if (reset) {
        STATE.loadedFiles = [];
        STATE.pageOffset = 0;
        STATE.totalFiles = 0;
        STATE.fetchingMore = false;
    }
    _doFetch(folderId);
}

async function _doFetch(folderId) {
    if (STATE.fetchingMore) return;
    STATE.fetchingMore = true;
    try {
        let url = '/api/drive';
        const ps = new URLSearchParams({
            offset: STATE.pageOffset,
            limit: VIRTUAL_CHUNK,
            sort: STATE.sortKey,
            order: STATE.sortDir === 1 ? 'asc' : 'desc'
        });
        if (folderId) ps.set('folder_id', folderId);
        const res = await fetch(url + '?' + ps, { cache: 'no-store' });
        const d = await res.json();
        STATE.driveData = d;

        if (STATE.pageOffset === 0) {
            STATE.loadedFiles = d.files || [];
            _renderDriveShell(d);
        } else {
            STATE.loadedFiles = STATE.loadedFiles.concat(d.files || []);
            _appendFileCards(d.files || []);
        }

        STATE.totalFiles = d.total_files || STATE.loadedFiles.length;
        STATE.pageOffset += (d.files || []).length;
        STATE.driveData.has_more = d.has_more;
        _updateLoadSentinel();
    } catch(e) {
        console.error('Drive fetch error', e);
    } finally {
        STATE.fetchingMore = false;
    }
}

function _renderDriveShell(d) {
    const isRoot = !STATE.folderId && !STATE.searchQuery;
    _renderBreadcrumbs(d.breadcrumbs || []);

    document.getElementById('toolbar').style.display = isRoot ? 'none' : 'flex';
    document.getElementById('dashboardView').style.display = isRoot ? 'block' : 'none';
    document.getElementById('browserView').style.display = isRoot ? 'none' : 'block';

    if (isRoot) {
        _renderDashboard(d);
        return;
    }

    // Render folders
    const fg = document.getElementById('foldersGrid');
    document.getElementById('foldersSection').style.display = (d.folders && d.folders.length) ? 'block' : 'none';
    fg.innerHTML = (d.folders || []).map(f => _folderCard(f)).join('');

    // Render files
    const fg2 = document.getElementById('filesGrid');
    const lv = document.getElementById('listView');
    document.getElementById('filesSection').style.display = (d.files.length || d.has_more) ? 'block' : 'none';
    document.getElementById('emptyMsg').style.display = (!d.folders.length && !d.files.length && !d.has_more) ? 'block' : 'none';

    if (STATE.viewMode === 'grid') {
        lv.style.display = 'none';
        fg2.style.display = '';
        fg2.innerHTML = '';
        _appendFileCards(d.files || []);
    } else {
        fg2.style.display = 'none';
        lv.style.display = 'block';
        _renderTable();
    }
}

function _appendFileCards(files) {
    const fg2 = document.getElementById('filesGrid');
    if (STATE.viewMode !== 'grid') return;
    const startIdx = STATE.loadedFiles.length - files.length;
    fg2.insertAdjacentHTML('beforeend', files.map((f, i) => _fileCard(f, startIdx + i)).join(''));
    // Observe thumbnails for lazy loading
    fg2.querySelectorAll('img[data-src]').forEach(img => _thumbObserver.observe(img));
}

// Lazy thumbnail IntersectionObserver
const _thumbObserver = new IntersectionObserver((entries) => {
    entries.forEach(e => {
        if (e.isIntersecting) {
            const img = e.target;
            img.src = img.dataset.src;
            img.onload = () => {
                img.style.opacity = '1';
                const sk = img.previousElementSibling;
                if (sk && sk.classList && sk.classList.contains('skeleton')) sk.remove();
            };
            img.onerror = () => {
                const sk = img.previousElementSibling;
                if (sk && sk.classList && sk.classList.contains('skeleton')) sk.remove();
                const parent = img.parentElement;
                if (parent) {
                    parent.className = 'thumb thumb-image-cloud';
                    parent.innerHTML = `
                        <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;width:100%;height:100%;background:radial-gradient(circle, #201A30 0%, #12101B 100%);border:1px solid rgba(168,199,250,0.18);position:relative;">
                            <i class="fa-solid fa-image" style="font-size:32px;color:#A8C7FA;opacity:0.9;"></i>
                            <div style="position:absolute;bottom:6px;left:6px;background:rgba(168,199,250,0.15);color:#C2E7FF;font-size:9.5px;font-weight:700;padding:2px 5px;border-radius:4px;letter-spacing:0.5px;">IMG</div>
                            <div style="position:absolute;top:6px;right:6px;font-size:10px;color:#81C995;" title="Cloud Synced"><i class="fa-solid fa-cloud"></i></div>
                        </div>`;
                }
            };
            _thumbObserver.unobserve(img);
        }
    });
}, { rootMargin: '250px' });

// Load-more IntersectionObserver for virtual scroll
let _sentinelObs = null;
function _updateLoadSentinel() {
    const sentinel = document.getElementById('loadSentinel');
    if (!sentinel) return;
    sentinel.style.display = STATE.driveData.has_more ? 'flex' : 'none';
    if (STATE.driveData.has_more) {
        if (_sentinelObs) _sentinelObs.disconnect();
        _sentinelObs = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting && !STATE.fetchingMore && STATE.driveData.has_more) {
                if (STATE.searchQuery) _doSearchFetch();
                else _doFetch(STATE.folderId);
            }
        }, { rootMargin: '350px' });
        _sentinelObs.observe(sentinel);
    } else {
        if (_sentinelObs) _sentinelObs.disconnect();
    }
}

function _folderCard(f) {
    const icon = getFolderIcon(f.name);
    const countNum = (f.item_count !== undefined && f.item_count !== null) ? Number(f.item_count) : 0;
    const cnt = countNum === 1 ? '1 item' : `${countNum.toLocaleString()} items`;
    return `<div class="folder-card" data-id="${f.id}" onclick="fetchDrive('${f.id}')" title="${esc(f.name)}">
        <i class="fa-solid ${icon}"></i>
        <div class="folder-card-info">
            <div class="folder-card-name">${esc(f.name)}</div>
            <div class="folder-card-count">${cnt}</div>
        </div>
    </div>`;
}

function _fileCard(f, idx) {
    const ext = (f.extension || '').replace('.','').toLowerCase();
    const icon = getFileIcon(ext);
    const isImg = IMG_EXTS.has(ext);
    let thumbHtml;
    const viewUrl = '/view?id=' + encodeURIComponent(f.id||'') + '&path=' + encodeURIComponent(f.local_path||'');
    const isMobile = (f.local_path && (f.local_path.includes('Internal shared storage') || f.local_path.includes('SD card') || f.local_path.startsWith('/storage') || f.local_path.startsWith('/sdcard')));
    
    if (isImg) {
        thumbHtml = `<div class="skeleton"></div><img data-src="${viewUrl}" style="opacity:0;" alt="${esc(f.name)}" loading="lazy">`;
    } else if (ext === 'pdf') {
        thumbHtml = `<div class="thumb-pdf" style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;position:relative;"><i class="fa-solid fa-file-pdf pdf-icon"></i><div class="pdf-tag">PDF</div>${isMobile ? '<div style="position:absolute;top:6px;right:6px;font-size:10px;color:#81C995;" title="Cloud Synced"><i class="fa-solid fa-cloud"></i></div>' : ''}</div>`;
    } else if (['mp4','webm','mkv','mov','avi','flv','wmv','3gp'].includes(ext)) {
        thumbHtml = `<div class="thumb-video" style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;position:relative;"><div class="play-btn"><i class="fa-solid fa-play" style="margin-left:2px;"></i></div><div class="vid-tag" style="position:absolute;bottom:6px;left:6px;background:rgba(138,180,248,0.2);color:#A8C7FA;font-size:9.5px;font-weight:700;padding:2px 5px;border-radius:4px;">${ext.toUpperCase()}</div>${isMobile ? '<div style="position:absolute;top:6px;right:6px;font-size:10px;color:#81C995;" title="Cloud Synced"><i class="fa-solid fa-cloud"></i></div>' : ''}</div>`;
    } else if (['mp3','wav','ogg','m4a','opus','flac','aac','wma'].includes(ext)) {
        thumbHtml = `<div class="thumb-audio" style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;position:relative;"><i class="fa-solid fa-music"></i><div style="position:absolute;bottom:6px;left:6px;background:rgba(251,188,4,0.2);color:#FDD663;font-size:9.5px;font-weight:700;padding:2px 5px;border-radius:4px;">${ext.toUpperCase()}</div>${isMobile ? '<div style="position:absolute;top:6px;right:6px;font-size:10px;color:#81C995;" title="Cloud Synced"><i class="fa-solid fa-cloud"></i></div>' : ''}</div>`;
    } else if (['py','js','ts','jsx','tsx','html','css','json','md','sql','sh','bat','txt','env','c','cpp','java','xml','yaml','yml','prisma','toml'].includes(ext)) {
        const tag = ext ? ext.toUpperCase() : 'CODE';
        thumbHtml = `<div class="thumb-code" style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;position:relative;"><div class="code-badge">&lt;${esc(tag)}/&gt;</div>${isMobile ? '<div style="position:absolute;top:6px;right:6px;font-size:10px;color:#81C995;" title="Cloud Synced"><i class="fa-solid fa-cloud"></i></div>' : ''}</div>`;
    } else {
        thumbHtml = `<div style="display:flex;align-items:center;justify-content:center;width:100%;height:100%;background:#1A1C1E;position:relative;"><i class="fa-solid ${icon}"></i>${isMobile ? '<div style="position:absolute;top:6px;right:6px;font-size:10px;color:#81C995;" title="Cloud Synced"><i class="fa-solid fa-cloud"></i></div>' : ''}</div>`;
    }
    return `<div class="file-card" data-id="${f.id}" onclick="previewFile(${idx})" oncontextmenu="openActionSheet(event, ${idx}); return false;" title="${esc(f.name)}">
        <div class="thumb">${thumbHtml}</div>
        <div class="file-card-footer">
            <i class="fa-solid ${icon}"></i>
            <div class="file-card-name">${esc(f.name)}</div>
            <button class="card-more-btn" onclick="openActionSheet(event, ${idx})" title="More options"><i class="fa-solid fa-ellipsis-vertical"></i></button>
        </div>
        <div class="file-card-meta"><span>${fmtBytes(f.size_bytes)}</span><span>${fmtDate(f.mtime)}</span></div>
    </div>`;
}

function _renderTable() {
    const tbody = document.getElementById('tableBody');
    const mContainer = document.getElementById('mobileListContainer');
    
    const folderRows = (STATE.driveData.folders||[]).map(f => {
        const countNum = (f.item_count !== undefined && f.item_count !== null) ? Number(f.item_count) : 0;
        const cnt = countNum === 1 ? '1 item' : `${countNum.toLocaleString()} items`;
        return `
        <tr data-id="${f.id}" onclick="fetchDrive('${f.id}')">
            <td><div class="tname"><i class="fa-solid ${getFolderIcon(f.name)}"></i><span>${esc(f.name)}</span></div></td>
            <td>Folder (${cnt})</td><td>${fmtDate(f.mtime)}</td><td>—</td>
            <td><a class="action-btn" href="/download-folder?id=${f.id}" onclick="event.stopPropagation()"><i class="fa-solid fa-download"></i> ZIP</a></td>
        </tr>`;
    }).join('');
        
    const fileRows = STATE.loadedFiles.map((f,i) => {
        const ext = (f.extension||'').replace('.','').toLowerCase();
        return `<tr data-id="${f.id}" onclick="previewFile(${i})" oncontextmenu="openActionSheet(event, ${i}); return false;">
            <td><div class="tname"><i class="fa-solid ${getFileIcon(ext)}"></i><span>${esc(f.name)}</span></div></td>
            <td>${ext.toUpperCase()||'File'}</td><td>${fmtDate(f.mtime)}</td><td>${fmtBytes(f.size_bytes)}</td>
            <td><div style="display:flex;gap:6px;">
                <a class="action-btn" href="/view?id=${f.id}&path=${encodeURIComponent(f.local_path||'')}" target="_blank" title="Open via Browser" onclick="event.stopPropagation()"><i class="fa-solid fa-globe"></i></a>
                <a class="action-btn" href="https://www.notion.so/${f.id}" target="_blank" title="Open in Notion" onclick="event.stopPropagation()"><i class="fa-solid fa-cloud"></i></a>
                <a class="action-btn" href="/download?id=${f.id}&path=${encodeURIComponent(f.local_path||'')}" title="Download" onclick="event.stopPropagation()"><i class="fa-solid fa-download"></i></a>
                <button class="action-btn" onclick="openActionSheet(event, ${i})"><i class="fa-solid fa-ellipsis"></i></button>
            </div></td>
        </tr>`;
    }).join('');
    
    tbody.innerHTML = folderRows + fileRows;

    // Mobile stacked list
    const mFolders = (STATE.driveData.folders||[]).map(f => {
        const countNum = (f.item_count !== undefined && f.item_count !== null) ? Number(f.item_count) : 0;
        const cnt = countNum === 1 ? '1 item' : `${countNum.toLocaleString()} items`;
        return `
        <div class="mobile-list-item" data-id="${f.id}" onclick="fetchDrive('${f.id}')">
            <div class="mli-icon"><i class="fa-solid ${getFolderIcon(f.name)}"></i></div>
            <div class="mli-content">
                <div class="mli-name">${esc(f.name)}</div>
                <div class="mli-meta">Folder • ${cnt}</div>
            </div>
            <i class="fa-solid fa-chevron-right" style="color:var(--text-muted);font-size:12px;"></i>
        </div>`;
    }).join('');

    const mFiles = STATE.loadedFiles.map((f,i) => {
        const ext = (f.extension||'').replace('.','').toLowerCase();
        return `<div class="mobile-list-item" data-id="${f.id}" onclick="previewFile(${i})">
            <div class="mli-icon"><i class="fa-solid ${getFileIcon(ext)}"></i></div>
            <div class="mli-content">
                <div class="mli-name">${esc(f.name)}</div>
                <div class="mli-meta">${fmtBytes(f.size_bytes)} • ${fmtDate(f.mtime)}</div>
            </div>
            <button class="card-more-btn" onclick="openActionSheet(event, ${i})" style="padding:8px;"><i class="fa-solid fa-ellipsis-vertical"></i></button>
        </div>`;
    }).join('');

    mContainer.innerHTML = mFolders + mFiles;
}

// ═══════════════════════════════════════════════════════════════════════════
// DASHBOARD (Root view)
// ═══════════════════════════════════════════════════════════════════════════
function _renderDashboard(d) {
    const deviceGrid = document.getElementById('deviceGrid');
    const ROOT_NAMES = new Set(['Local Disk (C:)','Local Disk (D:)','Internal shared storage','SD card','Internal Storage','SD Card']);
    const rootFolders = (d.folders || []).filter(f => ROOT_NAMES.has(f.name) || f.name.startsWith('Local Disk') || f.name.includes('storage') || f.name.includes('SD card'));
    
    if (rootFolders.length > 0) {
        deviceGrid.innerHTML = rootFolders.map(f => {
            const icon = getFolderIcon(f.name);
            const countNum = (f.item_count !== undefined && f.item_count !== null) ? Number(f.item_count) : 0;
            const cnt = countNum === 1 ? '1 item' : `${countNum.toLocaleString()} items`;
            return `<div class="device-card" data-id="${f.id}" onclick="fetchDrive('${f.id}')">
                <div class="device-icon-wrap"><i class="fa-solid ${icon}"></i></div>
                <div class="device-name">${esc(f.name)}</div>
                <div class="device-meta"><span>${cnt}</span><span style="color:var(--accent-green);"><i class="fa-solid fa-circle-check" style="font-size:10px;"></i> Synced</span></div>
            </div>`;
        }).join('');
    } else {
        deviceGrid.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:10px 0;">No devices synced yet. Run Notion_Sync to sync your drives.</div>';
    }

    // Quick access
    const qRow = document.getElementById('quickAccessRow');
    const qTitle = document.getElementById('quickAccessTitle');
    const others = (d.folders || []).filter(f => !ROOT_NAMES.has(f.name) && !f.name.startsWith('Local Disk')).slice(0, 12);
    if (others.length > 0) {
        if (qTitle) qTitle.style.display = 'block';
        if (qRow) {
            qRow.style.display = 'flex';
            qRow.innerHTML = others.map(f => {
                const countNum = (f.item_count !== undefined && f.item_count !== null) ? Number(f.item_count) : 0;
                const cnt = countNum === 1 ? '1 item' : `${countNum.toLocaleString()} items`;
                return `
                <div class="recent-chip" data-id="${f.id}" onclick="fetchDrive('${f.id}')">
                    <i class="fa-solid ${getFolderIcon(f.name)}"></i>
                    <div class="recent-chip-info"><div class="recent-chip-name">${esc(f.name)}</div><div class="recent-chip-meta">${cnt}</div></div>
                </div>`;
            }).join('');
        }
    } else {
        if (qTitle) qTitle.style.display = 'none';
        if (qRow) qRow.style.display = 'none';
    }
}

function previewRecentFile(id) {
    const item = STATE.loadedFiles.find(x => x.id === id);
    if (item) {
        previewFile(STATE.loadedFiles.indexOf(item));
        return;
    }
    fetch('/api/drive?limit=100').then(r=>r.json()).then(d => {
        const found = (d.files || []).find(x => x.id === id);
        if (found) {
            STATE.loadedFiles = [found];
            previewFile(0);
        } else {
            window.open('https://www.notion.so/' + id, '_blank');
        }
    }).catch(() => {
        window.open('https://www.notion.so/' + id, '_blank');
    });
}

// ═══════════════════════════════════════════════════════════════════════════
// RECENT & STARRED VIEWS
// ═══════════════════════════════════════════════════════════════════════════
async function loadRecent() {
    const grid = document.getElementById('recentGrid');
    const empty = document.getElementById('recentEmpty');
    try {
        const d = await fetch('/api/recent').then(r=>r.json());
        const files = d.files || [];
        if (!files.length) { empty.style.display='block'; grid.innerHTML=''; return; }
        empty.style.display='none';
        grid.innerHTML = files.map((f,i) => _fileCard(f, i)).join('');
        grid.querySelectorAll('img[data-src]').forEach(img => _thumbObserver.observe(img));
    } catch(e) {}
}

async function loadStarred() {
    const grid = document.getElementById('starredGrid');
    try {
        const d = await fetch('/api/starred').then(r=>r.json());
        const items = d.items || [];
        grid.innerHTML = items.map(f => `<div class="device-card" data-id="${f.id}" onclick="fetchDrive('${f.id}'); switchTab('drive');">
            <div class="device-icon-wrap"><i class="fa-solid ${getFolderIcon(f.name)}"></i></div>
            <div class="device-name">${esc(f.name)}</div>
            <div class="device-meta"><span>${f.item_count||''}</span></div>
        </div>`).join('') || '<div style="color:var(--text-muted);padding:10px 0;font-size:13px;">No starred items.</div>';
    } catch(e) {}
}

// ═══════════════════════════════════════════════════════════════════════════
// BREADCRUMBS
// ═══════════════════════════════════════════════════════════════════════════
function _renderBreadcrumbs(bcs) {
    const bc = document.getElementById('breadcrumbs');
    let html = `<span class="bc-item ${!STATE.folderId?'active':''}" onclick="goRoot()"><i class="fa-solid fa-hard-drive"></i> My Drive</span>`;
    if (bcs && bcs.length) {
        bcs.forEach((b,i) => {
            const isLast = i === bcs.length-1;
            html += `<span class="bc-sep"><i class="fa-solid fa-chevron-right"></i></span>
            <span class="bc-item ${isLast?'active':''}" onclick="fetchDrive('${b.id}')">${esc(b.name)}</span>`;
        });
    }
    bc.innerHTML = html;
}

// ═══════════════════════════════════════════════════════════════════════════
// SORT & VIEW CONTROLS
// ═══════════════════════════════════════════════════════════════════════════
function changeSort(key) { STATE.sortKey = key; fetchDrive(STATE.folderId); }
function toggleSortDir() {
    STATE.sortDir *= -1;
    document.getElementById('sortDirIcon').className = STATE.sortDir===1 ? 'fa-solid fa-arrow-down-short-wide' : 'fa-solid fa-arrow-up-wide-short';
    fetchDrive(STATE.folderId);
}
function tableSort(key) {
    if (STATE.sortKey === key) toggleSortDir();
    else { STATE.sortKey = key; STATE.sortDir = 1; document.getElementById('sortSelect').value = key; fetchDrive(STATE.folderId); }
}
function setView(mode) {
    STATE.viewMode = mode;
    document.getElementById('btnGrid').classList.toggle('active', mode==='grid');
    document.getElementById('btnList').classList.toggle('active', mode==='list');
    document.getElementById('filesGrid').style.display = mode==='grid' ? '' : 'none';
    document.getElementById('listView').style.display = mode==='list' ? 'block' : 'none';
    if (mode==='list') _renderTable();
    else { document.getElementById('filesGrid').innerHTML = ''; _appendFileCards(STATE.loadedFiles); }
}

// ═══════════════════════════════════════════════════════════════════════════
// SEARCH WITH CATEGORY FILTERS
// ═══════════════════════════════════════════════════════════════════════════
let _searchTimer;
function handleSearch() {
    clearTimeout(_searchTimer);
    const q = document.getElementById('searchInput').value.trim();
    STATE.searchQuery = q;
    const sf = document.getElementById('searchFilters');
    if (q) sf.style.display = 'flex';
    else if (window.innerWidth > 768) sf.style.display = 'none';
    
    if (!q) { fetchDrive(STATE.folderId); return; }
    _searchTimer = setTimeout(_doSearchFetch, 280);
}

function setSearchCategory(cat, btn) {
    STATE.searchCategory = cat;
    document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
    if (btn) btn.classList.add('active');
    _doSearchFetch();
}

async function _doSearchFetch() {
    const q = STATE.searchQuery;
    const cat = STATE.searchCategory;
    try {
        const d = await fetch(`/api/search?q=${encodeURIComponent(q)}&cat=${encodeURIComponent(cat)}`).then(r=>r.json());
        STATE.driveData = d;
        STATE.loadedFiles = d.files || [];
        STATE.folderId = null;
        _renderBreadcrumbs(d.breadcrumbs || []);
        document.getElementById('dashboardView').style.display = 'none';
        document.getElementById('browserView').style.display = 'block';
        document.getElementById('toolbar').style.display = 'flex';
        document.getElementById('foldersSection').style.display = (d.folders && d.folders.length) ? 'block' : 'none';
        document.getElementById('foldersGrid').innerHTML = (d.folders||[]).map(f=>_folderCard(f)).join('');
        const fg2 = document.getElementById('filesGrid');
        fg2.innerHTML = '';
        _appendFileCards(STATE.loadedFiles);
        document.getElementById('listView').style.display = 'none';
        document.getElementById('emptyMsg').style.display = (!d.folders.length && !d.files.length) ? 'block' : 'none';
        document.getElementById('loadSentinel').style.display = 'none';
    } catch(e) {
        console.error('Search error', e);
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// CONTEXT ACTION SHEET (Mobile bottom sheet / Desktop popup)
// ═══════════════════════════════════════════════════════════════════════════
function openActionSheet(e, idx) {
    if (e) { e.preventDefault(); e.stopPropagation(); }
    const f = STATE.loadedFiles[idx];
    if (!f) return;
    STATE.selectedItem = f;
    
    const ext = (f.extension||'').replace('.','').toLowerCase();
    document.getElementById('asIcon').innerHTML = `<i class="fa-solid ${getFileIcon(ext)}"></i>`;
    document.getElementById('asTitle').innerText = f.name;
    document.getElementById('asSubtitle').innerText = `${fmtBytes(f.size_bytes)} • ${fmtDate(f.mtime)}`;
    
    const notionUrl = `https://www.notion.so/${f.id}`;
    const viewUrl = `/view?id=${encodeURIComponent(f.id||'')}&path=${encodeURIComponent(f.local_path||'')}`;
    const dlUrl = `/download?id=${encodeURIComponent(f.id||'')}&path=${encodeURIComponent(f.local_path||'')}`;
    
    const btnBrowser = document.getElementById('asBtnBrowser');
    if (btnBrowser) btnBrowser.href = viewUrl;
    const btnNotion = document.getElementById('asBtnNotion');
    if (btnNotion) btnNotion.href = notionUrl;
    const btnDl = document.getElementById('asBtnDownload');
    if (btnDl) btnDl.href = dlUrl;
    
    document.getElementById('actionSheetOverlay').classList.add('open');
}

function closeActionSheet(e) {
    document.getElementById('actionSheetOverlay').classList.remove('open');
}

async function handleAction(action) {
    const f = STATE.selectedItem;
    if (!f) return;
    closeActionSheet();

    if (action === 'preview') {
        const idx = STATE.loadedFiles.indexOf(f);
        if (idx !== -1) previewFile(idx);
    } else if (action === 'copy_browser_link' || action === 'copy_link') {
        const link = `${window.location.origin}/view?id=${encodeURIComponent(f.id||'')}&path=${encodeURIComponent(f.local_path||'')}`;
        navigator.clipboard.writeText(link).then(() => {
            alert('Browser direct view link copied to clipboard!');
        }).catch(() => {
            prompt('Copy Browser Direct URL:', link);
        });
    } else if (action === 'copy_notion_link') {
        const link = `https://www.notion.so/${f.id}`;
        navigator.clipboard.writeText(link).then(() => {
            alert('Notion Cloud link copied to clipboard!');
        }).catch(() => {
            prompt('Copy Notion Cloud URL:', link);
        });
    } else if (action === 'delete') {
        if (!confirm(`Delete "${f.name}" from Notion Cloud and local drive?`)) return;
        try {
            const res = await fetch('/api/file/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: f.id, path: f.local_path})
            });
            const data = await res.json();
            if (data.success) {
                // Animate removal from DOM
                const card = document.querySelector(`[data-id="${f.id}"]`);
                if (card) {
                    card.style.transition = 'opacity 0.25s, transform 0.25s';
                    card.style.opacity = '0';
                    card.style.transform = 'scale(0.85)';
                    setTimeout(() => card.remove(), 250);
                }
                STATE.loadedFiles = STATE.loadedFiles.filter(x => x.id !== f.id);
                fetchStorageStats();
            } else {
                alert('Error deleting file: ' + (data.error || 'Unknown error'));
            }
        } catch(err) {
            alert('Failed to connect to server.');
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// PREVIEW MODAL
// ═══════════════════════════════════════════════════════════════════════════
function showFallbackCloud(f) {
    const notionUrl = 'https://www.notion.so/' + (f.id || '');
    const viewUrl = '/view?id=' + encodeURIComponent(f.id||'') + '&path=' + encodeURIComponent(f.local_path||'');
    const dlUrl = '/download?id=' + encodeURIComponent(f.id||'') + '&path=' + encodeURIComponent(f.local_path||'');
    const ext = (f.extension || '').replace('.','').toUpperCase();
    const isMobile = (f.local_path && (f.local_path.includes('Internal shared storage') || f.local_path.includes('SD card') || f.local_path.startsWith('/storage') || f.local_path.startsWith('/sdcard')));
    
    let iconClass = 'fa-cloud';
    if (['PNG','JPG','JPEG','WEBP','GIF'].includes(ext)) iconClass = 'fa-image';
    else if (['MP4','WEBM','MKV','MOV','AVI','3GP'].includes(ext)) iconClass = 'fa-film';
    else if (ext === 'PDF') iconClass = 'fa-file-pdf';
    else if (['PY','JS','TS','HTML','CSS','JSON','TXT','MD'].includes(ext)) iconClass = 'fa-file-code';

    return '<div style="padding:40px 24px;text-align:center;color:#E8EAED;width:100%;max-width:540px;margin:0 auto;">' +
        '<div style="width:68px;height:68px;border-radius:20px;background:radial-gradient(circle, rgba(168,199,250,0.18) 0%, rgba(168,199,250,0.06) 100%);border:1px solid rgba(168,199,250,0.25);display:inline-flex;align-items:center;justify-content:center;margin-bottom:18px;">' +
            '<i class="fa-solid ' + iconClass + '" style="font-size:30px;color:#A8C7FA;"></i>' +
        '</div>' +
        '<div><div style="display:inline-block;padding:4px 12px;border-radius:12px;background:rgba(52,168,83,0.15);color:#81C995;font-size:11.5px;font-weight:600;margin-bottom:14px;"><i class="fa-solid fa-circle-check"></i> Notion Cloud Indexed</div></div>' +
        '<h3 style="margin:0 0 6px;font-size:16px;font-weight:600;word-break:break-all;">' + esc(f.name) + '</h3>' +
        '<p style="color:#9AA0A6;margin:0 0 8px;font-size:12.5px;">' + fmtBytes(f.size_bytes) + ' • ' + (ext || 'FILE') + '</p>' +
        (isMobile ? '<p style="color:#80868B;margin:0 0 20px;font-size:11.5px;"><i class="fa-solid fa-mobile-screen"></i> Mobile storage item • Connect phone via USB/ADB for live raw media streaming</p>' : '<p style="color:#80868B;margin:0 0 20px;font-size:11.5px;">Indexed in Notion Cloud Database</p>') +
        '<div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap;">' +
            '<a class="action-btn" href="' + viewUrl + '" target="_blank" style="display:inline-flex;padding:9px 20px;font-size:13px;font-weight:600;background:#1A73E8;color:#fff;border-radius:24px;text-decoration:none;"><i class="fa-solid fa-globe"></i> Open via Browser</a>' +
            '<a class="action-btn" href="' + notionUrl + '" target="_blank" style="display:inline-flex;padding:9px 18px;font-size:13px;background:#222426;color:#E8EAED;border:1px solid #303234;border-radius:24px;text-decoration:none;"><i class="fa-solid fa-cloud"></i> Open in Notion</a>' +
            '<a class="action-btn" href="' + dlUrl + '" style="display:inline-flex;padding:9px 18px;font-size:13px;background:#222426;color:#E8EAED;border:1px solid #303234;border-radius:24px;text-decoration:none;"><i class="fa-solid fa-download"></i> Download</a>' +
        '</div>' +
    '</div>';
}

function previewFile(idx) {
    const f = STATE.loadedFiles[idx];
    if (!f) return;
    STATE.previewIndex = idx;
    const modal = document.getElementById('previewModal');
    const body = document.getElementById('modalBody');
    document.getElementById('modalTitle').innerText = f.name;
    
    // Update Counter and Navigation buttons
    const counter = document.getElementById('modalCounter');
    if (counter) counter.innerText = `${idx + 1} / ${STATE.loadedFiles.length}`;

    const prevBtn = document.getElementById('modalPrevBtn');
    const nextBtn = document.getElementById('modalNextBtn');
    if (prevBtn) prevBtn.style.display = STATE.loadedFiles.length > 1 ? 'flex' : 'none';
    if (nextBtn) nextBtn.style.display = STATE.loadedFiles.length > 1 ? 'flex' : 'none';

    const viewUrl = '/view?id=' + encodeURIComponent(f.id||'') + '&path=' + encodeURIComponent(f.local_path||'');
    const dlUrl = '/download?id=' + encodeURIComponent(f.id||'') + '&path=' + encodeURIComponent(f.local_path||'');
    const notionUrl = 'https://www.notion.so/' + (f.id || '');
    
    const dlBtn = document.getElementById('modalDl');
    if (dlBtn) dlBtn.href = dlUrl;
    const browserBtn = document.getElementById('modalBrowser');
    if (browserBtn) browserBtn.href = viewUrl;
    const notionBtn = document.getElementById('modalNotion');
    if (notionBtn) notionBtn.href = notionUrl;

    body.innerHTML = '';
    const ext = (f.extension||'').toLowerCase();
    
    if (ext === '.pdf') {
        const iframe = document.createElement('iframe');
        iframe.src = viewUrl;
        iframe.style.cssText = 'width:100%;height:78vh;border:none;border-radius:6px;';
        body.appendChild(iframe);
    } else if (['.png','.jpg','.jpeg','.webp','.svg','.gif','.ico','.bmp'].includes(ext)) {
        const wrap = document.createElement('div');
        wrap.style.cssText = 'display:flex;align-items:center;justify-content:center;width:100%;';
        const img = document.createElement('img');
        img.src = viewUrl;
        img.style.cssText = 'max-width:100%;max-height:78vh;object-fit:contain;border-radius:6px;';
        img.alt = f.name;
        img.onerror = function() { wrap.innerHTML = showFallbackCloud(f); };
        wrap.appendChild(img);
        body.appendChild(wrap);
    } else if (['.mp4','.webm','.mkv','.mov','.avi','.3gp'].includes(ext)) {
        const wrap = document.createElement('div');
        wrap.style.cssText = 'display:flex;align-items:center;justify-content:center;width:100%;';
        const video = document.createElement('video');
        video.controls = true;
        video.autoplay = true;
        video.playsInline = true;
        video.preload = 'metadata';
        video.src = viewUrl;
        video.style.cssText = 'max-width:100%;max-height:78vh;border-radius:6px;';
        video.onerror = function() { wrap.innerHTML = showFallbackCloud(f); };
        wrap.appendChild(video);
        body.appendChild(wrap);
    } else if (['.mp3','.wav','.ogg','.m4a','.opus','.flac'].includes(ext)) {
        const wrap = document.createElement('div');
        wrap.style.cssText = 'padding:50px 20px;text-align:center;width:100%;';
        const audio = document.createElement('audio');
        audio.controls = true;
        audio.autoplay = true;
        audio.src = viewUrl;
        audio.style.cssText = 'width:80%;max-width:460px;';
        audio.onerror = function() { wrap.innerHTML = showFallbackCloud(f); };
        wrap.appendChild(audio);
        body.appendChild(wrap);
    } else {
        const iframe = document.createElement('iframe');
        iframe.src = viewUrl;
        iframe.style.cssText = 'width:100%;height:78vh;border:none;border-radius:6px;background:#111;';
        body.appendChild(iframe);
    }
    modal.classList.add('open');
}

function navigatePreview(dir) {
    if (!STATE.loadedFiles || !STATE.loadedFiles.length) return;
    let nextIdx = (STATE.previewIndex || 0) + dir;
    if (nextIdx < 0) nextIdx = STATE.loadedFiles.length - 1;
    if (nextIdx >= STATE.loadedFiles.length) nextIdx = 0;
    previewFile(nextIdx);
}

function closeModal(e) {
    document.getElementById('previewModal').classList.remove('open');
    document.getElementById('modalBody').innerHTML = '';
}
function openSettingsModal() {
    document.getElementById('settingsModal').classList.add('open');
}
function closeSettingsModal(e) {
    document.getElementById('settingsModal').classList.remove('open');
}
async function optimizeDatabase() {
    const btn = document.getElementById('btnOptDb');
    const status = document.getElementById('optDbStatus');
    btn.disabled = true;
    btn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Optimizing...';
    try {
        const res = await fetch('/api/db/optimize', {method:'POST'});
        const data = await res.json();
        if (data.success) {
            status.innerText = 'Database optimized!';
            status.style.color = 'var(--accent-green)';
            fetchStorageStats();
        } else {
            status.innerText = data.error || 'Optimization failed';
            status.style.color = 'var(--accent-red)';
        }
    } catch(err) {
        status.innerText = 'Network error';
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> Vacuum & Optimize DB';
    }
}
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        closeModal(e);
        closeActionSheet(e);
        closeSettingsModal(e);
    } else if (e.key === 'ArrowLeft') {
        const modal = document.getElementById('previewModal');
        if (modal && modal.classList.contains('open')) navigatePreview(-1);
    } else if (e.key === 'ArrowRight') {
        const modal = document.getElementById('previewModal');
        if (modal && modal.classList.contains('open')) navigatePreview(1);
    }
});

// ═══════════════════════════════════════════════════════════════════════════
// STORAGE STATS & REFRESH
// ═══════════════════════════════════════════════════════════════════════════
async function fetchStorageStats() {
    try {
        const st = await fetch('/api/stats').then(r=>r.json());
        const gb = (st.total_mb/1024).toFixed(2);
        document.getElementById('storageDetail').innerText = `${gb} GB • ${st.total_files.toLocaleString()} files`;
    } catch(e) {}
}
async function refreshDrive() {
    const icon = document.getElementById('refreshIcon');
    icon.className = 'fa-solid fa-arrows-rotate fa-spin';
    try { await fetch('/api/refresh'); await fetchDrive(STATE.folderId); } finally {
        icon.className = 'fa-solid fa-arrows-rotate';
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// AUTH
// ═══════════════════════════════════════════════════════════════════════════
async function checkAuth() {
    try {
        const d = await fetch('/api/auth/status').then(r=>r.json());
        if (d.protected) document.getElementById('btnLock').style.display='flex';
    } catch(e) {}
}
async function lockDrive() {
    if (!confirm('Lock Notion Drive and sign out?')) return;
    await fetch('/api/auth/logout', {method:'POST'});
    location.reload();
}

// ═══════════════════════════════════════════════════════════════════════════
// SYNC CENTER
// ═══════════════════════════════════════════════════════════════════════════
function syncTab(t) {
    STATE.syncSubTab = t;
    ['queue','history','logs'].forEach(k => {
        document.getElementById('tab'+k.charAt(0).toUpperCase()+k.slice(1)).classList.toggle('active', k===t);
        document.getElementById('sub'+k.charAt(0).toUpperCase()+k.slice(1)).style.display = k===t ? 'block' : 'none';
    });
}

async function startSync(target) {
    closeNewMenu();
    closeMobileMenu();
    const ok = await fetch('/api/sync/start?target='+target, {method:'POST'}).then(r=>r.json()).catch(()=>({success:false}));
    if (!ok.success) return;
    switchTab('sync');
    document.getElementById('btnCancel').style.display='flex';
}
async function cancelSync() {
    await fetch('/api/sync/cancel', {method:'POST'});
    document.getElementById('btnCancel').style.display='none';
}

async function pollSync() {
    try {
        const st = await fetch('/api/sync/status').then(r=>r.json());

        if (st.cache_version !== undefined && st.cache_version !== STATE.lastCacheVer) {
            STATE.lastCacheVer = st.cache_version;
            if (STATE.tab === 'drive') fetchDrive(STATE.folderId, false);
            else if (STATE.tab === 'recent') loadRecent();
            else if (STATE.tab === 'starred') loadStarred();
            fetchStorageStats();
        }

        const badge = document.getElementById('syncNavBadge');
        const icon  = document.getElementById('syncNavIcon');
        const pulse = document.getElementById('syncPulse');
        if (st.is_running) {
            badge.className='nav-badge running'; badge.innerText=st.percent+'%';
            icon.className='fa-solid fa-arrows-rotate fa-spin';
            pulse.classList.add('running');
            document.getElementById('btnCancel').style.display='flex';
        } else {
            badge.className='nav-badge idle'; badge.innerText='Idle';
            icon.className='fa-solid fa-arrows-rotate';
            pulse.classList.remove('running');
            document.getElementById('btnCancel').style.display='none';
        }

        const t = st.is_running ? `Syncing ${st.current_target}...` : st.status_message;
        document.getElementById('syncMainTitle').innerText = t;
        document.getElementById('syncSubtitle').innerText = st.is_running ? `Active: ${st.current_file}` : 'Tracks .notion_sync_state.json • Skips unchanged files';
        document.getElementById('progressLabel').innerText = `Progress: ${st.percent}%`;
        document.getElementById('progressDetail').innerText = `${st.synced_files} / ${st.total_files} files (${st.remaining_files} remaining)`;
        document.getElementById('progressBar').style.width = st.percent+'%';
        document.getElementById('statTarget').innerText = st.current_target||'—';
        document.getElementById('statUploaded').innerText = st.synced_files;
        document.getElementById('statRemaining').innerText = st.remaining_files;
        document.getElementById('statSpeed').innerText = st.speed_str||'—';
        document.getElementById('badgeQueue').innerText = st.remaining_files||0;
        document.getElementById('badgeHistory').innerText = (st.history||[]).length;

        if (st.is_running && st.current_file!=='None') {
            document.getElementById('afbName').innerText = st.current_file;
            document.getElementById('afbPath').innerText = st.current_path;
            document.getElementById('afbSize').innerText = st.current_size_str;
        } else if (!st.is_running && st.total_files>0) {
            document.getElementById('afbName').innerText = 'All changes synchronized!';
            document.getElementById('afbPath').innerText = '100% up to date with Notion Cloud.';
            document.getElementById('afbSize').innerText = '\u2705 Done';
        }

        // Queue table
        const qb = document.getElementById('queueBody');
        if (st.queue && st.queue.length) {
            qb.innerHTML = st.queue.map(q => `<tr>
                <td><span class="tag ${q.tag||'NEW'}">${q.tag||'NEW'}</span></td>
                <td style="font-weight:500;">${esc(q.name)}</td>
                <td style="color:var(--text-muted);font-size:11.5px;">${esc(q.path)}</td>
                <td>${q.size_str}</td>
                <td><span class="pill ${q.status}">${q.status}</span></td>
            </tr>`).join('');
        } else if (!st.is_running && st.synced_files>0) {
            qb.innerHTML = `<tr><td colspan="5" style="text-align:center;color:#81C995;padding:22px;"><i class="fa-solid fa-circle-check"></i> All files synced!</td></tr>`;
        }

        // History table
        const hb = document.getElementById('historyBody');
        if (st.history && st.history.length) {
            hb.innerHTML = st.history.map(h => `<tr>
                <td style="font-weight:500;">${esc(h.name)}</td>
                <td style="color:var(--text-muted);font-size:11.5px;">${esc(h.path)}</td>
                <td>${h.size_str}</td><td>${h.time}</td>
                <td><span class="pill ${h.status==='success'?'synced':'failed'}">${h.status==='success'?'Synced':'Failed'}</span></td>
            </tr>`).join('');
        }

        // Console logs
        const cb = document.getElementById('consoleBox');
        if (st.logs && st.logs.length) {
            cb.innerHTML = st.logs.slice(-100).map(l=>`<div class="log-line">${esc(l)}</div>`).join('');
            cb.scrollTop = cb.scrollHeight;
        }
    } catch(e) { console.error('pollSync error', e); }
}

// ═══════════════════════════════════════════════════════════════════════════
// SSE — REAL-TIME LIVE SYNC (Automatic Notion Deletions & Additions)
// ═══════════════════════════════════════════════════════════════════════════
function connectSSE() {
    const dot = document.getElementById('sseDot');
    const lbl = document.getElementById('sseLabel');
    const es = new EventSource('/api/events');
    es.onopen = () => { dot.classList.add('connected'); lbl.innerText='Live'; };
    es.onerror = () => { dot.classList.remove('connected'); lbl.innerText='Reconnecting...'; setTimeout(connectSSE, 5000); es.close(); };
    
    es.addEventListener('file_deleted', e => {
        const d = JSON.parse(e.data);
        if (d.id) {
            const card = document.querySelector(`[data-id="${d.id}"]`);
            if (card) {
                card.style.transition = 'opacity 0.25s, transform 0.25s';
                card.style.opacity = '0';
                card.style.transform = 'scale(0.85)';
                setTimeout(() => card.remove(), 250);
            }
            STATE.loadedFiles = STATE.loadedFiles.filter(x => x.id !== d.id);
        }
        fetchStorageStats();
    });
    
    es.addEventListener('file_added', e => {
        const d = JSON.parse(e.data);
        if (d.version !== STATE.lastCacheVer) { 
            STATE.lastCacheVer = d.version; 
            if (STATE.tab==='drive') fetchDrive(STATE.folderId, false);
            else if (STATE.tab==='recent') loadRecent();
            fetchStorageStats();
        }
    });

    es.addEventListener('cache_updated', e => {
        const d = JSON.parse(e.data);
        if (d.deleted_ids && d.deleted_ids.length) {
            d.deleted_ids.forEach(delId => {
                const card = document.querySelector(`[data-id="${delId}"]`);
                if (card) card.remove();
            });
            STATE.loadedFiles = STATE.loadedFiles.filter(x => !d.deleted_ids.includes(x.id));
        }
        if (d.version !== STATE.lastCacheVer) { 
            STATE.lastCacheVer = d.version; 
            if (STATE.tab==='drive') fetchDrive(STATE.folderId, false);
            else if (STATE.tab==='recent') loadRecent();
            else if (STATE.tab==='starred') loadStarred();
            fetchStorageStats();
        }
    });
    
    es.addEventListener('sync_progress', e => { if (STATE.tab==='sync') pollSync(); });
}

// ═══════════════════════════════════════════════════════════════════════════
// DRAG & DROP + UPLOAD
// ═══════════════════════════════════════════════════════════════════════════
let _dragN = 0;
window.addEventListener('dragenter', e => { e.preventDefault(); if(++_dragN===1) document.getElementById('dropOverlay').classList.add('on'); });
window.addEventListener('dragleave', e => { e.preventDefault(); if(--_dragN<=0) { _dragN=0; document.getElementById('dropOverlay').classList.remove('on'); } });
window.addEventListener('dragover', e => e.preventDefault());
window.addEventListener('drop', async e => {
    e.preventDefault(); _dragN=0; document.getElementById('dropOverlay').classList.remove('on');
    const items = e.dataTransfer.items;
    if (!items || !items.length) return;
    const ps = Array.from(items).map(it => it.webkitGetAsEntry ? it.webkitGetAsEntry() : null).filter(Boolean);
    const allFiles = (await Promise.all(ps.map(x=>_traverseEntry(x,'')))).flat();
    if (allFiles.length) uploadFiles(allFiles);
});

function _traverseEntry(entry, path) {
    return new Promise(res => {
        if (entry.isFile) entry.file(f => { f.relPath = path+f.name; res([f]); });
        else if (entry.isDirectory) {
            entry.createReader().readEntries(async entries => {
                res((await Promise.all(entries.map(e=>_traverseEntry(e,path+entry.name+'/')))).flat());
            });
        } else res([]);
    });
}

function triggerFileInput() { closeNewMenu(); document.getElementById('fileInput').click(); }
function triggerFolderInput() { closeNewMenu(); document.getElementById('folderInput').click(); }
function handleFiles(e) { const files=Array.from(e.target.files); if(files.length) uploadFiles(files); e.target.value=''; }

async function uploadFiles(files) {
    const toast=document.getElementById('uploadToast');
    toast.style.display='flex';
    for (let i=0;i<files.length;i++) {
        const f=files[i];
        const rel=f.relPath||f.webkitRelativePath||f.name;
        const pct=Math.round(i/files.length*100);
        document.getElementById('utTitle').innerText=`Uploading (${i+1}/${files.length})...`;
        document.getElementById('utPct').innerText=pct+'%';
        document.getElementById('utFile').innerText=rel;
        document.getElementById('utBar').style.width=pct+'%';
        try {
            const b64 = await new Promise((res,rej)=>{const r=new FileReader();r.onload=()=>res(r.result.split(',')[1]||'');r.onerror=rej;r.readAsDataURL(f);});
            await fetch('/api/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:f.name,rel_path:rel,data_b64:b64,parent_folder_id:STATE.folderId})});
        } catch(err) { console.error('Upload error',f.name,err); }
    }
    document.getElementById('utBar').style.width='100%'; document.getElementById('utPct').innerText='100%';
    document.getElementById('utTitle').innerText='Upload Complete!';
    document.getElementById('utFile').innerText=`${files.length} file(s) uploaded.`;
    setTimeout(()=>{ toast.style.display='none'; fetchDrive(STATE.folderId); fetchStorageStats(); }, 2500);
}

function toggleNewMenu(e) { e.stopPropagation(); document.getElementById('newDropdown').classList.toggle('open'); }
function closeNewMenu() { document.getElementById('newDropdown').classList.remove('open'); }
document.addEventListener('click', closeNewMenu);

// ═══════════════════════════════════════════════════════════════════════════
// KEYBOARD SHORTCUTS & MOBILE DRAWER
// ═══════════════════════════════════════════════════════════════════════════
document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        const searchInput = document.getElementById('searchInput');
        if (searchInput) searchInput.focus();
    }
});

function toggleMobileMenu() {
    const sidebar = document.getElementById('sidebar');
    const backdrop = document.getElementById('drawerBackdrop');
    if (sidebar) sidebar.classList.toggle('open');
    if (backdrop) backdrop.classList.toggle('open');
}

function closeMobileMenu() {
    const sidebar = document.getElementById('sidebar');
    const backdrop = document.getElementById('drawerBackdrop');
    if (sidebar) sidebar.classList.remove('open');
    if (backdrop) backdrop.classList.remove('open');
}

// ═══════════════════════════════════════════════════════════════════════════
// INITIALIZATION
// ═══════════════════════════════════════════════════════════════════════════
checkAuth();
fetchDrive(null);
fetchStorageStats();
setInterval(fetchStorageStats, 20000);
setInterval(pollSync, 1200);
connectSSE();
</script>
</body>
</html>
"""


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

        if parsed.path == "/" or parsed.path == "/index.html":
            is_auth = check_authenticated(self.headers)
            html_to_serve = DRIVE_GUI_HTML if is_auth else LOCK_SCREEN_HTML
            body = html_to_serve.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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

            if text_content:
                # Render clean code / text viewer
                escaped_text = html.escape(text_content)
                html_resp = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{html.escape(item_name)} — Notion Cloud</title>
                <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
                <style>body{{background:#111213;color:#E8EAED;font-family:'Consolas','Courier New',monospace;margin:0;padding:20px;font-size:13px;line-height:1.6;}}
                .header{{display:flex;justify-content:space-between;align-items:center;padding:12px 18px;background:#181A1B;border:1px solid #303234;border-radius:10px;margin-bottom:16px;font-family:sans-serif;}}
                .btn{{background:#1A73E8;color:#fff;padding:6px 14px;border-radius:18px;text-decoration:none;font-size:12px;display:flex;align-items:center;gap:6px;font-weight:600;}}
                pre{{background:#181A1B;border:1px solid #303234;border-radius:10px;padding:18px;overflow-x:auto;white-space:pre-wrap;word-break:break-all;margin:0;}}
                </style></head>
                <body><div class="header"><div><strong>📄 {html.escape(item_name)}</strong> <span style="color:#9AA0A6;font-size:12px;margin-left:8px;">☁️ Notion Cloud</span></div><a class="btn" href="{notion_cloud_url}" target="_blank"><i class="fa-solid fa-cloud"></i> Open in Notion</a></div><pre>{escaped_text}</pre></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_resp.encode("utf-8"))))
                self.end_headers()
                self.wfile.write(html_resp.encode("utf-8"))
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

