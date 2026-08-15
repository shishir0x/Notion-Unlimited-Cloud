"""
Notion Drive Git-Like Sync Engine & Edge Browser Bridge
Features:
- Embedded Local Web Server (http://localhost:8765) for 1-click opening files in Edge tabs
- 1-Click Folder ZIP and File Download
- Live CLI Progress Bar and Git-style change detection
- Real-time Auto-Upload Watcher
"""

import os
import sys
import time
import json
import zipfile
import io
import mimetypes
import urllib.parse
import threading
import argparse
import webbrowser
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Any, List

# Windows UTF-8 console output fix
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

NOTION_VERSION = "2022-06-28"
DEFAULT_API_KEY = os.getenv("NOTION_TOKEN", "")
DEFAULT_DB_ID = os.getenv("NOTION_DATABASE_ID", "")
LOCAL_SERVER_PORT = int(os.getenv("LOCAL_SERVER_PORT", "8765"))
STATE_FILE = Path(__file__).parent / ".notion_sync_state.json"

FILE_TYPE_MAP = {
    ".pdf": "PDF",
    ".doc": "Word", ".docx": "Word",
    ".xls": "Excel", ".xlsx": "Excel", ".csv": "Excel",
    ".ppt": "PowerPoint", ".pptx": "PowerPoint",
    ".jpg": "Image", ".jpeg": "Image", ".png": "Image", ".gif": "Image", ".webp": "Image", ".svg": "Image",
    ".mp4": "Video", ".mkv": "Video", ".mov": "Video", ".avi": "Video",
    ".mp3": "Audio", ".wav": "Audio", ".aac": "Audio",
    ".zip": "ZIP", ".rar": "ZIP", ".7z": "ZIP", ".tar": "ZIP", ".gz": "ZIP",
    ".py": "Code", ".js": "Code", ".ts": "Code", ".html": "Code", ".css": "Code",
    ".java": "Code", ".cpp": "Code", ".c": "Code", ".json": "Code", ".yaml": "Code", ".yml": "Code", ".sql": "Code",
    ".txt": "Other", ".md": "Other"
}

EMOJI_MAP = {
    "PDF": "📕", "Word": "📝", "Excel": "📊", "PowerPoint": "📊",
    "Image": "🖼️", "Video": "🎬", "Audio": "🎵", "ZIP": "📦", "Code": "💻", "Other": "📄"
}

SYSTEM_CRITICAL_IGNORE = {
    "appdata", "application data", "local settings", "$recycle.bin", "system volume information",
    "__pycache__", "node_modules", ".gemini", ".git", "extensions", ".cache", ".gradle",
    ".m2", ".npm", ".rustup", ".cargo", ".nuget", ".venv", "venv", "env", "site-packages",
    "dist-info", ".android", ".jdks", ".antigravity", "crossdevice", "scoop", "microsoft",
    "saved games", "searches", "contacts", "links", "favorites", ".bun", ".cline", ".config",
    ".copilot", ".dotnet", ".expo", ".installer", ".ipython", ".lmstudio", ".local",
    ".sbx-denybin", ".semantic_search", ".ssh", ".virtualbox", ".vscode-shared", "onedrive",
    ".notion drive", "agent-plugins"
}

IGNORED_FILE_PREFIXES = ("ntuser.dat", "ntuser.rhk", "desktop.ini", "~$", "sti_trace.log", "2026-", "_viminfo", ".notion_")
IGNORED_FILE_EXTENSIONS = {
    ".tmp", ".log", ".blf", ".regtrans-ms", ".dat", ".search-ms", ".lock", ".dll",
    ".pyd", ".pyc", ".pyo", ".idx", ".pack", ".sys", ".lnk", ".url", ".exe", ".iso"
}


# ==============================================================================
# Local File Server & Edge Tab Preview Bridge
# ==============================================================================
def start_background_file_server(port: int = LOCAL_SERVER_PORT):
    """Ensures the full Google Drive Web Server is running."""
    import urllib.request
    try:
        req = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats", timeout=0.5)
        if req.status == 200:
            return None
    except Exception:
        pass

    try:
        import notion_server
        notion_server.load_disk_cache()
        handler_cls = getattr(
            notion_server,
            "NotionServerHandler",
            getattr(notion_server, "NotionFileServerHandler", None),
        )
        if handler_cls is None:
            return None
        server = ThreadingHTTPServer(("0.0.0.0", port), handler_cls)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"🚀 Google Drive Web GUI active on http://127.0.0.1:{port}")
        return server
    except Exception:
        return None


# ==============================================================================
# Dynamic CLI Progress Bar
# ==============================================================================
def render_progress_bar(current: int, total: int, prefix: str = "", current_file: str = "", length: int = 30):
    if total == 0:
        return
    percent = float(current) / float(total)
    filled_len = int(length * percent)
    bar = "█" * filled_len + "░" * (length - filled_len)
    display_file = (current_file[:25] + "..") if len(current_file) > 27 else current_file
    remaining = total - current
    sys.stdout.write(f"\r{prefix} |{bar}| {int(percent * 100)}% ({current}/{total}) [{display_file}] [Rem: {remaining}] ")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")
        sys.stdout.flush()


# ==============================================================================
# Notion Git Sync Engine
# ==============================================================================
class NotionGitSyncEngine:
    def __init__(self, api_key: str, db_id: str, root_dir: str = r"C:\Users", include_hidden: bool = True):
        import requests
        self.requests = requests
        self.api_key = api_key
        self.db_id = db_id.replace("-", "")
        self.root_dir = Path(root_dir).resolve()
        self.include_hidden = include_hidden
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION
        }
        self.state = self.load_state()
        self.folder_cache = {}
        # Start local web server for Edge browser integration
        start_background_file_server(LOCAL_SERVER_PORT)

    def load_state(self) -> Dict[str, Any]:
        state_paths = [
            STATE_FILE,
            Path(__file__).parent / ".notion_sync_state.json",
            Path.home() / ".notion_sync_state.json"
        ]
        for sp in state_paths:
            if sp.exists():
                try:
                    with open(sp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if data.get("files"):
                            return data
                except Exception:
                    pass
        return {"files": {}, "folders": {}}

    def save_state(self):
        state_paths = [
            STATE_FILE,
            Path(__file__).parent / ".notion_sync_state.json",
            Path.home() / ".notion_sync_state.json"
        ]
        for sp in state_paths:
            try:
                with open(sp, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, indent=2)
            except Exception:
                pass

    def should_ignore(self, path: Path) -> bool:
        parts = [p.lower() for p in path.parts]
        for ignored in SYSTEM_CRITICAL_IGNORE:
            if ignored in parts:
                return True

        if not self.include_hidden:
            for p in parts:
                if p.startswith(".") and p != ".":
                    return True

        if path.is_file():
            name_lower = path.name.lower()
            if any(name_lower.startswith(prefix) for prefix in IGNORED_FILE_PREFIXES):
                return True
            if path.suffix.lower() in IGNORED_FILE_EXTENSIONS:
                return True
        return False

    def get_local_snapshot(self) -> Dict[str, Dict[str, Any]]:
        snapshot = {}
        if not self.root_dir.exists():
            return snapshot

        for root, dirs, files in os.walk(self.root_dir):
            cur_path = Path(root)
            if self.should_ignore(cur_path):
                dirs[:] = []
                continue

            dirs[:] = [d for d in dirs if d.lower() not in SYSTEM_CRITICAL_IGNORE]
            if not self.include_hidden:
                dirs[:] = [d for d in dirs if not d.startswith(".")]

            for f in files:
                f_path = cur_path / f
                if not self.should_ignore(f_path):
                    try:
                        stat = f_path.stat()
                        snapshot[str(f_path)] = {
                            "mtime": stat.st_mtime,
                            "size": stat.st_size,
                            "name": f,
                            "parent": str(cur_path)
                        }
                    except (PermissionError, FileNotFoundError):
                        continue
        return snapshot

    def status(self):
        print(f"\n🔍 Scanning directory tree: {self.root_dir} ... (Include Hidden: {self.include_hidden})")
        local_files = self.get_local_snapshot()
        tracked_files = self.state.get("files", {})

        added = []
        modified = []
        deleted = []
        unchanged = []
        total_size_bytes = 0

        for p, meta in local_files.items():
            total_size_bytes += meta["size"]
            if p not in tracked_files:
                added.append(p)
            elif (abs(tracked_files[p].get("mtime", 0) - meta["mtime"]) > 1.0 or 
                  tracked_files[p].get("size", 0) != meta["size"]):
                modified.append(p)
            else:
                unchanged.append(p)

        for p in tracked_files:
            if p not in local_files:
                deleted.append(p)

        total_mb = round(total_size_bytes / (1024 * 1024), 2)
        total_gb = round(total_mb / 1024, 2)

        print("\n" + "="*65)
        print("📊 NOTION DRIVE GIT STATUS & STORAGE USAGE")
        print("="*65)
        print(f"💾 Total Local Storage: {total_mb} MB ({total_gb} GB) in {len(local_files)} files")
        print(f"⚪ Unchanged:           {len(unchanged)} files (synchronized)")
        print(f"🟢 Added:               {len(added)} files (new)")
        print(f"🟡 Modified:            {len(modified)} files (edited)")
        print(f"🔴 Deleted:             {len(deleted)} files (removed locally)")
        print("="*65)

        if added:
            print(f"\n🟢 New files to upload ({len(added)} items):")
            for p in added[:12]:
                print(f"   + {p}")
            if len(added) > 12:
                print(f"   ... and {len(added) - 12} more")

        if modified:
            print(f"\n🟡 Modified files ({len(modified)} items):")
            for p in modified[:10]:
                print(f"   * {p}")

        if not added and not modified and not deleted:
            print("\n✨ Workspace is 100% up to date with Notion! No pending changes.")

    def load_notion_folders(self):
        has_more = True
        start_cursor = None
        while has_more:
            payload = {"page_size": 100}
            if start_cursor:
                payload["start_cursor"] = start_cursor
            res = self.requests.post(f"https://api.notion.com/v1/databases/{self.db_id}/query", headers=self.headers, json=payload).json()
            for it in res.get("results", []):
                props = it.get("properties", {})
                title_list = props.get("Name", {}).get("title", [])
                name = title_list[0].get("plain_text", "") if title_list else ""
                clean_name = name.replace("📁 ", "").replace("📄 ", "").strip()
                parents = [p["id"].replace("-", "") for p in props.get("Parent Folder", {}).get("relation", [])]
                parent_id = parents[0] if parents else None
                item_type = props.get("Type", {}).get("select", {}).get("name", "")
                if item_type == "Folder":
                    self.folder_cache[(clean_name, parent_id)] = it["id"].replace("-", "")
            has_more = res.get("has_more", False)
            start_cursor = res.get("next_cursor")

    def ensure_root_container(self, container_name: str, emoji: str = "💽") -> str:
        """Finds or creates a root device container in Notion."""
        if (container_name, None) in self.folder_cache:
            return self.folder_cache[(container_name, None)]

        payload = {
            "parent": {"database_id": self.db_id},
            "icon": {"type": "emoji", "emoji": emoji},
            "properties": {
                "Name": {"title": [{"text": {"content": container_name}}]},
                "Type": {"select": {"name": "Folder"}},
                "Favorite": {"checkbox": True}
            }
        }
        res = self.requests.post("https://api.notion.com/v1/pages", headers=self.headers, json=payload)
        if res.status_code == 200:
            new_id = res.json()["id"].replace("-", "")
            self.folder_cache[(container_name, None)] = new_id
            return new_id
        return None

    def ensure_notion_folder_path(self, folder_path_str: Any) -> str:
        """Creates hierarchy rooted in Local Disk (C:), Local Disk (D:), Internal shared storage, or SD card."""
        folder_str = str(folder_path_str).replace("/", "\\").strip()
        is_android = ("This PC\\OnePlus Nord CE4" in folder_str or 
                      "Internal shared storage" in folder_str or 
                      "Internal Storage" in folder_str or 
                      "SD card" in folder_str or 
                      "SD Card" in folder_str or
                      folder_str.startswith("/storage") or
                      folder_str.startswith("/sdcard"))
        
        if not is_android:
            path_obj = Path(folder_path_str).resolve()
            drive_letter = path_obj.drive.upper().rstrip(":")
            root_container_name = f"Local Disk ({drive_letter}:)" if drive_letter else "Local Disk (C:)"
            container_id = self.ensure_root_container(root_container_name, "💽")
            rel_parts = [p for p in path_obj.parts[1:] if p]
        else:
            if "SD card" in folder_str or "SD Card" in folder_str or "/storage/4A21-0000" in folder_str:
                root_container_name = "SD card"
                container_id = self.ensure_root_container(root_container_name, "💾")
                clean = folder_str.replace("This PC\\OnePlus Nord CE4\\SD card", "").replace("SD card", "").replace("SD Card", "").replace("/storage/4A21-0000", "")
                rel_parts = [p for p in clean.replace("/", "\\").split("\\") if p]
            else:
                root_container_name = "Internal shared storage"
                container_id = self.ensure_root_container(root_container_name, "📱")
                clean = folder_str.replace("This PC\\OnePlus Nord CE4\\Internal shared storage", "").replace("Internal shared storage", "").replace("Internal Storage", "").replace("/storage/emulated/0", "").replace("/sdcard", "")
                rel_parts = [p for p in clean.replace("/", "\\").split("\\") if p]

        current_parent_id = container_id
        for part in rel_parts:
            cache_key = (part, current_parent_id)
            if cache_key in self.folder_cache:
                current_parent_id = self.folder_cache[cache_key]
            else:
                encoded_folder = urllib.parse.quote(str(folder_path_str))
                open_url = f"http://127.0.0.1:{LOCAL_SERVER_PORT}/view?path={encoded_folder}"
                payload = {
                    "parent": {"database_id": self.db_id},
                    "icon": {"type": "emoji", "emoji": "📁"},
                    "properties": {
                        "Name": {"title": [{"text": {"content": part}}]},
                        "Type": {"select": {"name": "Folder"}},
                        "Open in Browser": {"url": open_url},
                        "Favorite": {"checkbox": False}
                    }
                }
                if current_parent_id:
                    payload["properties"]["Parent Folder"] = {"relation": [{"id": current_parent_id}]}

                res = self.requests.post("https://api.notion.com/v1/pages", headers=self.headers, json=payload)
                if res.status_code == 200:
                    new_id = res.json()["id"].replace("-", "")
                    self.folder_cache[cache_key] = new_id
                    current_parent_id = new_id
                else:
                    if "Sub-item hierarchy" in res.text:
                        del payload["properties"]["Parent Folder"]
                        res2 = self.requests.post("https://api.notion.com/v1/pages", headers=self.headers, json=payload)
                        if res2.status_code == 200:
                            new_id = res2.json()["id"].replace("-", "")
                            self.folder_cache[cache_key] = new_id
                            current_parent_id = new_id
                        else:
                            return current_parent_id
                    else:
                        return current_parent_id
        return current_parent_id

    def sync_android(self, target: str = "both", folder_filter: str = None):
        """Streams Android files directly into Notion over USB without saving to PC disk."""
        import subprocess
        print("\n📱 Checking connected Android device via ADB...")
        try:
            out = subprocess.check_output(["adb", "devices"]).decode("utf-8")
        except Exception as e:
            print(f"❌ ADB error: {e}")
            return

        lines = [line.strip() for line in out.splitlines() if line.strip() and not line.startswith("List of")]
        if not lines:
            print("⚠️  No Android phone detected over USB.")
            print("   1. Connect phone with a USB cable.")
            print("   2. Enable 'USB Debugging' in Settings > Developer Options.")
            print("   3. Tap 'Allow' on your phone screen when prompted.")
            return

        device_id = lines[0].split()[0]
        print(f"✅ Found Connected Android Device: {device_id} (OnePlus Nord CE4)")

        storage_targets = []
        if target in ("internal", "both"):
            storage_targets.append((
                "This PC\\OnePlus Nord CE4\\Internal shared storage",
                "/storage/emulated/0",
                "Internal shared storage"
            ))
        if target in ("sdcard", "both"):
            try:
                stor_out = subprocess.check_output(["adb", "-s", device_id, "shell", "ls", "/storage"]).decode("utf-8")
                for s in stor_out.split():
                    if s not in ("emulated", "self", "persist", "sdcard0"):
                        storage_targets.append((
                            "This PC\\OnePlus Nord CE4\\SD card",
                            f"/storage/{s}",
                            "SD card"
                        ))
                        break
            except Exception:
                pass

        android_tracked = self.state.setdefault("android_files", {})
        self.load_notion_folders()

        items_to_sync = []
        for win_label, linux_base, container_name in storage_targets:
            scan_path = f"{linux_base}/{folder_filter}" if folder_filter else linux_base
            print(f"🔍 Scanning {container_name} ({scan_path}) directly on phone (Excluding Android app data)...")
            
            cmd = f"find '{scan_path}/' -type f -not -path '*/.*' -not -path '*/Android*' -not -path '*/Android/*' -not -path '*/.thumbnails*' -not -path '*/LOST.DIR*' -not -path '*/.trash*' -exec stat -c '%n|%s|%Y' {{}} + 2>/dev/null"
            try:
                proc = subprocess.run(["adb", "-s", device_id, "shell", cmd], capture_output=True, text=True, errors="ignore")
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
                            if not prev or abs(prev.get("mtime", 0) - fmtime) > 1.0 or prev.get("size", 0) != fsize:
                                items_to_sync.append({
                                    "fpath": fpath,
                                    "display_path": disp_full,
                                    "name": fname,
                                    "size": fsize,
                                    "mtime": fmtime,
                                    "ext": ext,
                                    "storage_label": container_name
                                })
            except Exception as e:
                print(f"   [!] Error scanning {container_name}: {e}")

        total = len(items_to_sync)
        if total == 0:
            print("✨ All Android phone files are 100% up to date in Notion!")
            return

        print(f"\n🚀 Direct USB Streaming: Uploading {total} items to Notion (0 PC disk bytes used)...\n")

        for idx, it in enumerate(items_to_sync, 1):
            render_progress_bar(idx - 1, total, prefix="Syncing Phone", current_file=it["name"])

            parent_folder_str = "\\".join(it["display_path"].split("\\")[:-1])
            parent_notion_id = self.ensure_notion_folder_path(parent_folder_str)

            file_type = FILE_TYPE_MAP.get(it["ext"], "Other")
            emoji = EMOJI_MAP.get(file_type, "📄")
            size_mb = round(it["size"] / (1024 * 1024), 2)
            
            encoded_fpath = urllib.parse.quote(it["fpath"])
            open_url = f"http://127.0.0.1:{LOCAL_SERVER_PORT}/view?path={encoded_fpath}"

            payload = {
                "parent": {"database_id": self.db_id},
                "icon": {"type": "emoji", "emoji": emoji},
                "properties": {
                    "Name": {"title": [{"text": {"content": it["name"]}}]},
                    "Type": {"select": {"name": "File"}},
                    "File Type": {"select": {"name": file_type}},
                    "File Extension": {"rich_text": [{"text": {"content": it["ext"]}}]},
                    "File Size": {"number": size_mb},
                    "Open in Browser": {"url": open_url},
                    "Description": {"rich_text": [{"text": {"content": f"Path: {it['display_path']}"}}]},
                    "Favorite": {"checkbox": False}
                }
            }
            if parent_notion_id:
                payload["properties"]["Parent Folder"] = {"relation": [{"id": parent_notion_id}]}

            res = self.requests.post("https://api.notion.com/v1/pages", headers=self.headers, json=payload)
            if res.status_code == 200:
                notion_id = res.json()["id"].replace("-", "")
                android_tracked[it["fpath"]] = {
                    "notion_id": notion_id,
                    "mtime": it["mtime"],
                    "size": it["size"],
                    "display_path": it["display_path"]
                }

            self.save_state()
            render_progress_bar(idx, total, prefix="Syncing Phone", current_file=it["name"])

        print("\n✅ Android phone direct sync finished successfully!")

    def rebuild_index(self):
        """Pulls all pages from Notion and rebuilds local sync state & drive cache."""
        print(f"\n🔄 Connecting to Notion and rebuilding index for DB: {self.db_id} ...")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION
        }
        items = []
        has_more = True
        start_cursor = None
        while has_more:
            payload = {"page_size": 100}
            if start_cursor:
                payload["start_cursor"] = start_cursor
            res = self.requests.post(f"https://api.notion.com/v1/databases/{self.db_id}/query", headers=headers, json=payload).json()
            items.extend(res.get("results", []))
            has_more = res.get("has_more", False)
            start_cursor = res.get("next_cursor")

        state_files = {}
        for it in items:
            notion_id = it["id"].replace("-", "")
            props = it.get("properties", {})
            desc_list = props.get("Description", {}).get("rich_text", [])
            desc = desc_list[0].get("plain_text", "") if desc_list else ""
            local_p = desc.replace("Path: ", "").replace("Local: ", "").replace(" (Updated)", "").replace(" (Modified)", "").strip()
            
            if local_p and Path(local_p).exists() and Path(local_p).is_file():
                try:
                    stat = Path(local_p).stat()
                    state_files[local_p] = {
                        "notion_id": notion_id,
                        "mtime": stat.st_mtime,
                        "size": stat.st_size
                    }
                except Exception:
                    pass

        self.state["files"] = state_files
        self.state["last_sync"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save_state()
        print(f"✅ Rebuilt local cloud index! {len(state_files)} local files mapped to Notion.")

    def sync(self, force_all: bool = False):
        sync_mode = "FORCE ALL FILES" if force_all else "INCREMENTAL (CHANGED & NEW ONLY)"
        print(f"\n🚀 Running Notion Git-Sync [{sync_mode}] for: {self.root_dir}")
        self.load_notion_folders()
        local_files = self.get_local_snapshot()
        tracked = self.state.setdefault("files", {})

        tasks = []
        for p, meta in local_files.items():
            if force_all:
                tasks.append((p, meta, "NEW" if p not in tracked else "MODIFIED"))
            else:
                if p not in tracked:
                    tasks.append((p, meta, "NEW"))
                elif (abs(tracked[p].get("mtime", 0) - meta["mtime"]) > 1.0 or 
                      tracked[p].get("size", 0) != meta["size"]):
                    tasks.append((p, meta, "MODIFIED"))

        total_tasks = len(tasks)
        if total_tasks == 0:
            print("✨ Everything is already up to date! Nothing to sync.")
            return

        print(f"[+] Total items to sync: {total_tasks}\n")

        for idx, (file_path_str, meta, change_type) in enumerate(tasks, 1):
            file_path = Path(file_path_str)
            render_progress_bar(idx - 1, total_tasks, prefix="Uploading", current_file=file_path.name)

            parent_dir = file_path.parent
            parent_notion_id = self.ensure_notion_folder_path(parent_dir)

            ext = file_path.suffix.lower()
            file_type = FILE_TYPE_MAP.get(ext, "Other")
            emoji = EMOJI_MAP.get(file_type, "📄")
            size_mb = round(meta["size"] / (1024 * 1024), 2)
            
            encoded_path = urllib.parse.quote(file_path_str)
            edge_view_url = f"http://127.0.0.1:{LOCAL_SERVER_PORT}/view?path={encoded_path}"

            if change_type == "NEW":
                payload = {
                    "parent": {"database_id": self.db_id},
                    "icon": {"type": "emoji", "emoji": emoji},
                    "properties": {
                        "Name": {"title": [{"text": {"content": file_path.name}}]},
                        "Type": {"select": {"name": "File"}},
                        "File Type": {"select": {"name": file_type}},
                        "File Extension": {"rich_text": [{"text": {"content": ext}}]},
                        "File Size": {"number": size_mb},
                        "Open in Browser": {"url": edge_view_url},
                        "Description": {"rich_text": [{"text": {"content": f"Local: {file_path_str}"}}]},
                        "Favorite": {"checkbox": False}
                    }
                }
                if parent_notion_id:
                    payload["properties"]["Parent Folder"] = {"relation": [{"id": parent_notion_id}]}

                res = self.requests.post("https://api.notion.com/v1/pages", headers=self.headers, json=payload)
                if res.status_code == 200:
                    notion_id = res.json()["id"].replace("-", "")
                    tracked[file_path_str] = {
                        "notion_id": notion_id,
                        "mtime": meta["mtime"],
                        "size": meta["size"]
                    }

            elif change_type == "MODIFIED":
                notion_id = tracked.get(file_path_str, {}).get("notion_id")
                if notion_id:
                    update_payload = {
                        "properties": {
                            "File Size": {"number": size_mb},
                            "Open in Browser": {"url": edge_view_url},
                            "Description": {"rich_text": [{"text": {"content": f"Local: {file_path_str} (Updated)"}}]}
                        }
                    }
                    res = self.requests.patch(f"https://api.notion.com/v1/pages/{notion_id}", headers=self.headers, json=update_payload)
                    if res.status_code == 200:
                        tracked[file_path_str]["mtime"] = meta["mtime"]
                        tracked[file_path_str]["size"] = meta["size"]

            self.save_state()
            render_progress_bar(idx, total_tasks, prefix="Uploading", current_file=file_path.name)

        print("\n✅ Sync finished successfully!")

    def watch(self, interval: int = 4):
        print(f"\n==================================================================")
        print(f"👀 LIVE AUTO-UPLOAD WATCHER & EDGE BRIDGE ACTIVE")
        print(f"   Root Directory:    {self.root_dir}")
        print(f"   Edge Server URL:   http://localhost:{LOCAL_SERVER_PORT}")
        print(f"   Hidden Folders:    {self.include_hidden}")
        print(f"==================================================================")
        print("💡 Drop any document or edit any file—it will auto-upload in real time!")
        print("🌐 Click 'Open in Browser' in Notion to view files directly in Edge tabs.")
        print("🛑 Press Ctrl + C in terminal to stop.\n")

        while True:
            try:
                local_files = self.get_local_snapshot()
                tracked = self.state.get("files", {})

                changed_files = []
                for p, meta in local_files.items():
                    if p not in tracked:
                        changed_files.append((p, "NEW"))
                    elif (abs(tracked[p].get("mtime", 0) - meta["mtime"]) > 1.0) or (tracked[p].get("size", 0) != meta["size"]):
                        changed_files.append((p, "MODIFIED"))

                if changed_files:
                    print(f"\n⚡ [{time.strftime('%H:%M:%S')}] Detected {len(changed_files)} change(s):")
                    for p, ctype in changed_files[:5]:
                        action = "➕ Added" if ctype == "NEW" else "🔄 Modified"
                        print(f"   {action}: {Path(p).name}")
                    if len(changed_files) > 5:
                        print(f"   ... and {len(changed_files) - 5} more")

                    self.sync(force_all=False)

                time.sleep(interval)
            except KeyboardInterrupt:
                print("\n🛑 Watcher stopped by user.")
                break

def run_interactive_menu():
    """Interactive CLI menu when launched directly."""
    start_background_file_server(LOCAL_SERVER_PORT)
    engine_c = NotionGitSyncEngine(DEFAULT_API_KEY, DEFAULT_DB_ID, root_dir=r"C:\Users", include_hidden=True)
    engine_d = NotionGitSyncEngine(DEFAULT_API_KEY, DEFAULT_DB_ID, root_dir=r"D:\\", include_hidden=True)

    while True:
        try:
            print("\n" + "="*70)
            print("        ☁️ NOTION UNLIMITED CLOUD & WEB DRIVE DASHBOARD")
            print("="*70)
            print("  [1] ⚡ Sync Local Disk (C:) - Changed & New Files (Incremental)")
            print("  [2] 🚀 Sync Local Disk (C:) - Force Upload All Files")
            print("  [3] 💾 Sync Local Disk (D:) - Incremental Sync")
            print("  [4] 📱 Direct Android USB Sync (Internal Storage & SD Card)")
            print("  [5] 👀 Start Real-Time Auto-Sync Watcher (C: Drive Monitor)")
            print("  [6] 🌐 Launch Web Drive File Manager GUI (Google Drive in Browser)")
            print("  [7] 📊 Check Storage Status & File Integrity (Git-Style Inspect)")
            print("  [8] 🔄 Rebuild & Refresh Local Cloud Index (Sync state from Notion)")
            print("  [9] 📝 Open Notion Database in Browser")
            print("  [10] ❌ Exit")
            print("="*70)
            
            choice = input("Select an option [1-10]: ").strip()
            if choice == "1":
                engine_c.sync(force_all=False)
            elif choice == "2":
                confirm = input("⚠️  Upload ALL files on C: drive tree to Notion? [y/N]: ").strip().lower()
                if confirm in ("y", "yes"):
                    engine_c.sync(force_all=True)
            elif choice == "3":
                engine_d.sync(force_all=False)
            elif choice == "4":
                print("\n📱 Android USB Direct Sync:")
                print("   [1] Full Sync (Both Internal Storage & SD Card)")
                print("   [2] Photos & Camera (DCIM)")
                print("   [3] Documents Folder")
                print("   [4] Downloads Folder")
                sub = input("Select Android option [1-4]: ").strip()
                if sub == "1":
                    engine_c.sync_android(target="both")
                elif sub == "2":
                    engine_c.sync_android(target="both", folder_filter="DCIM")
                elif sub == "3":
                    engine_c.sync_android(target="both", folder_filter="Documents")
                elif sub == "4":
                    engine_c.sync_android(target="both", folder_filter="Download")
            elif choice == "5":
                engine_c.watch()
            elif choice == "6":
                print("🌐 Opening Google Drive GUI on http://127.0.0.1:8765 ...")
                start_background_file_server(LOCAL_SERVER_PORT)
                webbrowser.open("http://127.0.0.1:8765")
            elif choice == "7":
                engine_c.status()
            elif choice == "8":
                engine_c.rebuild_index()
            elif choice == "9":
                print("🌐 Opening Notion in your default browser...")
                webbrowser.open(f"https://app.notion.com/p/{DEFAULT_DB_ID}")
            elif choice in ("10", "exit", "q", "quit"):
                print("👋 Exiting Notion Cloud Sync Engine. Goodbye!")
                sys.exit(0)
            else:
                print("❌ Invalid selection. Please enter a number between 1 and 10.")
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Exiting Notion Cloud Sync Engine. Goodbye!")
            sys.exit(0)


def main():
    if len(sys.argv) == 1:
        run_interactive_menu()
        return

    parser = argparse.ArgumentParser(description="Notion Unlimited Cloud & Web Drive Engine")
    parser.add_argument("command", nargs="?", default="menu", 
                        choices=["menu", "status", "sync", "sync-all", "sync-d", "android", "watch", "rebuild", "gui"], 
                        help="Command to run")
    parser.add_argument("--path", type=str, default=r"C:\Users", help="Root directory to sync")
    parser.add_argument("--hidden", action="store_true", default=True, help="Include hidden folders & files")
    parser.add_argument("--no-hidden", action="store_false", dest="hidden", help="Exclude hidden folders")
    parser.add_argument("--token", type=str, default=DEFAULT_API_KEY, help="Notion Integration Token")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_ID, help="Notion Database ID")
    parser.add_argument("--interval", type=int, default=4, help="Watcher poll interval")

    args = parser.parse_args()

    if args.command == "menu":
        run_interactive_menu()
        return

    engine = NotionGitSyncEngine(args.token, args.db, root_dir=args.path, include_hidden=args.hidden)

    if args.command == "status":
        engine.status()
    elif args.command == "sync":
        engine.sync(force_all=False)
    elif args.command == "sync-all":
        engine.sync(force_all=True)
    elif args.command == "sync-d":
        engine_d = NotionGitSyncEngine(args.token, args.db, root_dir=r"D:\\", include_hidden=args.hidden)
        engine_d.sync(force_all=False)
    elif args.command == "android":
        engine.sync_android(target="both")
    elif args.command == "watch":
        engine.watch(interval=args.interval)
    elif args.command == "rebuild":
        engine.rebuild_index()
    elif args.command == "gui":
        start_background_file_server(LOCAL_SERVER_PORT)
        webbrowser.open(f"http://127.0.0.1:{LOCAL_SERVER_PORT}")


if __name__ == "__main__":
    main()
