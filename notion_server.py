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
import io
import time
import json
import zipfile
import mimetypes
import urllib.parse
import html
import threading
import subprocess
import requests
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, List

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

PORT = int(os.getenv("LOCAL_SERVER_PORT", "8765"))
NOTION_VERSION = "2022-06-28"
DEFAULT_API_KEY = os.getenv("NOTION_TOKEN", "")
DEFAULT_DB_ID = os.getenv("NOTION_DATABASE_ID", "").replace("-", "")
CACHE_FILE = Path.home() / ".notion_drive_cache.json"
STATE_FILE = Path(__file__).parent / ".notion_sync_state.json"

DRIVE_CACHE = {
    "items": {},
    "children": {},
    "root_items": [],
    "version": 1
}
CACHE_LOCK = threading.Lock()

def register_drive_cache_item(item_id: str, name: str, item_type: str, ext: str, size_mb: float, size_bytes: int, parent_id: str, local_path: str, mtime: float = 0):
    """Instantly registers a file or folder in the in-memory cache and bumps version so browser GUI live updates."""
    with CACHE_LOCK:
        DRIVE_CACHE["items"][item_id] = {
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
            "local_path": local_path
        }
        if parent_id:
            children_list = DRIVE_CACHE["children"].setdefault(parent_id, [])
            if item_id not in children_list:
                children_list.append(item_id)
        else:
            if item_id not in DRIVE_CACHE["root_items"]:
                DRIVE_CACHE["root_items"].append(item_id)
        DRIVE_CACHE["version"] = DRIVE_CACHE.get("version", 0) + 1

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
SYNC_LOCK = threading.Lock()

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
                curr_id = self.folder_cache[cache_k]
                register_drive_cache_item(curr_id, part, "Folder", "", 0, 0, curr_id, part)
            else:
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
                        add_sync_log(f"Scanning {container_name} ({linux_base}) excluding Android app data...")
                        cmd = f"find '{linux_base}/' -type f -not -path '*/.*' -not -path '*/Android*' -not -path '*/Android/*' -not -path '*/.thumbnails*' -not -path '*/LOST.DIR*' -not -path '*/.trash*' -exec stat -c '%n|%s|%Y' {{}} + 2>/dev/null"
                        try:
                            proc = subprocess.run(["adb", "-s", dev_id, "shell", cmd], capture_output=True, text=True, errors="ignore")
                            for line in proc.stdout.splitlines():
                                if "|" in line:
                                    parts = line.strip().split("|")
                                    if len(parts) == 3:
                                        fpath, fsize, fmtime = parts[0], int(parts[1]), float(parts[2])
                                        fname = fpath.split("/")[-1]
                                        ext = "." + fname.split(".")[-1].lower() if "." in fname else ""
                                        if "/Android" in fpath or "/." in fpath or "/LOST.DIR" in fpath or "/.thumbnails" in fpath:
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
            encoded_p = urllib.parse.quote(it["fpath"])
            open_url = f"http://127.0.0.1:{PORT}/view?path={encoded_p}"

            payload = {
                "parent": {"database_id": self.db_id},
                "icon": {"type": "emoji", "emoji": emoji},
                "properties": {
                    "Name": {"title": [{"text": {"content": it["name"]}}]},
                    "Type": {"select": {"name": "File"}},
                    "File Type": {"select": {"name": file_type}},
                    "File Extension": {"rich_text": [{"text": {"content": it["ext"]}}]},
                    "File Size": {"number": mb},
                    "Open in Browser": {"url": open_url},
                    "Description": {"rich_text": [{"text": {"content": f"Path: {it['display_path']}"}}]},
                    "Favorite": {"checkbox": False}
                }
            }
            if parent_notion_id:
                payload["properties"]["Parent Folder"] = {"relation": [{"id": parent_notion_id}]}

            new_page_id = None
            if it.get("existing_notion_id"):
                # MODIFIED: update existing page in Notion
                try:
                    res = requests.patch(f"https://api.notion.com/v1/pages/{it['existing_notion_id']}", headers=self.headers, json={"properties": payload["properties"]}, timeout=30)
                    status_ok = res.status_code == 200
                    new_page_id = it["existing_notion_id"]
                except Exception:
                    status_ok = False
            else:
                # NEW: create new page in Notion
                try:
                    res = requests.post("https://api.notion.com/v1/pages", headers=self.headers, json=payload, timeout=30)
                    status_ok = res.status_code == 200
                    if status_ok:
                        new_page_id = res.json()["id"].replace("-", "")
                except Exception:
                    status_ok = False

            if status_ok and new_page_id:
                # 1. Update persistent Git-style state immediately
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

                # 2. Update live in-memory browser cache
                register_drive_cache_item(
                    new_page_id,
                    it["name"],
                    "File",
                    it["ext"],
                    mb,
                    int(it["size"]),
                    parent_notion_id,
                    it["display_path"] if it["is_android"] else it["fpath"],
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
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(DRIVE_CACHE, f)
        except Exception as e:
            print(f"[!] Error saving disk cache: {e}")

def load_disk_cache():
    global DRIVE_CACHE
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                with CACHE_LOCK:
                    DRIVE_CACHE.update(data)
            enrich_cache_items()
            print(f"[+] Loaded {len(DRIVE_CACHE['items'])} items from disk cache!")
            return True
        except Exception as e:
            print(f"[!] Error loading disk cache: {e}")
    return False

def populate_cache_from_notion():
    global DRIVE_CACHE
    headers = {
        "Authorization": f"Bearer {DEFAULT_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION
    }
    items = []
    has_more = True
    start_cursor = None

    state = load_sync_state()
    pc_tracked = state.setdefault("files", {})
    android_tracked = state.setdefault("android_files", {})

    try:
        while has_more:
            payload = {"page_size": 100}
            if start_cursor:
                payload["start_cursor"] = start_cursor
            res = requests.post(f"https://api.notion.com/v1/databases/{DEFAULT_DB_ID}/query", headers=headers, json=payload, timeout=20).json()
            items.extend(res.get("results", []))
            has_more = res.get("has_more", False)
            start_cursor = res.get("next_cursor")

        cached_items = {}
        children_map = {}
        root_items = []

        for it in items:
            it_id = it["id"].replace("-", "")
            props = it.get("properties", {})
            title_list = props.get("Name", {}).get("title", [])
            name = title_list[0].get("plain_text", "") if title_list else ""
            clean_name = name.replace("📁 ", "").replace("📄 ", "").strip()
            item_type = props.get("Type", {}).get("select", {}).get("name", "File") if props.get("Type", {}).get("select") else "File"
            
            ext_list = props.get("File Extension", {}).get("rich_text", [])
            ext = ext_list[0].get("plain_text", "") if ext_list else ""
            
            size_mb = props.get("File Size", {}).get("number", 0) or 0
            parents = [p["id"].replace("-", "") for p in props.get("Parent Folder", {}).get("relation", [])]
            parent_id = parents[0] if parents else None

            desc_list = props.get("Description", {}).get("rich_text", [])
            desc = desc_list[0].get("plain_text", "") if desc_list else ""
            local_p = desc.replace("Path: ", "").replace("Local: ", "").replace(" (Updated)", "").replace(" (Modified)", "").strip()

            created_iso = it.get("created_time", "")
            edited_iso = it.get("last_edited_time", "")
            mtime = 0
            ctime = 0
            size_bytes = int(size_mb * 1024 * 1024)

            if local_p:
                if "This PC\\OnePlus Nord CE4" in local_p or local_p.startswith("/storage") or local_p.startswith("/sdcard"):
                    # Mobile file in state
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
                "type": item_type,
                "extension": ext,
                "size_mb": size_mb,
                "size_bytes": size_bytes,
                "mtime": mtime,
                "ctime": ctime,
                "created_time": created_iso,
                "last_edited_time": edited_iso,
                "parent_id": parent_id,
                "local_path": local_p
            }

            if parent_id:
                children_map.setdefault(parent_id, []).append(it_id)
            else:
                root_items.append(it_id)

        save_sync_state(state)

        c_disk_id = None
        for it_id, it in cached_items.items():
            if it["name"] == "Local Disk (C:)":
                c_disk_id = it_id
                break

        if c_disk_id:
            for it_id in list(root_items):
                it = cached_items.get(it_id, {})
                if it.get("name") in ("Users", "Default", "nitro", "TEMP", "TEMP.SHISHIR0X") and it_id != c_disk_id:
                    if it_id in root_items:
                        root_items.remove(it_id)
                    cached_items[it_id]["parent_id"] = c_disk_id
                    children_map.setdefault(c_disk_id, []).append(it_id)

        with CACHE_LOCK:
            DRIVE_CACHE["items"] = cached_items
            DRIVE_CACHE["children"] = children_map
            DRIVE_CACHE["root_items"] = root_items
            DRIVE_CACHE["version"] = DRIVE_CACHE.get("version", 0) + 1

        enrich_cache_items()
        save_disk_cache()
        print(f"[+] Notion cache refreshed: {len(cached_items)} items.")
    except Exception as e:
        print(f"[!] Notion sync error: {e}")

# ==============================================================================
# HTML & JS FRONTEND TEMPLATE
# ==============================================================================
DRIVE_GUI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>My Drive - Notion Cloud Manager</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {
            --bg-main: #131314;
            --bg-sidebar: #1E1F20;
            --bg-card: #28292A;
            --bg-card-hover: #333537;
            --bg-selected: #004A77;
            --text-main: #E3E3E3;
            --text-muted: #9E9E9E;
            --accent-blue: #A8C7FA;
            --accent-primary: #1A73E8;
            --accent-green: #34A853;
            --accent-orange: #FBBC04;
            --accent-red: #EA4335;
            --border-color: #3C4043;
            --item-radius: 12px;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Google Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }
        body { background-color: var(--bg-main); color: var(--text-main); display: flex; height: 100vh; overflow: hidden; }

        /* Sidebar */
        .sidebar {
            width: 256px;
            background-color: var(--bg-sidebar);
            display: flex;
            flex-direction: column;
            border-right: 1px solid var(--border-color);
            padding: 16px 12px;
            flex-shrink: 0;
        }

        .logo {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 8px 12px 24px;
            font-size: 18px;
            font-weight: 600;
            color: var(--text-main);
        }
        .logo i { color: #4285F4; font-size: 24px; }

        .nav-section { display: flex; flex-direction: column; gap: 4px; flex: 1; }
        .nav-item {
            display: flex;
            align-items: center;
            gap: 14px;
            padding: 10px 16px;
            border-radius: 24px;
            font-size: 14px;
            font-weight: 500;
            color: var(--text-main);
            text-decoration: none;
            cursor: pointer;
            transition: background 0.15s;
            position: relative;
        }
        .nav-item:hover { background-color: var(--bg-card-hover); }
        .nav-item.active { background-color: var(--bg-selected); color: var(--accent-blue); }
        .nav-item i { font-size: 16px; width: 20px; text-align: center; }

        .sync-badge {
            margin-left: auto;
            font-size: 11px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 10px;
            text-transform: uppercase;
        }
        .sync-badge.idle { background: rgba(255,255,255,0.08); color: var(--text-muted); }
        .sync-badge.running { background: #004A77; color: var(--accent-blue); animation: pulse 1.5s infinite; }

        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.6; }
            100% { opacity: 1; }
        }

        .storage-card {
            background-color: var(--bg-card);
            border-radius: var(--item-radius);
            padding: 16px;
            margin-top: auto;
            border: 1px solid var(--border-color);
        }
        .storage-title { font-size: 13px; color: var(--text-muted); margin-bottom: 8px; display: flex; justify-content: space-between; }
        .storage-bar-bg { background: #3C4043; height: 6px; border-radius: 3px; overflow: hidden; margin-bottom: 8px; }
        .storage-bar-fill { background: var(--accent-blue); height: 100%; width: 100%; border-radius: 3px; transition: width 0.3s; }
        .storage-text { font-size: 12px; color: var(--text-main); }

        /* Main Container */
        .main-container {
            flex: 1;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        /* Top Header */
        .topbar {
            height: 64px;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 24px;
            gap: 20px;
        }

        .search-box {
            flex: 1;
            max-width: 680px;
            background-color: var(--bg-card);
            border-radius: 28px;
            display: flex;
            align-items: center;
            padding: 8px 18px;
            gap: 12px;
            border: 1px solid transparent;
            transition: all 0.2s;
        }
        .search-box:focus-within {
            background-color: #1E1F20;
            border-color: var(--accent-blue);
            box-shadow: 0 1px 3px rgba(0,0,0,0.4);
        }
        .search-box input {
            background: transparent;
            border: none;
            outline: none;
            color: var(--text-main);
            font-size: 15px;
            width: 100%;
        }
        .search-box i { color: var(--text-muted); }

        .top-actions { display: flex; align-items: center; gap: 10px; }
        .btn-sync {
            background-color: var(--accent-primary);
            color: white;
            padding: 8px 16px;
            border-radius: 20px;
            border: none;
            font-size: 13px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
            transition: opacity 0.2s;
        }
        .btn-sync:hover { opacity: 0.9; }

        /* Breadcrumb & Toolbar */
        .content-header {
            padding: 14px 24px 10px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            flex-wrap: wrap;
        }
        .breadcrumbs {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 16px;
            font-weight: 500;
            flex: 1;
        }
        .breadcrumb-item { color: var(--text-muted); cursor: pointer; display: flex; align-items: center; gap: 6px; }
        .breadcrumb-item:hover { color: var(--text-main); }
        .breadcrumb-item.active { color: var(--text-main); font-weight: 600; cursor: default; }
        .breadcrumb-sep { color: var(--text-muted); font-size: 11px; }

        .toolbar-controls { display: flex; align-items: center; gap: 12px; }
        .control-pill {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 4px 12px;
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 13px;
            color: var(--text-main);
        }
        .control-pill select {
            background: transparent;
            border: none;
            color: var(--text-main);
            font-size: 13px;
            outline: none;
            cursor: pointer;
        }
        .control-pill select option { background: #1E1F20; color: #E3E3E3; }
        
        .btn-tool {
            background: transparent;
            border: none;
            color: var(--text-muted);
            cursor: pointer;
            font-size: 14px;
            padding: 4px;
            border-radius: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .btn-tool:hover { color: var(--text-main); background: rgba(255,255,255,0.08); }

        .view-switcher {
            display: flex;
            background: var(--bg-card);
            border-radius: 20px;
            border: 1px solid var(--border-color);
            overflow: hidden;
        }
        .view-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 6px 12px;
            cursor: pointer;
            font-size: 13px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .view-btn.active { background: var(--bg-selected); color: var(--accent-blue); }

        /* Workspaces */
        .workspace-scroll {
            flex: 1;
            overflow-y: auto;
            padding: 16px 24px;
        }

        .section-label {
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
            margin: 16px 0 10px;
        }

        .grid-view {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 14px;
        }

        .folder-card {
            background-color: var(--bg-card);
            border-radius: var(--item-radius);
            padding: 14px 16px;
            display: flex;
            align-items: center;
            gap: 12px;
            cursor: pointer;
            border: 1px solid var(--border-color);
            transition: all 0.15s;
            user-select: none;
        }
        .folder-card:hover {
            background-color: var(--bg-card-hover);
            border-color: #5F6368;
            transform: translateY(-1px);
        }
        .folder-card i { font-size: 20px; color: var(--accent-blue); flex-shrink: 0; }
        .folder-card .title {
            font-size: 14px;
            font-weight: 500;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .folder-card .count { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

        .file-card {
            background-color: var(--bg-card);
            border-radius: var(--item-radius);
            padding: 12px;
            border: 1px solid var(--border-color);
            cursor: pointer;
            transition: all 0.15s;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .file-card:hover {
            background-color: var(--bg-card-hover);
            border-color: #5F6368;
            transform: translateY(-1px);
        }
        .file-thumb {
            height: 110px;
            background: #1E1F20;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 36px;
            color: var(--text-muted);
            overflow: hidden;
        }
        .file-thumb img { width: 100%; height: 100%; object-fit: cover; }
        .file-info { display: flex; align-items: center; gap: 8px; }
        .file-info i { font-size: 16px; color: var(--accent-blue); }
        .file-name {
            font-size: 13px;
            font-weight: 500;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            flex: 1;
        }
        .file-meta { font-size: 11px; color: var(--text-muted); display: flex; justify-content: space-between; }

        /* Table List View */
        .drive-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }
        .drive-table th {
            padding: 10px 14px;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border-color);
            font-weight: 500;
            font-size: 12px;
            user-select: none;
            cursor: pointer;
        }
        .drive-table th:hover { color: var(--text-main); }
        .drive-table td {
            padding: 12px 14px;
            border-bottom: 1px solid rgba(255,255,255,0.04);
            color: var(--text-main);
        }
        .drive-table tr:hover td { background-color: var(--bg-card-hover); }
        .drive-table tr { cursor: pointer; }

        .table-item-name { display: flex; align-items: center; gap: 12px; font-weight: 500; }
        .table-item-name i { font-size: 16px; color: var(--accent-blue); width: 18px; text-align: center; }

        /* Modal Preview */
        .modal-overlay {
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.75);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            backdrop-filter: blur(4px);
        }
        .modal-content {
            background: var(--bg-sidebar);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            width: 90%;
            max-width: 900px;
            max-height: 88vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            box-shadow: 0 10px 30px rgba(0,0,0,0.6);
        }
        .modal-header {
            padding: 14px 20px;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .modal-body {
            padding: 16px;
            flex: 1;
            overflow: auto;
            display: flex;
            align-items: center;
            justify-content: center;
            background: #111;
        }
        .action-btn {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            color: var(--text-main);
            padding: 6px 12px;
            border-radius: 16px;
            cursor: pointer;
            font-size: 12px;
            display: flex;
            align-items: center;
            gap: 6px;
            text-decoration: none;
        }
        .action-btn:hover { background: var(--bg-card-hover); color: var(--accent-blue); }

        /* ===================================================================== */
        /* SYNC CENTER STYLES                                                    */
        /* ===================================================================== */
        .sync-hero-card {
            background: linear-gradient(135deg, #1E1F20 0%, #28292A 100%);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        }

        .sync-header-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 18px;
            flex-wrap: wrap;
            gap: 12px;
        }

        .sync-title-area { display: flex; align-items: center; gap: 12px; }
        .sync-pulse-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: var(--accent-green);
            box-shadow: 0 0 10px var(--accent-green);
        }
        .sync-pulse-dot.running {
            background: var(--accent-blue);
            box-shadow: 0 0 12px var(--accent-blue);
            animation: pulse 1s infinite;
        }

        .sync-controls-row {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }
        .btn-sync-action {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            color: var(--text-main);
            padding: 8px 16px;
            border-radius: 20px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 8px;
            transition: all 0.2s;
        }
        .btn-sync-action:hover {
            background: var(--bg-card-hover);
            border-color: var(--accent-blue);
            color: var(--accent-blue);
        }
        .btn-sync-action.primary {
            background: var(--accent-primary);
            border-color: var(--accent-primary);
            color: white;
        }
        .btn-sync-action.primary:hover { opacity: 0.9; }
        .btn-sync-action.danger {
            background: rgba(234,67,53,0.15);
            border-color: rgba(234,67,53,0.3);
            color: #F28B82;
        }
        .btn-sync-action.danger:hover { background: rgba(234,67,53,0.3); }

        .progress-container { margin-bottom: 20px; }
        .progress-labels {
            display: flex;
            justify-content: space-between;
            font-size: 13px;
            margin-bottom: 8px;
            font-weight: 500;
        }
        .progress-bar-outer {
            background: rgba(255,255,255,0.08);
            height: 10px;
            border-radius: 5px;
            overflow: hidden;
        }
        .progress-bar-inner {
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg, #1A73E8, #34A853);
            border-radius: 5px;
            transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 14px;
            margin-bottom: 20px;
        }
        .stat-box {
            background: #1E1F20;
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 14px 16px;
        }
        .stat-label { font-size: 12px; color: var(--text-muted); margin-bottom: 4px; }
        .stat-value { font-size: 20px; font-weight: 600; color: var(--text-main); }

        .active-file-card {
            background: rgba(0, 74, 119, 0.25);
            border: 1px solid rgba(168, 199, 250, 0.3);
            border-radius: 12px;
            padding: 16px 20px;
            display: flex;
            align-items: center;
            gap: 16px;
            margin-bottom: 24px;
        }
        .active-file-card i { font-size: 24px; color: var(--accent-blue); }
        .active-file-details { flex: 1; }
        .active-file-name { font-size: 14px; font-weight: 600; margin-bottom: 2px; }
        .active-file-path { font-size: 12px; color: var(--text-muted); word-break: break-all; }

        .sync-tabs-header {
            display: flex;
            gap: 8px;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 16px;
        }
        .sync-tab-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 10px 16px;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            border-bottom: 2px solid transparent;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .sync-tab-btn.active {
            color: var(--accent-blue);
            border-bottom-color: var(--accent-blue);
        }
        .tab-counter {
            background: rgba(255,255,255,0.1);
            padding: 1px 7px;
            border-radius: 10px;
            font-size: 11px;
        }

        .sync-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        .sync-table th {
            padding: 10px 14px;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border-color);
            font-size: 12px;
            text-align: left;
        }
        .sync-table td {
            padding: 12px 14px;
            border-bottom: 1px solid rgba(255,255,255,0.04);
        }

        .status-pill {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
        }
        .status-pill.queued { background: rgba(255,255,255,0.08); color: var(--text-muted); }
        .status-pill.uploading { background: #004A77; color: var(--accent-blue); animation: pulse 1.2s infinite; }
        .status-pill.synced { background: rgba(52,168,83,0.15); color: #81C995; }
        .status-pill.failed { background: rgba(234,67,53,0.15); color: #F28B82; }

        .tag-pill {
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            margin-right: 6px;
        }
        .tag-pill.NEW { background: rgba(52,168,83,0.2); color: #81C995; }
        .tag-pill.MODIFIED { background: rgba(251,188,4,0.2); color: #FDD663; }

        .console-logs {
            background: #111;
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 14px;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 12px;
            color: #A8C7FA;
            height: 320px;
            overflow-y: auto;
            line-height: 1.6;
        }
        .log-entry { margin-bottom: 2px; }
    </style>
</head>
<body>
    <!-- Left Navigation Sidebar -->
    <div class="sidebar">
        <div class="logo">
            <i class="fa-brands fa-google-drive"></i>
            <span>Notion Drive</span>
        </div>

        <div class="nav-section">
            <div class="nav-item active" id="navItemDrive" onclick="switchMainTab('drive')">
                <i class="fa-solid fa-folder"></i>
                <span>My Drive</span>
            </div>
            <div class="nav-item" id="navItemSync" onclick="switchMainTab('sync')">
                <i class="fa-solid fa-arrows-rotate" id="syncNavIcon"></i>
                <span>Sync Activity</span>
                <span class="sync-badge idle" id="syncNavBadge">Idle</span>
            </div>
            <div class="nav-item" onclick="openNotionWeb()">
                <i class="fa-solid fa-arrow-up-right-from-square"></i>
                <span>Open in Notion</span>
            </div>
        </div>

        <div class="storage-card">
            <div class="storage-title">
                <span>Storage</span>
                <span style="background: rgba(168,199,250,0.15); color: #A8C7FA; padding: 2px 8px; border-radius: 12px; font-weight: 600; font-size: 11px;"><i class="fa-solid fa-infinity"></i> Unlimited</span>
            </div>
            <div class="storage-bar-bg" style="background: rgba(255,255,255,0.08);">
                <div class="storage-bar-fill" style="width: 100%; background: linear-gradient(90deg, #1A73E8, #34A853);"></div>
            </div>
            <div class="storage-text" id="storage-detail" style="font-weight: 500; font-size: 12px;">Loading storage...</div>
            <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;"><i class="fa-solid fa-graduation-cap"></i> Student / Plus Unlimited Plan</div>
        </div>
    </div>

    <!-- Main Workspace Container -->
    <div class="main-container">
        <!-- Top Search Bar -->
        <div class="topbar">
            <div class="search-box">
                <i class="fa-solid fa-magnifying-glass"></i>
                <input type="text" id="searchInput" placeholder="Search files and folders in My Drive..." oninput="handleSearch()">
            </div>
            <div class="top-actions">
                <button class="btn-sync" onclick="refreshDrive()">
                    <i class="fa-solid fa-arrows-rotate"></i>
                    <span>Sync Notion</span>
                </button>
            </div>
        </div>

        <!-- 1. MY DRIVE VIEW -->
        <div id="viewMyDrive" style="display: flex; flex-direction: column; flex: 1; overflow: hidden;">
            <div class="content-header">
                <div class="breadcrumbs" id="breadcrumbContainer">
                    <span class="breadcrumb-item active" onclick="loadDriveRoot()">
                        <i class="fa-solid fa-hard-drive"></i> My Drive
                    </span>
                </div>

                <div class="toolbar-controls">
                    <div class="control-pill">
                        <span>Sort:</span>
                        <select id="sortSelect" onchange="changeSort(this.value)">
                            <option value="name">Name</option>
                            <option value="mtime">Last modified</option>
                            <option value="ctime">Date created</option>
                            <option value="size_bytes">File size</option>
                            <option value="type">File type</option>
                        </select>
                        <button class="btn-tool" id="btnSortDir" onclick="toggleSortDir()" title="Reverse sort direction">
                            <i class="fa-solid fa-arrow-down-short-wide" id="sortDirIcon"></i>
                        </button>
                    </div>

                    <div class="view-switcher">
                        <button class="view-btn active" id="btnViewGrid" onclick="setViewMode('grid')" title="Grid view">
                            <i class="fa-solid fa-table-cells-large"></i>
                        </button>
                        <button class="view-btn" id="btnViewList" onclick="setViewMode('list')" title="List view">
                            <i class="fa-solid fa-list-ul"></i>
                        </button>
                    </div>
                </div>
            </div>

            <div class="workspace-scroll" id="workspaceContainer">
                <div id="gridViewWrapper">
                    <div id="foldersSection">
                        <div class="section-label">Folders</div>
                        <div class="grid-view" id="foldersGrid"></div>
                    </div>
                    <div id="filesSection">
                        <div class="section-label">Files</div>
                        <div class="grid-view" id="filesGrid"></div>
                    </div>
                </div>

                <div id="listViewWrapper" style="display: none;">
                    <table class="drive-table">
                        <thead>
                            <tr>
                                <th onclick="tableHeaderSort('name')">Name <i id="th-icon-name" class="fa-solid fa-sort"></i></th>
                                <th onclick="tableHeaderSort('type')">Type <i id="th-icon-type" class="fa-solid fa-sort"></i></th>
                                <th onclick="tableHeaderSort('mtime')">Last modified <i id="th-icon-mtime" class="fa-solid fa-sort"></i></th>
                                <th onclick="tableHeaderSort('size_bytes')">File size <i id="th-icon-size_bytes" class="fa-solid fa-sort"></i></th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody id="driveTableBody"></tbody>
                    </table>
                </div>

                <div id="emptyMessage" style="display:none; color: var(--text-muted); padding: 48px 0; text-align: center; font-size: 15px;">
                    <i class="fa-regular fa-folder-open" style="font-size: 36px; margin-bottom: 12px; display: block; opacity: 0.6;"></i>
                    This folder is empty.
                </div>
            </div>
        </div>

        <!-- 2. SYNC CENTER VIEW -->
        <div id="viewSyncCenter" style="display: none; flex-direction: column; flex: 1; overflow-y: auto; padding: 24px;">
            <!-- Hero Sync Card -->
            <div class="sync-hero-card">
                <div class="sync-header-row">
                    <div class="sync-title-area">
                        <div class="sync-pulse-dot" id="syncPulseDot"></div>
                        <div>
                            <h2 style="font-size: 18px; font-weight: 600;" id="syncMainStatus">Git-Style Differential Sync</h2>
                            <div style="font-size: 12px; color: var(--text-muted);" id="syncSubStatus">Tracks .notion_sync_state.json • Skips unchanged files automatically</div>
                        </div>
                    </div>

                    <div class="sync-controls-row">
                        <button class="btn-sync-action primary" onclick="startSync('all')"><i class="fa-solid fa-bolt"></i> Sync All</button>
                        <button class="btn-sync-action" onclick="startSync('c')"><i class="fa-solid fa-hard-drive"></i> PC (C:)</button>
                        <button class="btn-sync-action" onclick="startSync('d')"><i class="fa-solid fa-hard-drive"></i> PC (D:)</button>
                        <button class="btn-sync-action" onclick="startSync('phone')"><i class="fa-solid fa-mobile-screen"></i> Phone</button>
                        <button class="btn-sync-action" onclick="startSync('sdcard')"><i class="fa-solid fa-sd-card"></i> SD Card</button>
                        <button class="btn-sync-action danger" onclick="cancelSync()"><i class="fa-solid fa-stop"></i> Stop</button>
                    </div>
                </div>

                <!-- Progress Bar -->
                <div class="progress-container">
                    <div class="progress-labels">
                        <span id="syncProgressLabel">Progress: 0%</span>
                        <span id="syncStatsDetail">0 / 0 files</span>
                    </div>
                    <div class="progress-bar-outer">
                        <div class="progress-bar-inner" id="syncProgressBar"></div>
                    </div>
                </div>

                <!-- Live Metrics Grid -->
                <div class="stats-grid">
                    <div class="stat-box">
                        <div class="stat-label">Target Device</div>
                        <div class="stat-value" id="statTarget" style="font-size: 16px; color: var(--accent-blue);">-</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-label">Uploaded</div>
                        <div class="stat-value" id="statUploaded">0</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-label">Remaining Changes</div>
                        <div class="stat-value" id="statRemaining">0</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-label">Sync Speed</div>
                        <div class="stat-value" id="statSpeed" style="font-size: 16px;">-</div>
                    </div>
                </div>

                <!-- Active Uploading File Spotlight -->
                <div class="active-file-card" id="activeFileBox">
                    <i class="fa-solid fa-cloud-arrow-up fa-fade"></i>
                    <div class="active-file-details">
                        <div class="active-file-name" id="activeFileName">Waiting for sync to start...</div>
                        <div class="active-file-path" id="activeFilePath">Persistent state loaded. Click a sync button above to calculate differential changes.</div>
                    </div>
                    <div id="activeFileSize" style="font-weight: 600; font-size: 13px; color: var(--accent-blue);">-</div>
                </div>
            </div>

            <!-- Sub Tabs: Live Queue, History, Logs -->
            <div class="sync-tabs-header">
                <button class="sync-tab-btn active" id="tabBtnQueue" onclick="switchSyncSubTab('queue')">
                    <i class="fa-solid fa-list-check"></i> Live Queue <span class="tab-counter" id="badgeQueueCount">0</span>
                </button>
                <button class="sync-tab-btn" id="tabBtnHistory" onclick="switchSyncSubTab('history')">
                    <i class="fa-solid fa-clock-rotate-left"></i> Completed History <span class="tab-counter" id="badgeHistoryCount">0</span>
                </button>
                <button class="sync-tab-btn" id="tabBtnLogs" onclick="switchSyncSubTab('logs')">
                    <i class="fa-solid fa-terminal"></i> Console Logs
                </button>
            </div>

            <!-- 1. Live Queue Table (Dynamic Sliding Window) -->
            <div id="syncSubViewQueue">
                <table class="sync-table">
                    <thead>
                        <tr>
                            <th>Change</th>
                            <th>File Name</th>
                            <th>Target Cloud Path</th>
                            <th>Size</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody id="syncQueueTableBody">
                        <tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 24px;">No files currently in queue.</td></tr>
                    </tbody>
                </table>
            </div>

            <!-- 2. History Table -->
            <div id="syncSubViewHistory" style="display: none;">
                <table class="sync-table">
                    <thead>
                        <tr>
                            <th>File Name</th>
                            <th>Cloud Destination</th>
                            <th>Size</th>
                            <th>Time</th>
                            <th>Result</th>
                        </tr>
                    </thead>
                    <tbody id="syncHistoryTableBody">
                        <tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 24px;">No upload history yet.</td></tr>
                    </tbody>
                </table>
            </div>

            <!-- 3. Console Logs -->
            <div id="syncSubViewLogs" style="display: none;">
                <div class="console-logs" id="consoleLogsBox"></div>
            </div>
        </div>
    </div>

    <!-- Preview Modal -->
    <div class="modal-overlay" id="previewModal" onclick="closeModal(event)">
        <div class="modal-content" onclick="event.stopPropagation()">
            <div class="modal-header">
                <span style="font-weight: 600;" id="modalTitle">File Preview</span>
                <div style="display: flex; gap: 10px;">
                    <a id="modalOpenTabBtn" class="action-btn" title="Open in New Tab" target="_blank"><i class="fa-solid fa-arrow-up-right-from-square"></i></a>
                    <a id="modalDownloadBtn" class="action-btn" title="Download File"><i class="fa-solid fa-download"></i></a>
                    <button class="action-btn" onclick="closeModal(event)"><i class="fa-solid fa-xmark"></i></button>
                </div>
            </div>
            <div class="modal-body" id="modalBody"></div>
        </div>
    </div>

    <script>
        let currentFolderId = null;
        let driveData = { folders: [], files: [], breadcrumbs: [] };
        let viewMode = 'grid';
        let sortKey = 'name';
        let sortDir = 1;
        let currentMainTab = 'drive';
        let currentSyncSubTab = 'queue';
        let lastCacheVersion = -1;

        const fileIcons = {
            'pdf': 'fa-file-pdf', 'doc': 'fa-file-word', 'docx': 'fa-file-word',
            'xls': 'fa-file-excel', 'xlsx': 'fa-file-excel', 'csv': 'fa-file-excel',
            'ppt': 'fa-file-powerpoint', 'pptx': 'fa-file-powerpoint',
            'jpg': 'fa-file-image', 'jpeg': 'fa-file-image', 'png': 'fa-file-image', 'webp': 'fa-file-image', 'svg': 'fa-file-image',
            'mp4': 'fa-file-video', 'mkv': 'fa-file-video', 'mp3': 'fa-file-audio', 'm4a': 'fa-file-audio', 'opus': 'fa-file-audio',
            'zip': 'fa-file-zipper', 'rar': 'fa-file-zipper', '7z': 'fa-file-zipper',
            'py': 'fa-file-code', 'js': 'fa-file-code', 'ts': 'fa-file-code', 'html': 'fa-file-code', 'css': 'fa-file-code', 'json': 'fa-file-code'
        };

        function getFolderIcon(name) {
            if (name.includes('Local Disk (C:)') || name.includes('Local Disk (D:)')) return 'fa-solid fa-hard-drive';
            if (name.includes('Internal shared storage') || name.includes('Internal Storage')) return 'fa-solid fa-mobile-screen-button';
            if (name.includes('SD card') || name.includes('SD Card')) return 'fa-solid fa-sd-card';
            return 'fa-solid fa-folder';
        }

        function switchMainTab(tab) {
            currentMainTab = tab;
            document.getElementById('navItemDrive').classList.toggle('active', tab === 'drive');
            document.getElementById('navItemSync').classList.toggle('active', tab === 'sync');
            
            document.getElementById('viewMyDrive').style.display = tab === 'drive' ? 'flex' : 'none';
            document.getElementById('viewSyncCenter').style.display = tab === 'sync' ? 'flex' : 'none';

            if (tab === 'drive') {
                fetchDrive(currentFolderId);
            } else if (tab === 'sync') {
                pollSyncStatus();
            }
        }

        function switchSyncSubTab(subTab) {
            currentSyncSubTab = subTab;
            document.getElementById('tabBtnQueue').classList.toggle('active', subTab === 'queue');
            document.getElementById('tabBtnHistory').classList.toggle('active', subTab === 'history');
            document.getElementById('tabBtnLogs').classList.toggle('active', subTab === 'logs');

            document.getElementById('syncSubViewQueue').style.display = subTab === 'queue' ? 'block' : 'none';
            document.getElementById('syncSubViewHistory').style.display = subTab === 'history' ? 'block' : 'none';
            document.getElementById('syncSubViewLogs').style.display = subTab === 'logs' ? 'block' : 'none';
        }

        async function fetchDrive(folderId = null) {
            currentFolderId = folderId;
            const url = folderId ? `/api/drive?folder_id=${encodeURIComponent(folderId)}` : '/api/drive';
            try {
                const res = await fetch(url);
                driveData = await res.json();
                sortItems();
                renderView();
                renderBreadcrumbs();
            } catch (e) {
                console.error("Drive fetch error:", e);
            }
        }

        function formatBytes(bytes) {
            if (!bytes || bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
        }

        function formatDate(timestamp) {
            if (!timestamp || timestamp === 0) return '-';
            const d = new Date(timestamp * 1000);
            return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
        }

        function changeSort(val) {
            sortKey = val;
            sortItems();
            renderView();
        }

        function toggleSortDir() {
            sortDir *= -1;
            document.getElementById('sortDirIcon').className = sortDir === 1 ? 'fa-solid fa-arrow-down-short-wide' : 'fa-solid fa-arrow-up-wide-short';
            sortItems();
            renderView();
        }

        function tableHeaderSort(key) {
            if (sortKey === key) {
                toggleSortDir();
            } else {
                sortKey = key;
                sortDir = 1;
                document.getElementById('sortSelect').value = key;
                sortItems();
                renderView();
            }
        }

        function sortItems() {
            const cmp = (a, b) => {
                let va = a[sortKey];
                let vb = b[sortKey];
                if (typeof va === 'string') va = va.toLowerCase();
                if (typeof vb === 'string') vb = vb.toLowerCase();
                if (va < vb) return -1 * sortDir;
                if (va > vb) return 1 * sortDir;
                return 0;
            };
            if (driveData.folders) driveData.folders.sort(cmp);
            if (driveData.files) driveData.files.sort(cmp);
        }

        function setViewMode(mode) {
            viewMode = mode;
            document.getElementById('btnViewGrid').classList.toggle('active', mode === 'grid');
            document.getElementById('btnViewList').classList.toggle('active', mode === 'list');
            document.getElementById('gridViewWrapper').style.display = mode === 'grid' ? 'block' : 'none';
            document.getElementById('listViewWrapper').style.display = mode === 'list' ? 'block' : 'none';
            renderView();
        }

        function renderView() {
            const isEmpty = (!driveData.folders || driveData.folders.length === 0) && (!driveData.files || driveData.files.length === 0);
            document.getElementById('emptyMessage').style.display = isEmpty ? 'block' : 'none';

            if (viewMode === 'grid') {
                const fg = document.getElementById('foldersGrid');
                const fs = document.getElementById('filesGrid');
                fg.innerHTML = '';
                fs.innerHTML = '';

                document.getElementById('foldersSection').style.display = driveData.folders.length ? 'block' : 'none';
                document.getElementById('filesSection').style.display = driveData.files.length ? 'block' : 'none';

                driveData.folders.forEach(f => {
                    const countTxt = f.item_count !== undefined ? `${f.item_count} items` : '';
                    fg.innerHTML += `
                        <div class="folder-card" onclick="fetchDrive('${f.id}')" title="${f.name}">
                            <i class="${getFolderIcon(f.name)}"></i>
                            <div style="overflow:hidden;">
                                <div class="title">${f.name}</div>
                                <div class="count">${countTxt}</div>
                            </div>
                        </div>
                    `;
                });

                driveData.files.forEach(f => {
                    const ext = (f.extension || '').replace('.', '').toLowerCase();
                    const iconClass = fileIcons[ext] || 'fa-file';
                    const isImg = ['jpg','jpeg','png','webp','gif','svg'].includes(ext);
                    const thumb = isImg 
                        ? `<img src="/view?path=${encodeURIComponent(f.local_path)}" loading="lazy">` 
                        : `<i class="fa-solid ${iconClass}"></i>`;

                    fs.innerHTML += `
                        <div class="file-card" onclick='previewFile(${JSON.stringify(f)})' title="${f.name}">
                            <div class="file-thumb">${thumb}</div>
                            <div class="file-info">
                                <i class="fa-solid ${iconClass}"></i>
                                <div class="file-name">${f.name}</div>
                            </div>
                            <div class="file-meta">
                                <span>${formatBytes(f.size_bytes)}</span>
                                <span>${f.mtime ? formatDate(f.mtime).split(',')[0] : '-'}</span>
                            </div>
                        </div>
                    `;
                });
            } else {
                const tbody = document.getElementById('driveTableBody');
                tbody.innerHTML = '';

                driveData.folders.forEach(f => {
                    tbody.innerHTML += `
                        <tr onclick="fetchDrive('${f.id}')">
                            <td><div class="table-item-name"><i class="${getFolderIcon(f.name)}"></i><span>${f.name}</span></div></td>
                            <td>Folder</td>
                            <td>${formatDate(f.mtime)}</td>
                            <td>-</td>
                            <td><a class="action-btn" href="/download-folder?id=${f.id}" onclick="event.stopPropagation()"><i class="fa-solid fa-download"></i> ZIP</a></td>
                        </tr>
                    `;
                });

                driveData.files.forEach(f => {
                    const ext = (f.extension || '').replace('.', '').toLowerCase();
                    const iconClass = fileIcons[ext] || 'fa-file';
                    tbody.innerHTML += `
                        <tr onclick='previewFile(${JSON.stringify(f)})'>
                            <td><div class="table-item-name"><i class="fa-solid ${iconClass}"></i><span>${f.name}</span></div></td>
                            <td>${f.type || ext.toUpperCase() || 'File'}</td>
                            <td>${formatDate(f.mtime)}</td>
                            <td>${formatBytes(f.size_bytes)}</td>
                            <td>
                                <div style="display:flex; gap:6px;">
                                    <a class="action-btn" href="/view?path=${encodeURIComponent(f.local_path)}" target="_blank" onclick="event.stopPropagation()"><i class="fa-solid fa-arrow-up-right-from-square"></i></a>
                                    <a class="action-btn" href="/download?path=${encodeURIComponent(f.local_path)}" onclick="event.stopPropagation()"><i class="fa-solid fa-download"></i></a>
                                </div>
                            </td>
                        </tr>
                    `;
                });
            }
        }

        function renderBreadcrumbs() {
            const bc = document.getElementById('breadcrumbContainer');
            bc.innerHTML = `
                <span class="breadcrumb-item ${!currentFolderId ? 'active' : ''}" onclick="loadDriveRoot()">
                    <i class="fa-solid fa-hard-drive"></i> My Drive
                </span>
            `;

            if (driveData.breadcrumbs && driveData.breadcrumbs.length) {
                driveData.breadcrumbs.forEach((b, idx) => {
                    const isLast = idx === driveData.breadcrumbs.length - 1;
                    bc.innerHTML += `
                        <span class="breadcrumb-sep"><i class="fa-solid fa-chevron-right"></i></span>
                        <span class="breadcrumb-item ${isLast ? 'active' : ''}" onclick="fetchDrive('${b.id}')">${b.name}</span>
                    `;
                });
            }
        }

        function previewFile(f) {
            const modal = document.getElementById('previewModal');
            const body = document.getElementById('modalBody');
            const title = document.getElementById('modalTitle');
            const dlBtn = document.getElementById('modalDownloadBtn');
            const tabBtn = document.getElementById('modalOpenTabBtn');

            title.innerText = f.name;
            dlBtn.href = `/download?path=${encodeURIComponent(f.local_path)}`;
            tabBtn.href = `/view?path=${encodeURIComponent(f.local_path)}`;
            body.innerHTML = '';

            const ext = (f.extension || '').toLowerCase();
            const viewUrl = `/view?path=${encodeURIComponent(f.local_path)}`;

            if (ext === '.pdf') {
                body.innerHTML = `<iframe src="${viewUrl}" style="width:100%; height:75vh; border:none; border-radius:8px;"></iframe>`;
            } else if (['.png', '.jpg', '.jpeg', '.webp', '.svg', '.gif', '.ico', '.bmp'].includes(ext)) {
                body.innerHTML = `<img src="${viewUrl}" alt="${f.name}" style="max-width:100%; max-height:75vh; object-fit:contain; border-radius:8px;">`;
            } else if (['.mp4', '.webm', '.mkv'].includes(ext)) {
                body.innerHTML = `<video controls autoplay src="${viewUrl}" style="max-width:100%; max-height:75vh; border-radius:8px;"></video>`;
            } else if (['.mp3', '.wav', '.ogg', '.m4a', '.opus'].includes(ext)) {
                body.innerHTML = `<div style="padding:40px 20px; text-align:center; width:100%;"><audio controls autoplay src="${viewUrl}" style="width:80%; max-width:500px;"></audio></div>`;
            } else {
                body.innerHTML = `<iframe src="${viewUrl}" style="width:100%; height:75vh; border:none; border-radius:8px; background:#1e1e1e;"></iframe>`;
            }

            modal.style.display = 'flex';
        }

        function closeModal(e) {
            document.getElementById('previewModal').style.display = 'none';
            document.getElementById('modalBody').innerHTML = '';
        }

        function loadDriveRoot() { fetchDrive(null); }
        function openNotionWeb() { window.open('https://app.notion.com/p/3bd3d81b2f368055902aeee41736ae89', '_blank'); }

        async function handleSearch() {
            const query = document.getElementById('searchInput').value.trim();
            if (!query) { fetchDrive(currentFolderId); return; }
            const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
            driveData = await res.json();
            sortItems();
            renderView();
        }

        async function refreshDrive() {
            const btn = document.querySelector('.btn-sync');
            btn.innerHTML = '<i class="fa-solid fa-arrows-rotate fa-spin"></i> Syncing...';
            await fetch('/api/refresh');
            await fetchDrive(currentFolderId);
            btn.innerHTML = '<i class="fa-solid fa-arrows-rotate"></i> Sync Notion';
        }

        async function fetchStorageStats() {
            try {
                const res = await fetch('/api/stats');
                const st = await res.json();
                const gb = (st.total_mb / 1024).toFixed(2);
                document.getElementById('storage-detail').innerText = `${gb} GB Used • ${st.total_files.toLocaleString()} files stored`;
            } catch (e) {
                console.error(e);
            }
        }

        // =====================================================================
        // SYNC CENTER LOGIC & REAL-TIME POLLING
        // =====================================================================
        async function startSync(target) {
            try {
                const res = await fetch(`/api/sync/start?target=${target}`, { method: 'POST' });
                const d = await res.json();
                switchMainTab('sync');
                pollSyncStatus();
            } catch (e) {
                alert("Error starting sync: " + e);
            }
        }

        async function cancelSync() {
            try {
                await fetch('/api/sync/cancel', { method: 'POST' });
            } catch (e) {
                console.error(e);
            }
        }

        async function pollSyncStatus() {
            try {
                const res = await fetch('/api/sync/status');
                const st = await res.json();

                if (st.cache_version !== undefined && st.cache_version !== lastCacheVersion) {
                    lastCacheVersion = st.cache_version;
                    if (currentMainTab === 'drive') {
                        fetchDrive(currentFolderId);
                    }
                    fetchStorageStats();
                }

                const badge = document.getElementById('syncNavBadge');
                const icon = document.getElementById('syncNavIcon');
                if (st.is_running) {
                    badge.className = 'sync-badge running';
                    badge.innerText = `${st.percent}%`;
                    icon.className = 'fa-solid fa-arrows-rotate fa-spin';
                    document.getElementById('syncPulseDot').className = 'sync-pulse-dot running';
                } else {
                    badge.className = 'sync-badge idle';
                    badge.innerText = 'Idle';
                    icon.className = 'fa-solid fa-arrows-rotate';
                    document.getElementById('syncPulseDot').className = 'sync-pulse-dot';
                }

                document.getElementById('syncMainStatus').innerText = st.is_running ? `Syncing ${st.current_target}...` : st.status_message;
                document.getElementById('syncSubStatus').innerText = st.is_running ? `Active: ${st.current_file}` : 'Tracks .notion_sync_state.json • Skips unchanged files automatically';
                document.getElementById('syncProgressLabel').innerText = `Progress: ${st.percent}%`;
                document.getElementById('syncStatsDetail').innerText = `${st.synced_files} / ${st.total_files} changes (${st.remaining_files} remaining)`;
                document.getElementById('syncProgressBar').style.width = `${st.percent}%`;

                document.getElementById('statTarget').innerText = st.current_target;
                document.getElementById('statUploaded').innerText = st.synced_files;
                document.getElementById('statRemaining').innerText = st.remaining_files;
                document.getElementById('statSpeed').innerText = st.speed_str;

                document.getElementById('badgeQueueCount').innerText = st.remaining_files || (st.queue ? st.queue.length : 0);
                document.getElementById('badgeHistoryCount').innerText = st.history ? st.history.length : 0;

                if (st.is_running && st.current_file !== 'None') {
                    document.getElementById('activeFileName').innerText = st.current_file;
                    document.getElementById('activeFilePath').innerText = st.current_path;
                    document.getElementById('activeFileSize').innerText = st.current_size_str;
                } else if (!st.is_running && st.total_files > 0) {
                    document.getElementById('activeFileName').innerText = "All changes synchronized!";
                    document.getElementById('activeFilePath').innerText = "Notion Cloud database is 100% up to date with persistent state.";
                    document.getElementById('activeFileSize').innerText = "✅ Complete";
                }

                const qBody = document.getElementById('syncQueueTableBody');
                if (st.queue && st.queue.length) {
                    qBody.innerHTML = '';
                    st.queue.forEach(q => {
                        const statusPill = `<span class="status-pill ${q.status}">${q.status}</span>`;
                        const tagPill = `<span class="tag-pill ${q.tag || 'NEW'}">${q.tag || 'NEW'}</span>`;
                        qBody.innerHTML += `
                            <tr>
                                <td>${tagPill}</td>
                                <td style="font-weight:500;">${q.name}</td>
                                <td style="color:var(--text-muted); font-size:12px;">${q.path}</td>
                                <td>${q.size_str}</td>
                                <td>${statusPill}</td>
                            </tr>
                        `;
                    });
                } else {
                    if (!st.is_running && st.synced_files > 0) {
                        qBody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: #81C995; padding: 24px; font-weight: 500;">
                            <i class="fa-solid fa-circle-check" style="font-size: 20px; display: block; margin-bottom: 6px;"></i>
                            All files have finished syncing! View completed items in the 'Completed History' tab.
                        </td></tr>`;
                    } else {
                        qBody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 24px;">No changes currently in queue. Click a sync button above to calculate differential changes.</td></tr>`;
                    }
                }

                const hBody = document.getElementById('syncHistoryTableBody');
                if (st.history && st.history.length) {
                    hBody.innerHTML = '';
                    st.history.forEach(h => {
                        const pill = h.status === 'success' ? '<span class="status-pill synced">Synced</span>' : '<span class="status-pill failed">Failed</span>';
                        hBody.innerHTML += `
                            <tr>
                                <td style="font-weight:500;">${h.name}</td>
                                <td style="color:var(--text-muted); font-size:12px;">${h.path}</td>
                                <td>${h.size_str}</td>
                                <td>${h.time}</td>
                                <td>${pill}</td>
                            </tr>
                        `;
                    });
                }

                const logBox = document.getElementById('consoleLogsBox');
                if (st.logs && st.logs.length) {
                    logBox.innerHTML = st.logs.map(l => `<div class="log-entry">${htmlEscape(l)}</div>`).join('');
                    logBox.scrollTop = logBox.scrollHeight;
                }
            } catch (e) {
                console.error("Poll sync error:", e);
            }
        }

        function htmlEscape(str) {
            return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        fetchDrive();
        fetchStorageStats();
        setInterval(fetchStorageStats, 15000);
        setInterval(pollSyncStatus, 1000);
    </script>
</body>
</html>"""

# ==============================================================================
# HTTP SERVER ROUTING
# ==============================================================================
class NotionServerHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/api/sync/start":
            target = params.get("target", ["all"])[0].lower()
            ok, msg = trigger_background_sync(target)
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": ok, "message": msg}).encode("utf-8"))
            return

        if parsed.path == "/api/sync/cancel":
            global CANCEL_SYNC_FLAG
            CANCEL_SYNC_FLAG = True
            add_sync_log("Sync cancellation requested by user.")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"success": true, "message": "Cancelled"}')
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DRIVE_GUI_HTML.encode("utf-8"))
            return

        if parsed.path == "/api/sync/status":
            with SYNC_LOCK:
                st_copy = dict(SYNC_STATE)
            with CACHE_LOCK:
                st_copy["cache_version"] = DRIVE_CACHE.get("version", 0)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(st_copy).encode("utf-8"))
            return

        if parsed.path == "/api/drive":
            folder_id = params.get("folder_id", [None])[0]
            if folder_id:
                folder_id = folder_id.replace("-", "")

            with CACHE_LOCK:
                if folder_id:
                    child_ids = list(DRIVE_CACHE["children"].get(folder_id, []))
                else:
                    ROOT_DEVICE_NAMES = {
                        "Local Disk (C:)", "Local Disk (D:)",
                        "Internal shared storage", "SD card",
                        "Internal Storage", "SD Card"
                    }
                    child_ids = [cid for cid, it in DRIVE_CACHE["items"].items() if it.get("name") in ROOT_DEVICE_NAMES and not it.get("parent_id")]
                    if not child_ids:
                        child_ids = list(DRIVE_CACHE["root_items"])

                folders = []
                files = []
                for cid in child_ids:
                    item = DRIVE_CACHE["items"].get(cid)
                    if not item:
                        continue
                    if item.get("type") == "Folder":
                        sub_count = len(DRIVE_CACHE["children"].get(cid, []))
                        item_copy = dict(item)
                        item_copy["item_count"] = sub_count
                        folders.append(item_copy)
                    else:
                        files.append(dict(item))

                breadcrumbs = []
                curr = folder_id
                while curr:
                    c_item = DRIVE_CACHE["items"].get(curr)
                    if not c_item:
                        break
                    breadcrumbs.insert(0, {"id": curr, "name": c_item["name"]})
                    curr = c_item.get("parent_id")

                resp_data = {
                    "folders": sorted(folders, key=lambda x: x["name"].lower()),
                    "files": sorted(files, key=lambda x: x["name"].lower()),
                    "breadcrumbs": breadcrumbs,
                    "version": DRIVE_CACHE.get("version", 0)
                }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(resp_data).encode("utf-8"))
            return

        if parsed.path == "/api/search":
            query = params.get("q", [""])[0].lower()
            matching_files = []
            matching_folders = []
            with CACHE_LOCK:
                for it in DRIVE_CACHE["items"].values():
                    if query in it["name"].lower():
                        if it["type"] == "Folder":
                            matching_folders.append(dict(it))
                        else:
                            matching_files.append(dict(it))

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "folders": matching_folders[:20],
                "files": matching_files[:50],
                "breadcrumbs": [{"id": None, "name": f"Search results for '{query}'"}]
            }).encode("utf-8"))
            return

        if parsed.path == "/api/stats":
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

        if parsed.path == "/api/refresh":
            populate_cache_from_notion()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        file_path_str = (params.get("path", [None])[0] or 
                         params.get("file", [None])[0] or 
                         params.get("p", [None])[0] or 
                         params.get("url", [None])[0] or 
                         params.get("target", [None])[0])

        if not file_path_str:
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        clean_path_str = urllib.parse.unquote(file_path_str).replace("Local: ", "").replace("Path: ", "").strip()
        norm_str = clean_path_str.replace("/", "\\")
        is_android = ("This PC\\OnePlus Nord CE4" in norm_str or 
                      "Internal shared storage" in norm_str or 
                      "Internal Storage" in norm_str or 
                      "SD card" in norm_str or 
                      "SD Card" in norm_str or
                      clean_path_str.startswith("/storage") or
                      clean_path_str.startswith("/sdcard"))

        if is_android:
            phone_path = clean_path_str
            if "SD card" in norm_str or "SD Card" in norm_str or "/storage/4A21-0000" in clean_path_str:
                clean_rel = norm_str.replace("This PC\\OnePlus Nord CE4\\SD card", "").replace("SD card", "").replace("SD Card", "").replace("/storage/4A21-0000", "").replace("\\", "/").lstrip("/")
                try:
                    stor_out = subprocess.check_output(["adb", "shell", "ls", "/storage"]).decode("utf-8")
                    for s in stor_out.split():
                        if s not in ("emulated", "self", "persist", "sdcard0"):
                            phone_path = f"/storage/{s}/{clean_rel}"
                            break
                except Exception:
                    phone_path = f"/storage/4A21-0000/{clean_rel}"
            else:
                clean_rel = norm_str.replace("This PC\\OnePlus Nord CE4\\Internal shared storage", "").replace("Internal shared storage", "").replace("Internal Storage", "").replace("/storage/emulated/0", "").replace("/sdcard", "").replace("\\", "/").lstrip("/")
                phone_path = f"/storage/emulated/0/{clean_rel}"

            fname = phone_path.split("/")[-1]
            mime, _ = mimetypes.guess_type(fname)
            mime = mime or "application/octet-stream"

            try:
                proc = subprocess.Popen(["adb", "exec-out", "cat", phone_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                content, err = proc.communicate()
                if proc.returncode == 0 and content:
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(content)))
                    if parsed.path == "/download":
                        self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                    else:
                        self.send_header("Content-Disposition", "inline")
                    self.end_headers()
                    self.wfile.write(content)
                    return
            except Exception as e:
                print(f"[!] ADB stream error: {e}")

            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            err_html = f"""<!DOCTYPE html><html><head><title>Device Not Connected</title>
            <style>body{{background:#131314;color:#E3E3E3;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}}
            .box{{background:#1E1F20;padding:32px;border-radius:16px;border:1px solid #3C4043;max-width:500px;text-align:center;}}
            a{{color:#A8C7FA;text-decoration:none;display:inline-block;margin-top:16px;padding:8px 16px;background:#004A77;border-radius:20px;}}</style></head>
            <body><div class="box"><h2>📱 Phone Not Connected</h2><p style="color:#9E9E9E;">Connect your OnePlus Nord CE4 via USB with USB Debugging enabled to stream this file live.</p><p style="color:#666;font-size:12px;word-break:break-all;">{html.escape(phone_path)}</p><a href="/">📁 Open Notion Drive GUI</a></div></body></html>"""
            self.wfile.write(err_html.encode("utf-8"))
            return

        target_path = Path(clean_path_str).resolve()

        if not target_path.exists():
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            err_html = f"""<!DOCTYPE html><html><head><title>File Not Found</title>
            <style>body{{background:#131314;color:#E3E3E3;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}}
            .box{{background:#1E1F20;padding:32px;border-radius:16px;border:1px solid #3C4043;max-width:500px;text-align:center;}}
            a{{color:#A8C7FA;text-decoration:none;display:inline-block;margin-top:16px;padding:8px 16px;background:#004A77;border-radius:20px;}}</style></head>
            <body><div class="box"><h2>📄 File Not Found</h2><p style="color:#9E9E9E;word-break:break-all;">{html.escape(str(target_path))}</p><a href="/">📁 Open Notion Drive GUI</a></div></body></html>"""
            self.wfile.write(err_html.encode("utf-8"))
            return

        if parsed.path == "/download":
            if target_path.is_file():
                try:
                    with open(target_path, "rb") as f:
                        content = f.read()
                    mime, _ = mimetypes.guess_type(str(target_path))
                    mime = mime or "application/octet-stream"
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("Content-Disposition", f'attachment; filename="{target_path.name}"')
                    self.end_headers()
                    self.wfile.write(content)
                    return
                except Exception as e:
                    self.send_error(500, f"Error reading file: {e}")
                    return

        if parsed.path == "/download-folder":
            if target_path.is_dir():
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

        if target_path.is_file():
            try:
                mime, _ = mimetypes.guess_type(str(target_path))
                mime = mime or "application/octet-stream"

                with open(target_path, "rb") as f:
                    content = f.read()

                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Content-Disposition", "inline")
                self.end_headers()
                self.wfile.write(content)
                return
            except Exception as e:
                self.send_error(500, f"Error serving file: {e}")
                return
        elif target_path.is_dir():
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

def start_server():
    if not load_disk_cache():
        print("[+] Initializing cache from Notion DB...")
        populate_cache_from_notion()
    
    server = ThreadingHTTPServer(("127.0.0.1", PORT), NotionServerHandler)
    print(f"🚀 Google Drive Web GUI active on http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Server stopped.")

if __name__ == "__main__":
    start_server()
