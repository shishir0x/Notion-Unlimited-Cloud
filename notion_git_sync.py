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
from http.server import HTTPServer, BaseHTTPRequestHandler
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
    ".notion drive"
}

IGNORED_FILE_PREFIXES = ("ntuser.dat", "ntuser.rhk", "desktop.ini", "~$", "sti_trace.log", "2026-", "_viminfo", ".notion_")
IGNORED_FILE_EXTENSIONS = {
    ".tmp", ".log", ".blf", ".regtrans-ms", ".dat", ".search-ms", ".lock", ".dll",
    ".pyd", ".pyc", ".pyo", ".idx", ".pack", ".sys", ".lnk", ".url", ".exe", ".iso"
}


# ==============================================================================
# Local File Server & Edge Tab Preview Bridge
# ==============================================================================
class NotionFileServerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress console clutter for background requests

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
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
        target_path = Path(clean_path_str).resolve()
        if not target_path.exists():
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h3>File not found on local drive.</h3>")
            return

        # Route 1: View in Edge Tab
        if parsed.path == "/view":
            if target_path.is_dir():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                html = f"<h2>📁 Folder: {target_path.name}</h2><p>Path: {target_path}</p><a href='/download_folder?path={urllib.parse.quote(str(target_path))}'>⬇️ Download Entire Folder as ZIP</a><hr><ul>"
                for item in target_path.iterdir():
                    action = "view" if item.is_file() else "view"
                    html += f"<li><a href='/{action}?path={urllib.parse.quote(str(item))}'>{'📄' if item.is_file() else '📁'} {item.name}</a></li>"
                html += "</ul>"
                self.wfile.write(html.encode("utf-8"))
                return

            mime_type, _ = mimetypes.guess_type(str(target_path))
            if not mime_type:
                mime_type = "text/plain" if target_path.suffix in [".py", ".json", ".yaml", ".md", ".txt", ".js", ".ts"] else "application/octet-stream"

            try:
                with open(target_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Content-Disposition", f"inline; filename=\"{target_path.name}\"")
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"Error reading file: {e}".encode("utf-8"))

        # Route 2: Direct Download Single File
        elif parsed.path == "/download":
            if target_path.is_file():
                try:
                    with open(target_path, "rb") as f:
                        content = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("Content-Disposition", f"attachment; filename=\"{target_path.name}\"")
                    self.end_headers()
                    self.wfile.write(content)
                except Exception as e:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(f"Download error: {e}".encode("utf-8"))

        # Route 3: Download Entire Folder as ZIP
        elif parsed.path == "/download_folder":
            if target_path.is_dir():
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                    for root, _, files in os.walk(target_path):
                        for file in files:
                            full_f = Path(root) / file
                            try:
                                rel_f = full_f.relative_to(target_path)
                                zf.write(full_f, arcname=str(rel_f))
                            except Exception:
                                pass
                zip_buffer.seek(0)
                zip_data = zip_buffer.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(zip_data)))
                self.send_header("Content-Disposition", f"attachment; filename=\"{target_path.name}.zip\"")
                self.end_headers()
                self.wfile.write(zip_data)


def start_background_file_server(port: int = LOCAL_SERVER_PORT):
    """Starts the local HTTP bridge in a background daemon thread."""
    try:
        server = HTTPServer(("127.0.0.1", port), NotionFileServerHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server
    except Exception as e:
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

    def sync(self):
        print(f"\n🚀 Running Notion Git-Sync for: {self.root_dir}")
        self.load_notion_folders()
        local_files = self.get_local_snapshot()
        tracked = self.state.setdefault("files", {})

        added_or_modified = []
        for p, meta in local_files.items():
            if p not in tracked:
                added_or_modified.append((p, meta, "NEW"))
            elif (abs(tracked[p].get("mtime", 0) - meta["mtime"]) > 1.0 or 
                  tracked[p].get("size", 0) != meta["size"]):
                added_or_modified.append((p, meta, "MODIFIED"))

        total_tasks = len(added_or_modified)
        if total_tasks == 0:
            print("✨ Everything is already up to date! Nothing to sync.")
            return

        print(f"[+] Total items to sync: {total_tasks}\n")

        for idx, (file_path_str, meta, change_type) in enumerate(added_or_modified, 1):
            file_path = Path(file_path_str)
            render_progress_bar(idx - 1, total_tasks, prefix="Uploading", current_file=file_path.name)

            parent_dir = file_path.parent
            parent_notion_id = self.ensure_notion_folder_path(parent_dir)

            ext = file_path.suffix.lower()
            file_type = FILE_TYPE_MAP.get(ext, "Other")
            emoji = EMOJI_MAP.get(file_type, "📄")
            size_mb = round(meta["size"] / (1024 * 1024), 2)
            
            # Browser View URL for Microsoft Edge
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
                notion_id = tracked[file_path_str].get("notion_id")
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

                    self.sync()

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
            print("        📁 NOTION GOOGLE DRIVE AUTO-SYNC & WEB GUI ENGINE")
            print("="*68)
            print("  [1] 🚀 Start Live Auto-Upload Watcher (Syncs changes to Notion)")
            print("  [2] 🌐 Launch Web Drive File Manager GUI (Google Drive in Browser)")
            print("  [3] ⚡ Run Incremental Sync Now (Push pending changes)")
            print("  [4] 📊 Check Status & Storage Usage (Git-style inspect)")
            print("  [5] 📝 Open Notion Database in Browser")
            print("  [6] ❌ Exit")
            print("="*68)
            
            choice = input("Select an option [1-6]: ").strip()
            if choice == "1":
                engine.watch()
            elif choice == "2":
                print("🌐 Opening Google Drive GUI on http://127.0.0.1:8765 ...")
                webbrowser.open("http://127.0.0.1:8765")
            elif choice == "3":
                engine.sync()
            elif choice == "4":
                engine.status()
            elif choice == "5":
                print("🌐 Opening Notion in your default browser...")
                webbrowser.open("https://app.notion.com/p/3bd3d81b2f368055902aeee41736ae89")
            elif choice in ("6", "exit", "q", "quit"):
                print("👋 Exiting Notion Sync Engine. Goodbye!")
                sys.exit(0)
            else:
                print("❌ Invalid selection. Please enter 1, 2, 3, 4, 5, or 6.")
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Exiting Notion Sync Engine. Goodbye!")
            sys.exit(0)


def main():
    if len(sys.argv) == 1:
        run_interactive_menu()
        return

    parser = argparse.ArgumentParser(description="Notion Drive Git-Style Sync Engine")
    parser.add_argument("command", nargs="?", default="menu", choices=["menu", "status", "sync", "watch"], help="Command to run")
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
        engine.sync()
    elif args.command == "watch":
        engine.watch(interval=args.interval)


if __name__ == "__main__":
    main()
