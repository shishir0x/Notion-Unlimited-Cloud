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
        server = ThreadingHTTPServer(("0.0.0.0", port), notion_server.NotionFileServerHandler)
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

    def ensure_notion_folder_path(self, local_folder_path: Path) -> str:
        try:
            rel = local_folder_path.relative_to(self.root_dir.parent)
        except ValueError:
            rel = local_folder_path

        parts = rel.parts
        current_parent_id = None

        for part in parts:
            cache_key = (part, current_parent_id)
            if cache_key in self.folder_cache:
                current_parent_id = self.folder_cache[cache_key]
            else:
                encoded_folder = urllib.parse.quote(str(local_folder_path))
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
                    return None
        return current_parent_id

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
            
            # Browser View URL for Microsoft Edge & Google Drive Web GUI
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
    # Ensure server is running
    start_background_file_server(LOCAL_SERVER_PORT)
    engine = NotionGitSyncEngine(DEFAULT_API_KEY, DEFAULT_DB_ID, root_dir=r"C:\Users", include_hidden=True)

    while True:
        try:
            print("\n" + "="*68)
            print("        ☁️ NOTION UNLIMITED CLOUD & WEB DRIVE DASHBOARD")
            print("="*68)
            print("  [1] ⚡ Upload Only Changed & New Files (Smart Incremental Sync)")
            print("  [2] 🚀 Upload All Files (Force Full Cloud Sync)")
            print("  [3] 👀 Start Real-Time Auto-Sync Watcher (Live Background Monitor)")
            print("  [4] 🌐 Launch Web Drive File Manager GUI (Google Drive in Browser)")
            print("  [5] 📊 Check Storage Status & File Integrity (Git-Style Inspect)")
            print("  [6] 🔄 Rebuild & Refresh Local Cloud Index (Sync state from Notion)")
            print("  [7] 📝 Open Notion Database in Browser")
            print("  [8] ❌ Exit")
            print("="*68)
            
            choice = input("Select an option [1-8]: ").strip()
            if choice == "1":
                engine.sync(force_all=False)
            elif choice == "2":
                confirm = input("⚠️  Upload ALL files in directory tree to Notion? [y/N]: ").strip().lower()
                if confirm in ("y", "yes"):
                    engine.sync(force_all=True)
                else:
                    print("Canceled.")
            elif choice == "3":
                engine.watch()
            elif choice == "4":
                print("🌐 Opening Google Drive GUI on http://127.0.0.1:8765 ...")
                start_background_file_server(LOCAL_SERVER_PORT)
                webbrowser.open("http://127.0.0.1:8765")
            elif choice == "5":
                engine.status()
            elif choice == "6":
                engine.rebuild_index()
            elif choice == "7":
                print("🌐 Opening Notion in your default browser...")
                webbrowser.open(f"https://app.notion.com/p/{DEFAULT_DB_ID}")
            elif choice in ("8", "exit", "q", "quit"):
                print("👋 Exiting Notion Cloud Sync Engine. Goodbye!")
                sys.exit(0)
            else:
                print("❌ Invalid selection. Please enter a number between 1 and 8.")
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Exiting Notion Cloud Sync Engine. Goodbye!")
            sys.exit(0)


def main():
    if len(sys.argv) == 1:
        run_interactive_menu()
        return

    parser = argparse.ArgumentParser(description="Notion Unlimited Cloud & Web Drive Engine")
    parser.add_argument("command", nargs="?", default="menu", 
                        choices=["menu", "status", "sync", "sync-all", "watch", "rebuild", "gui"], 
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
    elif args.command == "watch":
        engine.watch(interval=args.interval)
    elif args.command == "rebuild":
        engine.rebuild_index()
    elif args.command == "gui":
        start_background_file_server(LOCAL_SERVER_PORT)
        webbrowser.open(f"http://127.0.0.1:{LOCAL_SERVER_PORT}")


if __name__ == "__main__":
    main()
