"""
Notion Drive Web GUI File Manager & Edge Browser Bridge
A modern, full-featured Web GUI mirroring Google Drive & OneDrive.
Serves a responsive single-page web app at http://127.0.0.1:8765
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
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

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
DEFAULT_DB_ID = os.getenv("NOTION_DATABASE_ID", "")
CACHE_FILE = Path.home() / ".notion_drive_cache.json"

DRIVE_CACHE = {
    "items": {},
    "children": {},
    "root_items": []
}

def enrich_cache_items():
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
        if "size_bytes" not in item:
            item["size_bytes"] = int(item.get("size_mb", 0) * 1024 * 1024)
        if "mtime" not in item:
            item["mtime"] = 0
        if "ctime" not in item:
            item["ctime"] = 0

def load_disk_cache():
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                DRIVE_CACHE["items"] = data.get("items", {})
                DRIVE_CACHE["children"] = data.get("children", {})
                DRIVE_CACHE["root_items"] = data.get("root_items", [])
                enrich_cache_items()
                print(f"[+] Loaded {len(DRIVE_CACHE['items'])} items from disk cache!")
        except Exception:
            pass

def save_disk_cache():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(DRIVE_CACHE, f, indent=2)
    except Exception:
        pass

def populate_cache_from_notion():
    import requests
    headers = {
        "Authorization": f"Bearer {DEFAULT_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION
    }

    try:
        items = []
        has_more = True
        start_cursor = None
        while has_more:
            payload = {"page_size": 100}
            if start_cursor:
                payload["start_cursor"] = start_cursor
            res = requests.post(f"https://api.notion.com/v1/databases/{DEFAULT_DB_ID}/query", headers=headers, json=payload).json()
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

            if local_p and Path(local_p).exists():
                try:
                    stat = Path(local_p).stat()
                    mtime = stat.st_mtime
                    ctime = stat.st_ctime
                    size_bytes = stat.st_size
                    size_mb = round(size_bytes / (1024 * 1024), 2)
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

        # Organize root device containers
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

        DRIVE_CACHE["items"] = cached_items
        DRIVE_CACHE["children"] = children_map
        DRIVE_CACHE["root_items"] = root_items
        enrich_cache_items()
        save_disk_cache()
        print(f"[+] Notion cache refreshed: {len(cached_items)} items.")
    except Exception as e:
        print(f"[!] Notion sync error: {e}")

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
        }
        .nav-item:hover { background-color: var(--bg-card-hover); }
        .nav-item.active { background-color: var(--bg-selected); color: var(--accent-blue); }
        .nav-item i { font-size: 16px; width: 20px; text-align: center; }

        .storage-card {
            background-color: var(--bg-card);
            border-radius: var(--item-radius);
            padding: 16px;
            margin-top: auto;
            border: 1px solid var(--border-color);
        }
        .storage-title { font-size: 13px; color: var(--text-muted); margin-bottom: 8px; display: flex; justify-content: space-between; }
        .storage-bar-bg { background: #3C4043; height: 6px; border-radius: 3px; overflow: hidden; margin-bottom: 8px; }
        .storage-bar-fill { background: var(--accent-blue); height: 100%; width: 45%; border-radius: 3px; transition: width 0.3s; }
        .storage-text { font-size: 12px; color: var(--text-main); }

        /* Main Workspace */
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

        /* Breadcrumb & Sub-header Toolbar */
        .content-header {
            padding: 14px 24px 10px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            flex-wrap: wrap;
            border-bottom: 1px solid rgba(255,255,255,0.06);
        }
        .breadcrumbs {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 18px;
            font-weight: 500;
            flex-wrap: wrap;
        }
        .breadcrumb-item {
            color: var(--text-muted);
            cursor: pointer;
            text-decoration: none;
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 4px 8px;
            border-radius: 6px;
            transition: all 0.15s;
        }
        .breadcrumb-item:hover { color: var(--text-main); background: var(--bg-card-hover); }
        .breadcrumb-item.active { color: var(--text-main); font-weight: 600; }
        .breadcrumb-sep { color: #5F6368; font-size: 12px; }

        /* Toolbar Controls (Sort & View) */
        .toolbar-controls {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .control-pill {
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            display: flex;
            align-items: center;
            padding: 4px 10px;
            gap: 6px;
        }
        .control-pill span { font-size: 13px; color: var(--text-muted); }
        .control-pill select {
            background: transparent;
            border: none;
            color: var(--text-main);
            font-size: 13px;
            font-weight: 500;
            outline: none;
            cursor: pointer;
        }
        .control-pill select option {
            background: var(--bg-sidebar);
            color: var(--text-main);
        }
        .btn-tool {
            background: transparent;
            border: none;
            color: var(--text-main);
            cursor: pointer;
            padding: 4px 6px;
            border-radius: 4px;
            font-size: 13px;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: color 0.15s;
        }
        .btn-tool:hover { color: var(--accent-blue); }

        .view-switcher {
            display: flex;
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 2px;
        }
        .view-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            width: 32px;
            height: 30px;
            border-radius: 16px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 13px;
            cursor: pointer;
            transition: all 0.15s;
        }
        .view-btn.active {
            background-color: var(--bg-selected);
            color: var(--accent-blue);
        }

        .workspace-scroll {
            flex: 1;
            padding: 16px 24px 32px;
            overflow-y: auto;
        }

        .section-label {
            font-size: 13px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin: 16px 0 12px;
        }

        /* Grid View */
        .grid-view {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
            gap: 16px;
        }

        .grid-card {
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--item-radius);
            padding: 16px;
            display: flex;
            flex-direction: column;
            cursor: pointer;
            transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
        }
        .grid-card:hover {
            background-color: var(--bg-card-hover);
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            border-color: #5F6368;
        }
        .grid-card.folder-card {
            flex-direction: row;
            align-items: center;
            gap: 12px;
            padding: 14px 16px;
        }
        .card-icon { font-size: 26px; display: flex; align-items: center; justify-content: center; width: 44px; height: 44px; border-radius: 8px; }
        .card-icon.folder { color: #A8C7FA; }
        .card-icon.pdf { color: #EA4335; }
        .card-icon.image { color: #34A853; }
        .card-icon.code { color: #FBBC04; }
        .card-icon.video { color: #A142F4; }
        .card-icon.audio { color: #FA7B17; }
        .card-icon.other { color: #9AA0A6; }

        .card-title {
            font-size: 14px;
            font-weight: 500;
            color: var(--text-main);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            margin-top: 8px;
        }
        .folder-card .card-title { margin-top: 0; }
        .card-meta { font-size: 12px; color: var(--text-muted); margin-top: 4px; display: flex; justify-content: space-between; }

        .card-actions {
            position: absolute;
            top: 10px;
            right: 10px;
            display: none;
            gap: 4px;
        }
        .grid-card:hover .card-actions { display: flex; }
        .action-btn {
            background: #202124;
            border: 1px solid var(--border-color);
            color: var(--text-main);
            width: 28px;
            height: 28px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            cursor: pointer;
            transition: all 0.15s;
            text-decoration: none;
        }
        .action-btn:hover { background: var(--accent-primary); color: white; border-color: transparent; }

        /* List View (Table) */
        .list-view-container {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        .drive-table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }
        .drive-table th {
            color: var(--text-muted);
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            padding: 10px 16px;
            border-bottom: 1px solid var(--border-color);
            cursor: pointer;
            user-select: none;
        }
        .drive-table th:hover { color: var(--text-main); }
        .drive-table th i { margin-left: 4px; font-size: 11px; }
        .drive-table td {
            padding: 12px 16px;
            border-bottom: 1px solid rgba(255,255,255,0.04);
            color: var(--text-main);
            vertical-align: middle;
        }
        .drive-table tbody tr {
            cursor: pointer;
            transition: background 0.1s;
        }
        .drive-table tbody tr:hover {
            background-color: var(--bg-card-hover);
        }
        .td-name {
            display: flex;
            align-items: center;
            gap: 12px;
            font-weight: 500;
            max-width: 380px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .td-icon { font-size: 18px; width: 24px; text-align: center; }
        .td-icon.folder { color: #A8C7FA; }
        .td-icon.pdf { color: #EA4335; }
        .td-icon.image { color: #34A853; }
        .td-icon.code { color: #FBBC04; }
        .td-icon.video { color: #A142F4; }
        .td-icon.audio { color: #FA7B17; }
        .td-icon.other { color: #9AA0A6; }

        .td-meta { color: var(--text-muted); font-size: 12px; }
        .table-actions {
            display: flex;
            gap: 6px;
            opacity: 0;
            transition: opacity 0.15s;
        }
        .drive-table tr:hover .table-actions { opacity: 1; }

        /* Modal Overlay */
        .modal-overlay {
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.75);
            backdrop-filter: blur(4px);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 1000;
        }
        .modal-content {
            background: #1E1F20;
            border: 1px solid var(--border-color);
            border-radius: 16px;
            width: 90%;
            max-width: 900px;
            max-height: 85vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            box-shadow: 0 16px 40px rgba(0,0,0,0.6);
        }
        .modal-header {
            padding: 16px 20px;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .modal-body { flex: 1; padding: 20px; overflow-y: auto; display: flex; justify-content: center; align-items: center; }
        .modal-body iframe { width: 100%; height: 60vh; border: none; border-radius: 8px; }
        .modal-body img { max-width: 100%; max-height: 60vh; object-fit: contain; border-radius: 8px; }
    </style>
</head>
<body>
    <div class="sidebar">
        <div class="logo">
            <i class="fa-brands fa-google-drive"></i>
            <span>Notion Drive</span>
        </div>

        <div class="nav-section">
            <div class="nav-item active" onclick="loadDriveRoot()">
                <i class="fa-solid fa-folder"></i>
                <span>My Drive</span>
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

    <div class="main-container">
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

        <div class="content-header">
            <div class="breadcrumbs" id="breadcrumbContainer">
                <span class="breadcrumb-item active" onclick="loadDriveRoot()">
                    <i class="fa-solid fa-hard-drive"></i> My Drive
                </span>
            </div>

            <!-- Google Drive Style Sort and View Mode Controls -->
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
            <!-- Grid View Container -->
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

            <!-- List View Table Container -->
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

            <!-- Empty Folder Message -->
            <div id="emptyMessage" style="display:none; color: var(--text-muted); padding: 48px 0; text-align: center; font-size: 15px;">
                <i class="fa-regular fa-folder-open" style="font-size: 36px; margin-bottom: 12px; display: block; opacity: 0.6;"></i>
                This folder is empty.
            </div>
        </div>
    </div>

    <!-- Interactive File Preview Modal -->
    <div class="modal-overlay" id="previewModal" onclick="closeModal(event)">
        <div class="modal-content" onclick="event.stopPropagation()">
            <div class="modal-header">
                <span style="font-weight: 600;" id="modalTitle">File Preview</span>
                <div style="display: flex; gap: 10px;">
                    <a id="modalOpenTabBtn" class="action-btn" title="Open in New Tab" target="_blank"><i class="fa-solid fa-arrow-up-right-from-square"></i></a>
                    <a id="modalDownloadBtn" class="action-btn" title="Download File"><i class="fa-solid fa-download"></i></a>
                    <button class="action-btn" onclick="closeModal()"><i class="fa-solid fa-xmark"></i></button>
                </div>
            </div>
            <div class="modal-body" id="modalBody"></div>
        </div>
    </div>

    <script>
        let currentFolderId = null;
        let driveData = { folders: [], files: [], breadcrumbs: [] };
        let viewMode = localStorage.getItem('notion_drive_view_mode') || 'grid';
        let sortBy = localStorage.getItem('notion_drive_sort_by') || 'name';
        let sortDir = localStorage.getItem('notion_drive_sort_dir') || 'asc';

        // Helpers
        function formatBytes(bytes) {
            if (!bytes || bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
        }

        function formatDate(timestamp) {
            if (!timestamp) return '--';
            const date = new Date(timestamp * 1000 || timestamp);
            if (isNaN(date.getTime())) return '--';
            const now = new Date();
            const isToday = date.toDateString() === now.toDateString();
            const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            if (isToday) return `Today, ${timeStr}`;
            return date.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) + `, ${timeStr}`;
        }

        function getFolderIcon(name) {
            const lower = (name || '').toLowerCase();
            if (lower.includes('local disk') || lower.includes('(c:)') || lower.includes('(d:)')) {
                return { cls: 'fa-hard-drive', color: '#8AB4F8', bg: 'rgba(138, 180, 248, 0.15)' };
            }
            if (lower.includes('internal storage') || lower.includes('phone')) {
                return { cls: 'fa-mobile-screen-button', color: '#81C995', bg: 'rgba(129, 201, 149, 0.15)' };
            }
            if (lower.includes('sd card')) {
                return { cls: 'fa-sd-card', color: '#FDD663', bg: 'rgba(253, 214, 99, 0.15)' };
            }
            return { cls: 'fa-folder', color: '#A8C7FA', bg: 'rgba(168, 199, 250, 0.15)' };
        }

        function getFileIcon(ext) {
            ext = (ext || '').toLowerCase();
            if (ext === '.pdf') return { cls: 'fa-file-pdf', type: 'pdf' };
            if (['.jpg','.png','.jpeg','.webp','.svg','.gif','.ico','.bmp'].includes(ext)) return { cls: 'fa-file-image', type: 'image' };
            if (['.py','.js','.ts','.html','.json','.yaml','.yml','.sql','.c','.cpp','.java','.css','.sh','.bat'].includes(ext)) return { cls: 'fa-file-code', type: 'code' };
            if (['.mp4','.mkv','.webm','.mov','.avi'].includes(ext)) return { cls: 'fa-file-video', type: 'video' };
            if (['.mp3','.wav','.ogg','.m4a','.flac'].includes(ext)) return { cls: 'fa-file-audio', type: 'audio' };
            return { cls: 'fa-file', type: 'other' };
        }

        function setViewMode(mode) {
            viewMode = mode;
            localStorage.setItem('notion_drive_view_mode', mode);
            document.getElementById('btnViewGrid').classList.toggle('active', mode === 'grid');
            document.getElementById('btnViewList').classList.toggle('active', mode === 'list');
            document.getElementById('gridViewWrapper').style.display = mode === 'grid' ? 'block' : 'none';
            document.getElementById('listViewWrapper').style.display = mode === 'list' ? 'block' : 'none';
            renderView();
        }

        function changeSort(field) {
            sortBy = field;
            localStorage.setItem('notion_drive_sort_by', field);
            document.getElementById('sortSelect').value = field;
            sortItems();
            renderView();
            updateSortHeaders();
        }

        function toggleSortDir() {
            sortDir = sortDir === 'asc' ? 'desc' : 'asc';
            localStorage.setItem('notion_drive_sort_dir', sortDir);
            updateSortDirIcon();
            sortItems();
            renderView();
            updateSortHeaders();
        }

        function tableHeaderSort(field) {
            if (sortBy === field) {
                toggleSortDir();
            } else {
                sortDir = 'asc';
                changeSort(field);
            }
        }

        function updateSortDirIcon() {
            const icon = document.getElementById('sortDirIcon');
            icon.className = sortDir === 'asc' ? 'fa-solid fa-arrow-down-short-wide' : 'fa-solid fa-arrow-up-wide-short';
        }

        function updateSortHeaders() {
            ['name', 'type', 'mtime', 'size_bytes'].forEach(col => {
                const icon = document.getElementById(`th-icon-${col}`);
                if (!icon) return;
                if (sortBy === col) {
                    icon.className = sortDir === 'asc' ? 'fa-solid fa-arrow-up' : 'fa-solid fa-arrow-down';
                    icon.style.color = 'var(--accent-blue)';
                } else {
                    icon.className = 'fa-solid fa-sort';
                    icon.style.color = 'var(--text-muted)';
                }
            });
        }

        function sortItems() {
            const comparator = (a, b) => {
                let valA = a[sortBy];
                let valB = b[sortBy];

                if (sortBy === 'name' || sortBy === 'type') {
                    valA = (valA || '').toString().toLowerCase();
                    valB = (valB || '').toString().toLowerCase();
                    return sortDir === 'asc' ? valA.localeCompare(valB) : valB.localeCompare(valA);
                } else {
                    valA = Number(valA) || 0;
                    valB = Number(valB) || 0;
                    return sortDir === 'asc' ? valA - valB : valB - valA;
                }
            };

            driveData.folders.sort(comparator);
            driveData.files.sort(comparator);
        }

        async function fetchDrive(folderId = null) {
            currentFolderId = folderId;
            const url = folderId ? `/api/drive?folder_id=${encodeURIComponent(folderId)}` : '/api/drive';
            const res = await fetch(url);
            driveData = await res.json();
            sortItems();
            renderView();
            updateBreadcrumbs();
            fetchStorageStats();
        }

        async function fetchStorageStats() {
            const res = await fetch('/api/stats');
            const stats = await res.json();
            const usedStr = formatBytes(stats.total_mb * 1024 * 1024);
            const filesStr = (stats.total_files || 0).toLocaleString();
            document.getElementById('storage-detail').innerText = `${usedStr} Used (${filesStr} files)`;
        }

        function renderView() {
            const fGrid = document.getElementById('foldersGrid');
            const fileGrid = document.getElementById('filesGrid');
            const fSec = document.getElementById('foldersSection');
            const fileSec = document.getElementById('filesSection');
            const tableBody = document.getElementById('driveTableBody');
            const emptyMsg = document.getElementById('emptyMessage');

            fGrid.innerHTML = '';
            fileGrid.innerHTML = '';
            tableBody.innerHTML = '';

            const hasItems = driveData.folders.length > 0 || driveData.files.length > 0;
            emptyMsg.style.display = hasItems ? 'none' : 'block';

            if (viewMode === 'grid') {
                fSec.style.display = driveData.folders.length ? 'block' : 'none';
                fileSec.style.display = driveData.files.length ? 'block' : 'none';

                driveData.folders.forEach(f => {
                    const icon = getFolderIcon(f.name);
                    const card = document.createElement('div');
                    card.className = 'grid-card folder-card';
                    card.onclick = () => fetchDrive(f.id);
                    card.innerHTML = `
                        <div class="card-icon" style="color: ${icon.color};"><i class="fa-solid ${icon.cls}"></i></div>
                        <div style="flex:1; overflow:hidden;">
                            <div class="card-title" title="${f.name}">${f.name}</div>
                            <div class="card-meta">
                                <span>${f.item_count || 0} items</span>
                                <span>${formatDate(f.mtime)}</span>
                            </div>
                        </div>
                        <div class="card-actions" onclick="event.stopPropagation()">
                            <a class="action-btn" title="Download Folder ZIP" href="/download_folder?path=${encodeURIComponent(f.local_path)}"><i class="fa-solid fa-download"></i></a>
                        </div>
                    `;
                    fGrid.appendChild(card);
                });

                driveData.files.forEach(f => {
                    const card = document.createElement('div');
                    card.className = 'grid-card';
                    card.onclick = () => previewFile(f);

                    const icon = getFileIcon(f.extension);
                    card.innerHTML = `
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <div class="card-icon ${icon.type}"><i class="fa-solid ${icon.cls}"></i></div>
                        </div>
                        <div class="card-title" title="${f.name}">${f.name}</div>
                        <div class="card-meta">
                            <span>${formatBytes(f.size_bytes || f.size_mb * 1024 * 1024)}</span>
                            <span>${formatDate(f.mtime)}</span>
                        </div>
                        <div class="card-actions" onclick="event.stopPropagation()">
                            <a class="action-btn" title="Download File" href="/download?path=${encodeURIComponent(f.local_path)}"><i class="fa-solid fa-download"></i></a>
                            <a class="action-btn" title="Open in New Tab" href="/view?path=${encodeURIComponent(f.local_path)}" target="_blank"><i class="fa-solid fa-arrow-up-right-from-square"></i></a>
                        </div>
                    `;
                    fileGrid.appendChild(card);
                });
            } else {
                // Render List View (Table)
                driveData.folders.forEach(f => {
                    const icon = getFolderIcon(f.name);
                    const tr = document.createElement('tr');
                    tr.onclick = () => fetchDrive(f.id);
                    tr.innerHTML = `
                        <td>
                            <div class="td-name">
                                <i class="fa-solid ${icon.cls} td-icon" style="color: ${icon.color};"></i>
                                <span title="${f.name}">${f.name}</span>
                            </div>
                        </td>
                        <td class="td-meta">Folder</td>
                        <td class="td-meta">${formatDate(f.mtime)}</td>
                        <td class="td-meta">${f.item_count || 0} items</td>
                        <td onclick="event.stopPropagation()">
                            <div class="table-actions">
                                <a class="action-btn" title="Download Folder ZIP" href="/download_folder?path=${encodeURIComponent(f.local_path)}"><i class="fa-solid fa-download"></i></a>
                            </div>
                        </td>
                    `;
                    tableBody.appendChild(tr);
                });

                driveData.files.forEach(f => {
                    const tr = document.createElement('tr');
                    tr.onclick = () => previewFile(f);
                    const icon = getFileIcon(f.extension);
                    tr.innerHTML = `
                        <td>
                            <div class="td-name">
                                <i class="fa-solid ${icon.cls} td-icon ${icon.type}"></i>
                                <span title="${f.name}">${f.name}</span>
                            </div>
                        </td>
                        <td class="td-meta">${(f.extension || 'File').toUpperCase().replace('.', '')}</td>
                        <td class="td-meta">${formatDate(f.mtime)}</td>
                        <td class="td-meta">${formatBytes(f.size_bytes || f.size_mb * 1024 * 1024)}</td>
                        <td onclick="event.stopPropagation()">
                            <div class="table-actions">
                                <a class="action-btn" title="Download File" href="/download?path=${encodeURIComponent(f.local_path)}"><i class="fa-solid fa-download"></i></a>
                                <a class="action-btn" title="Open in New Tab" href="/view?path=${encodeURIComponent(f.local_path)}" target="_blank"><i class="fa-solid fa-arrow-up-right-from-square"></i></a>
                            </div>
                        </td>
                    `;
                    tableBody.appendChild(tr);
                });
            }
        }

        function updateBreadcrumbs() {
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
            } else if (['.mp3', '.wav', '.ogg', '.m4a'].includes(ext)) {
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

        // Init
        document.getElementById('sortSelect').value = sortBy;
        updateSortDirIcon();
        updateSortHeaders();
        setViewMode(viewMode);
        fetchDrive();
    </script>
</body>
</html>
"""

class NotionFileServerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path in ["", "/", "/drive", "/index.html"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DRIVE_GUI_HTML.encode("utf-8"))
            return

        if parsed.path == "/api/drive":
            folder_id = params.get("folder_id", [None])[0]
            if folder_id:
                folder_id = folder_id.replace("-", "")

            print(f"DEBUG: folder_id={folder_id}, root_items={DRIVE_CACHE['root_items']}, total_items={len(DRIVE_CACHE['items'])}")

            if folder_id:
                child_ids = DRIVE_CACHE["children"].get(folder_id, [])
            else:
                ROOT_DEVICE_NAMES = {"Local Disk (C:)", "Local Disk (D:)", "Internal Storage", "SD Card"}
                child_ids = [cid for cid in DRIVE_CACHE["items"] if DRIVE_CACHE["items"][cid].get("name") in ROOT_DEVICE_NAMES]
                if not child_ids:
                    child_ids = DRIVE_CACHE["root_items"]

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

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "folders": sorted(folders, key=lambda x: x["name"].lower()),
                "files": sorted(files, key=lambda x: x["name"].lower()),
                "breadcrumbs": breadcrumbs
            }).encode("utf-8"))
            return

        if parsed.path == "/api/search":
            query = params.get("q", [""])[0].lower()
            matching_files = []
            matching_folders = []
            for it in DRIVE_CACHE["items"].values():
                if query in it["name"].lower():
                    if it["type"] == "Folder":
                        matching_folders.append(it)
                    else:
                        matching_files.append(it)

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

        # Extract file path from various parameter names (path, file, p, url, target)
        file_path_str = (params.get("path", [None])[0] or 
                         params.get("file", [None])[0] or 
                         params.get("p", [None])[0] or 
                         params.get("url", [None])[0] or 
                         params.get("target", [None])[0])

        if not file_path_str:
            # If no path parameter provided, redirect gracefully to Web GUI
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        # Clean parameter
        clean_path_str = urllib.parse.unquote(file_path_str).replace("Local: ", "").replace("Path: ", "").strip()
        target_path = Path(clean_path_str).resolve()

        if not target_path.exists():
            # Show friendly 404 page with drive link
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
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("Content-Disposition", f"attachment; filename=\"{target_path.name}\"")
                    self.end_headers()
                    self.wfile.write(content)
                    return
                except Exception as e:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(str(e).encode("utf-8"))
                    return

        if parsed.path == "/download_folder" or (parsed.path == "/download" and target_path.is_dir()):
            if target_path.is_dir():
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_STORED) as zf:
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
                return

        if parsed.path in ["/view", "/open"]:
            ext = target_path.suffix.lower()

            if ext == ".pdf":
                mime_type = "application/pdf"
            elif ext == ".svg":
                mime_type = "image/svg+xml"
            elif ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp"]:
                mime_type = f"image/{'jpeg' if ext in ['.jpg', '.jpeg'] else ext.lstrip('.')}"
            elif ext in [".mp4", ".webm", ".mkv"]:
                mime_type = f"video/{'mp4' if ext == '.mp4' else 'webm'}"
            elif ext in [".mp3", ".wav", ".ogg", ".m4a"]:
                mime_type = f"audio/{'mpeg' if ext == '.mp3' else ext.lstrip('.')}"
            elif ext in [".html", ".htm"]:
                mime_type = "text/html; charset=utf-8"
            elif ext in [".txt", ".md", ".json", ".py", ".js", ".ts", ".css", ".yaml", ".yml", ".sql", ".sh", ".ps1", ".bat", ".c", ".cpp", ".java", ".log", ".csv"]:
                mime_type = "text/plain; charset=utf-8"
            else:
                guessed, _ = mimetypes.guess_type(str(target_path))
                mime_type = guessed or "application/octet-stream"

            try:
                with open(target_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Content-Disposition", "inline")
                self.end_headers()
                self.wfile.write(content)
                return
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
                return


def run_server():
    load_disk_cache()
    threading.Thread(target=populate_cache_from_notion, daemon=True).start()
    server_address = ("0.0.0.0", PORT)
    httpd = ThreadingHTTPServer(server_address, NotionFileServerHandler)
    print(f"🚀 Google Drive Web GUI active on http://127.0.0.1:{PORT}")
    httpd.serve_forever()


if __name__ == "__main__":
    run_server()
