"""
notion_sync.py — Main entry point for Notion Unlimited Cloud.

Run this script to:
  - Auto-discover all connected storage devices
  - Select which folders/devices to sync to Notion
  - Run a Git-style incremental sync (only new/changed files uploaded)
  - Monitor sync status live
  - Launch the Web Drive GUI

Usage:
    python notion_sync.py                  # Interactive menu (recommended)
    python notion_sync.py status           # Show git-status of all sources
    python notion_sync.py sync --path C:\\Users\\nitro\\Documents
    python notion_sync.py watch --path C:\\Users\\nitro
    python notion_sync.py gui              # Open web browser at http://127.0.0.1:8765
    python notion_sync.py rebuild          # Rebuild local index from Notion
"""

import argparse
import json
import os
import sys
import time
import threading
import webbrowser
import urllib.request
from pathlib import Path
from typing import Any, List, Optional

# ── Ensure the project root is on sys.path ────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import config as cfg
from core import state as S
from core import storage as STOR
from core import sync_engine as ENGINE
from core.notion_api import NotionAPI
from core.storage import StorageDevice, get_user_subfolders


# ─────────────────────────────────────────────────────────────────────────────
# Guard: first-run setup
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_credentials():
    """If no .env credentials exist, run the setup wizard first."""
    cfg.reload()
    if not cfg.has_credentials():
        print("\n  ⚠️  No Notion credentials found.")
        print("  Running first-time setup wizard...\n")
        import setup as SETUP
        ok = SETUP.run_setup()
        if not ok:
            print("\n❌ Setup incomplete. Please run 'python setup.py' to configure.")
            sys.exit(1)
        cfg.reload()


# ─────────────────────────────────────────────────────────────────────────────
# Local web server (background)
# ─────────────────────────────────────────────────────────────────────────────

_server_instance = None

def start_web_server(port: int = None) -> bool:
    """Start notion_server.py as a background daemon thread. Returns True if running."""
    port = port or cfg.LOCAL_SERVER_PORT
    # Already running?
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/stats", timeout=1)
        return True
    except Exception:
        pass

    try:
        import notion_server
        from http.server import ThreadingHTTPServer
        # Load disk cache first (populates from Notion if needed)
        if hasattr(notion_server, "load_disk_cache"):
            notion_server.load_disk_cache()
        # Use the correct handler class name from notion_server.py
        handler_cls = getattr(
            notion_server,
            "NotionServerHandler",        # current name
            getattr(notion_server, "NotionFileServerHandler", None),  # legacy fallback
        )
        if handler_cls is None:
            raise AttributeError("Cannot find request handler class in notion_server.py")
        srv = ThreadingHTTPServer(("0.0.0.0", port), handler_cls)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        global _server_instance
        _server_instance = srv
        time.sleep(0.5)
        print(f"  🚀 Web Drive GUI started → http://127.0.0.1:{port}")
        return True
    except Exception as e:
        print(f"  ⚠️  Could not start web server: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Progress bar (CLI)
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Progress bar (CLI & Web Live Bridge)
# ─────────────────────────────────────────────────────────────────────────────

_SYNC_START_TIME = None
_BYTES_TRANSFERRED = 0

def notify_server(payload: dict):
    """Notify the local Web Drive server so browser GUI updates in real time."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{cfg.LOCAL_SERVER_PORT}/api/sync/update",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=0.5) as res:
            pass
    except Exception as e:
        # Fallback print if server error occurs
        pass


def refresh_server_cache_once() -> None:
    """Ask the Web Drive server to refresh cache so synced files appear immediately."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{cfg.LOCAL_SERVER_PORT}/api/refresh", timeout=2.5):
            pass
    except Exception:
        pass


def _progress(current: int, total: int, item: Any, tag: str):
    global _SYNC_START_TIME, _BYTES_TRANSFERRED
    if _SYNC_START_TIME is None:
        _SYNC_START_TIME = time.time()
        _BYTES_TRANSFERRED = 0

    if total == 0:
        return
    bar_len = 28
    pct = current / total if total else 0
    filled = int(bar_len * pct)
    bar = "█" * filled + "░" * (bar_len - filled)
    fname = item.name if (item and hasattr(item, "name")) else (str(item) if item else "")
    short = (fname[:22] + "…") if len(fname) > 23 else fname
    tag_str = f"[{tag}]" if tag and tag != "DONE" else ""
    sys.stdout.write(
        f"\r  |{bar}| {int(pct*100):3d}% ({current}/{total}) {tag_str} {short:<23}  "
    )
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")
        sys.stdout.flush()

    elapsed = max(0.1, time.time() - _SYNC_START_TIME)
    if item and hasattr(item, "size"):
        _BYTES_TRANSFERRED += item.size

    speed_files_per_min = round((current / elapsed) * 60, 1) if current > 0 else 0
    speed_mb_s = round((_BYTES_TRANSFERRED / elapsed) / 1e6, 2)
    speed_str = f"{speed_files_per_min} files/min ({speed_mb_s} MB/s)"

    size_str = f"{item.size / 1e6:.2f} MB" if (item and hasattr(item, "size")) else "-"
    disp_path = item.display_path if (item and hasattr(item, "display_path")) else (item.path if (item and hasattr(item, "path")) else "")

    notify_server({
        "is_running": current < total,
        "percent": int(pct * 100),
        "synced_files": current,
        "total_files": total,
        "remaining_files": max(0, total - current),
        "current_file": fname if current < total else "Completed",
        "current_path": disp_path,
        "current_size_str": size_str,
        "speed_str": speed_str,
        "status_message": f"Syncing {current+1}/{total} changes via Terminal..." if current < total else f"All {total} files synchronized from Terminal!",
        "log_message": f"Synced: {fname} [{tag}]" if fname and current < total else None,
        "history_item": {
            "name": fname,
            "path": disp_path,
            "size_str": size_str,
            "time": time.strftime("%H:%M:%S"),
            "status": "success"
        } if (fname and current > 0) else None
    })


# ─────────────────────────────────────────────────────────────────────────────
# SYNC — local directory
# ─────────────────────────────────────────────────────────────────────────────

def sync_local(path: str, force: bool = False):
    """Scan a local directory and sync new/modified files to Notion."""
    _ensure_credentials()
    api = NotionAPI(cfg.NOTION_TOKEN, cfg.NOTION_DATABASE_ID)
    state = S.load_state()

    notify_server({
        "is_running": True,
        "current_target": f"Local: {path}",
        "status_message": f"Scanning {path}...",
        "percent": 0,
        "synced_files": 0,
        "total_files": 0,
        "current_file": "Scanning filesystem..."
    })

    print(f"\n  🔍 Scanning: {path}")
    print(f"  🔗 Preloading Notion folder cache…")
    api.preload_folders()

    # ── Step 1: Scan and sync folders first ──────────────────────────────────
    print(f"  📁 Scanning folders…")
    all_folders = list(ENGINE.scan_folders(path))
    print(f"  📂 Found {len(all_folders):,} folders")

    if force:
        # Mark everything as new for force-upload
        for folder in all_folders:
            folder.status_tag = "NEW"
        folders_to_sync = all_folders
        folders_skipped = 0
    else:
        folders_to_sync, folders_skipped = ENGINE.compute_folder_diff(all_folders, state)

    folders_new = sum(1 for f in folders_to_sync if f.status_tag == "NEW")
    folders_mod = sum(1 for f in folders_to_sync if f.status_tag == "MODIFIED")

    print()
    print(f"  ✅ {folders_skipped:,} folders up-to-date (skipped)")
    print(f"  🟢 {folders_new:,} new folders to create")
    print(f"  🟡 {folders_mod:,} modified folders to update")
    print()

    # Sync folders first (so hierarchy exists before files are uploaded)
    if folders_to_sync:
        print(f"  ⚡ Syncing {len(folders_to_sync):,} folders…\n")
        folder_result = ENGINE.run_folder_sync(
            api, state, folders_to_sync,
            on_progress=_progress,
        )
        print(f"\n  ✅ Folder sync complete!")
        print(f"     Created : {folder_result.uploaded:,}")
        print(f"     Updated : {folder_result.updated:,}")
        if folder_result.failed:
            print(f"     ❌ Failed : {folder_result.failed:,}")
        print()

    # ── Step 2: Scan and sync files ──────────────────────────────────────────
    all_items = list(ENGINE.scan_local(path))
    print(f"  📂 Found {len(all_items):,} files")

    if force:
        # Mark everything as new for force-upload
        for item in all_items:
            item.status_tag = "NEW"
        to_sync = all_items
        skipped = 0
    else:
        to_sync, skipped = ENGINE.compute_diff(all_items, state)

    new_c = sum(1 for i in to_sync if i.status_tag == "NEW")
    mod_c = sum(1 for i in to_sync if i.status_tag == "MODIFIED")

    queue_items = [{
        "name": it.name,
        "path": it.display_path or it.path,
        "size_str": f"{it.size / 1e6:.2f} MB",
        "tag": it.status_tag,
        "status": "pending"
    } for it in to_sync[:80]]

    notify_server({
        "is_running": len(to_sync) > 0,
        "current_target": f"Local: {path}",
        "total_files": len(to_sync),
        "remaining_files": len(to_sync),
        "synced_files": 0,
        "percent": 0,
        "queue": queue_items,
        "status_message": f"Found {len(to_sync)} changes to sync" if to_sync else "Everything up to date!"
    })

    print()
    print(f"  ✅ {skipped:,} files up-to-date (skipped)")
    print(f"  🟢 {new_c:,} new files to upload")
    print(f"  🟡 {mod_c:,} modified files to update")
    print()

    all_scanned_paths = {it.path for it in all_items}
    norm_path = path.replace("\\", "/").lower().rstrip("/")
    missing_count = sum(
        1 for p in state.get("files", {})
        if p.replace("\\", "/").lower().startswith(norm_path) and p not in all_scanned_paths and not Path(p).exists()
    )

    if not to_sync and not folders_to_sync and missing_count == 0:
        print("  ✨ Everything is already in sync! Nothing to upload.")
        notify_server({
            "is_running": False,
            "percent": 100,
            "status_message": "Everything is already in sync!",
            "log_message": f"Scan completed for {path}: 0 changes needed."
        })
        return

    global _SYNC_START_TIME
    _SYNC_START_TIME = time.time()

    if to_sync or missing_count > 0:
        if to_sync:
            print(f"  ⚡ Uploading {len(to_sync):,} files…\n")
        elif missing_count > 0:
            print(f"  🗑️  Removing {missing_count:,} deleted files from Notion…\n")

        result = ENGINE.run_sync(
            api, state, to_sync,
            on_progress=_progress,
            server_port=cfg.LOCAL_SERVER_PORT,
            delete_missing=True,
            all_scanned_paths=all_scanned_paths,
            root_path=path,
        )

        notify_server({
            "is_running": False,
            "percent": 100,
            "synced_files": len(to_sync),
            "remaining_files": 0,
            "current_file": "Sync Complete",
            "status_message": f"Sync finished! {result.uploaded} uploaded, {result.updated} updated, {result.deleted} deleted.",
            "log_message": f"Sync completed for {path}: {result.uploaded} uploaded, {result.updated} updated, {result.deleted} deleted."
        })
        refresh_server_cache_once()

        print(f"\n  ✅ Sync complete!")
        print(f"     Uploaded : {result.uploaded:,}")
        print(f"     Updated  : {result.updated:,}")
        print(f"     Skipped  : {skipped:,}")
        if result.deleted:
            print(f"     🗑️  Deleted : {result.deleted:,}")
        if result.failed:
            print(f"     ❌ Failed : {result.failed:,}")
    else:
        print("  ✨ No file changes to sync.")


# ─────────────────────────────────────────────────────────────────────────────
# SYNC — Android device
# ─────────────────────────────────────────────────────────────────────────────

def sync_android(device: StorageDevice):
    """Sync an Android Internal Storage or SD Card to Notion via ADB."""
    _ensure_credentials()

    if device.device_type == "android_internal":
        win_label = f"This PC\\{device.device_model}\\Internal shared storage"
        container_emoji = "📱"
    else:
        win_label = f"This PC\\{device.device_model}\\SD card"
        container_emoji = "💾"

    api = NotionAPI(cfg.NOTION_TOKEN, cfg.NOTION_DATABASE_ID)
    state = S.load_state()

    notify_server({
        "is_running": True,
        "current_target": device.label,
        "status_message": f"Scanning {device.label} via ADB…",
        "percent": 0,
        "synced_files": 0,
        "total_files": 0,
        "current_file": "Scanning device via ADB..."
    })

    print(f"\n  📱 Scanning {device.label} via ADB…")
    print(f"  🔗 Preloading Notion folder cache…")
    api.preload_folders()

    # ── Step 1: Scan and sync folders first (including empty ones) ───────────
    print(f"  📁 Scanning folders on device…")
    all_folders = list(ENGINE.scan_android_folders(
        device.adb_device_id, device.adb_path, win_label
    ))
    print(f"  📂 Found {len(all_folders):,} folders on device")

    folders_to_sync, folders_skipped = ENGINE.compute_folder_diff(all_folders, state)
    folders_new = sum(1 for f in folders_to_sync if f.status_tag == "NEW")
    folders_mod = sum(1 for f in folders_to_sync if f.status_tag == "MODIFIED")

    if folders_to_sync:
        print()
        print(f"  ✅ {folders_skipped:,} folders up-to-date (skipped)")
        print(f"  🟢 {folders_new:,} new folders to create")
        print(f"  🟡 {folders_mod:,} modified folders to update")
        print()
        print(f"  ⚡ Syncing {len(folders_to_sync):,} folders…\n")
        folder_result = ENGINE.run_folder_sync(
            api, state, folders_to_sync,
            on_progress=_progress,
            adb_root=device.adb_path,
            container_name="Internal shared storage" if device.device_type == "android_internal" else "SD card",
            container_emoji=container_emoji,
        )
        print(f"\n  ✅ Folder sync complete!")
        print(f"     Created : {folder_result.uploaded:,}")
        print(f"     Updated : {folder_result.updated:,}")
        if folder_result.failed:
            print(f"     ❌ Failed : {folder_result.failed:,}")
        print()

    # ── Step 2: Scan and sync files ──────────────────────────────────────────
    print(f"  🔍 Reading file list from device…")
    all_items = list(ENGINE.scan_android(
        device.adb_device_id, device.adb_path, win_label
    ))
    print(f"  📂 Found {len(all_items):,} files on device")

    to_sync, skipped = ENGINE.compute_diff(all_items, state)
    new_c = sum(1 for i in to_sync if i.status_tag == "NEW")
    mod_c = sum(1 for i in to_sync if i.status_tag == "MODIFIED")

    queue_items = [{
        "name": it.name,
        "path": it.display_path or it.path,
        "size_str": f"{it.size / 1e6:.2f} MB",
        "tag": it.status_tag,
        "status": "pending"
    } for it in to_sync[:80]]

    notify_server({
        "is_running": len(to_sync) > 0,
        "current_target": device.label,
        "total_files": len(to_sync),
        "remaining_files": len(to_sync),
        "synced_files": 0,
        "percent": 0,
        "queue": queue_items,
        "status_message": f"Found {len(to_sync)} changes to sync" if to_sync else "Everything up to date!"
    })

    print()
    print(f"  ✅ {skipped:,} files up-to-date (skipped)")
    print(f"  🟢 {new_c:,} new files to upload")
    print(f"  🟡 {mod_c:,} modified files to update")
    print()

    all_scanned_paths = {it.path for it in all_items}
    norm_root = device.adb_path.replace("\\", "/").lower().rstrip("/")
    missing_count = sum(
        1 for p in state.get("android_files", {})
        if p.replace("\\", "/").lower().startswith(norm_root) and p not in all_scanned_paths
    )

    if not to_sync and not folders_to_sync and missing_count == 0:
        print("  ✨ Everything is already in sync! Nothing to upload.")
        notify_server({
            "is_running": False,
            "percent": 100,
            "status_message": "Everything is already in sync!",
            "log_message": f"Scan completed for {device.label}: 0 changes needed."
        })
        return

    global _SYNC_START_TIME
    _SYNC_START_TIME = time.time()

    if to_sync:
        print(f"  ⚡ Uploading {len(to_sync):,} files…\n")
    elif missing_count > 0:
        print(f"  🗑️  Removing {missing_count:,} deleted files from Notion…\n")

    result = ENGINE.run_sync(
        api, state, to_sync,
        on_progress=_progress,
        server_port=cfg.LOCAL_SERVER_PORT,
        adb_root=device.adb_path,
        container_name="Internal shared storage" if device.device_type == "android_internal" else "SD card",
        container_emoji=container_emoji,
        delete_missing=True,
        all_scanned_paths=all_scanned_paths,
        root_path=device.adb_path,
    )

    notify_server({
        "is_running": False,
        "percent": 100,
        "synced_files": len(to_sync),
        "remaining_files": 0,
        "current_file": "Sync Complete",
        "status_message": f"Sync finished! {result.uploaded} uploaded, {result.updated} updated, {result.deleted} deleted.",
        "log_message": f"Sync completed for {device.label}: {result.uploaded} uploaded, {result.updated} updated, {result.deleted} deleted."
    })
    refresh_server_cache_once()

    print(f"\n  ✅ Android sync complete!")
    print(f"     Uploaded : {result.uploaded:,}")
    print(f"     Updated  : {result.updated:,}")
    print(f"     Skipped  : {skipped:,}")
    if result.deleted:
        print(f"     🗑️  Deleted : {result.deleted:,}")
    if result.failed:
        print(f"     ❌ Failed : {result.failed:,}")


# ─────────────────────────────────────────────────────────────────────────────
# STATUS (git status equivalent)
# ─────────────────────────────────────────────────────────────────────────────

def show_status(path: str):
    """Print a git-style status summary for a local directory."""
    _ensure_credentials()
    state = S.load_state()

    print(f"\n  🔍 Scanning: {path}")
    all_items = list(ENGINE.scan_local(path))
    to_sync, skipped = ENGINE.compute_diff(all_items, state)

    new_c = sum(1 for i in to_sync if i.status_tag == "NEW")
    mod_c = sum(1 for i in to_sync if i.status_tag == "MODIFIED")
    total_bytes = sum(i.size for i in all_items)

    print()
    print("=" * 60)
    print("  📊 NOTION DRIVE — GIT STATUS")
    print("=" * 60)
    print(f"  📁 Total local files  : {len(all_items):,}")
    print(f"  💾 Total size         : {total_bytes / 1e9:.2f} GB")
    print(f"  ⚪ Up-to-date         : {skipped:,}  (nothing to do)")
    print(f"  🟢 New (not in Notion): {new_c:,}")
    print(f"  🟡 Modified           : {mod_c:,}")
    print("=" * 60)

    if to_sync[:10]:
        print(f"\n  Changes ({min(len(to_sync), 10)} of {len(to_sync)} shown):")
        for item in to_sync[:10]:
            tag = "+" if item.status_tag == "NEW" else "*"
            print(f"    {tag} {item.path}")
        if len(to_sync) > 10:
            print(f"    … and {len(to_sync) - 10} more")
    else:
        print("\n  ✨ Everything is already in Notion. No pending changes.")


# ─────────────────────────────────────────────────────────────────────────────
# REBUILD — rebuild local index from Notion
# ─────────────────────────────────────────────────────────────────────────────

def rebuild_index():
    """Pull all pages from Notion and reconstruct the local state index."""
    _ensure_credentials()
    api = NotionAPI(cfg.NOTION_TOKEN, cfg.NOTION_DATABASE_ID)
    state = S.load_state()

    print("\n  🔄 Rebuilding local index from Notion database…")
    count = 0
    for item in api.query_all():
        props = item.get("properties", {})
        desc_rt = props.get("Description", {}).get("rich_text", [])
        desc = desc_rt[0].get("plain_text", "") if desc_rt else ""
        local_path = (
            desc.replace("Path: ", "").replace("Local: ", "")
               .replace(" (Updated)", "").replace(" (Modified)", "").strip()
        )
        if local_path and Path(local_path).is_file():
            try:
                st = Path(local_path).stat()
                S.record_file(
                    state, local_path, item["id"].replace("-", ""),
                    st.st_mtime, st.st_size, android=False,
                )
                count += 1
            except Exception:
                pass

    S.save_state(state)
    print(f"  ✅ Rebuilt index with {count:,} local files mapped to Notion.")


# ─────────────────────────────────────────────────────────────────────────────
# WATCH — real-time file watcher
# ─────────────────────────────────────────────────────────────────────────────

def watch(path: str, interval: int = None):
    """Continuously watch a directory and auto-upload changes."""
    _ensure_credentials()
    interval = interval or cfg.POLL_INTERVAL
    print(f"\n  👀 Live Watcher active on: {path}")
    print(f"  ⏱️  Polling every {interval}s. Press Ctrl+C to stop.\n")

    while True:
        try:
            state = S.load_state()
            all_items = list(ENGINE.scan_local(path))
            to_sync, _ = ENGINE.compute_diff(all_items, state)
            if to_sync:
                ts = time.strftime("%H:%M:%S")
                print(f"\n  ⚡ [{ts}] {len(to_sync)} change(s) detected")
                for item in to_sync[:5]:
                    print(f"     {'➕' if item.status_tag == 'NEW' else '🔄'} {item.name}")
                if len(to_sync) > 5:
                    print(f"     … and {len(to_sync) - 5} more")
                sync_local(path)
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n  🛑 Watcher stopped.")
            break


# ─────────────────────────────────────────────────────────────────────────────
# INTERACTIVE DEVICE SELECTOR MENU
# ─────────────────────────────────────────────────────────────────────────────

def _print_banner():
    print()
    print("╔" + "═" * 63 + "╗")
    print("║  ☁️   NOTION UNLIMITED CLOUD & WEB DRIVE                      ║")
    print("║  Your personal unlimited cloud — powered by Notion API        ║")
    print("╚" + "═" * 63 + "╝")
    print()


def _menu_devices(devices: List[StorageDevice]) -> Optional[str]:
    """Show device list and return the user's choice (index string or command)."""
    print("  Select a storage source to sync:\n")

    local_devices = [d for d in devices if not d.is_android]
    android_devices = [d for d in devices if d.is_android]

    idx = 1
    device_map = {}  # str(idx) → StorageDevice or action string

    if local_devices:
        print("  ── LOCAL DRIVES ─────────────────────────────────────────")
        for dev in local_devices:
            print(f"    [{idx}] {dev.emoji}  {dev.label:<35} {dev.size_str}")
            device_map[str(idx)] = dev
            idx += 1

    if android_devices:
        print()
        adb_model = android_devices[0].device_model or "Android"
        print(f"  ── ANDROID ({adb_model} — connected via USB) ──────────────")
        for dev in android_devices:
            print(f"    [{idx}] {dev.emoji}  {dev.label:<35}")
            device_map[str(idx)] = dev
            idx += 1
    elif not android_devices:
        print()
        print("  ── ANDROID ──────────────────────────────────────────────")
        print("    (No Android device detected via ADB USB)")
        print("    To connect: enable USB Debugging, plug in via USB, tap 'Allow'")

    print()
    print("  ── OTHER OPTIONS ────────────────────────────────────────────")
    print(f"    [g] 🌐  Open Web Drive GUI (http://127.0.0.1:{cfg.LOCAL_SERVER_PORT})")
    print(f"    [s] 📊  Show Sync Status (git status view)")
    print(f"    [r] 🔄  Rebuild index from Notion")
    print(f"    [n] 📝  Open Notion database in browser")
    print(f"    [q] ❌  Exit")
    print()

    device_map["g"] = "gui"
    device_map["s"] = "status"
    device_map["r"] = "rebuild"
    device_map["n"] = "notion"
    device_map["q"] = "quit"

    choice = input("  Enter your choice: ").strip().lower()
    return device_map.get(choice)


def _menu_subfolders(device: StorageDevice) -> Optional[str]:
    """Show folder selection for a local drive. Returns the selected path."""
    print(f"\n  📁 Choose what to sync from {device.label}:\n")

    subfolders = get_user_subfolders(device)
    idx_map = {}
    for i, sf in enumerate(subfolders, 1):
        count_hint = ""
        print(f"    [{i}] {sf['emoji']}  {sf['name']}")
        idx_map[str(i)] = sf

    print(f"    [b] ↩  Back to device list")
    print()

    choice = input("  Select folder: ").strip().lower()
    if choice == "b":
        return None

    sf = idx_map.get(choice)
    if not sf:
        return None

    if sf["path"] == "__custom__":
        custom = input(f"  Enter full path (e.g. {device.path}Documents): ").strip()
        return custom if custom else None

    return sf["path"]


def interactive_menu():
    """The main interactive loop shown when user just runs `python notion_sync.py`."""
    _ensure_credentials()
    start_web_server()
    _print_banner()

    while True:
        print("  🔍 Detecting connected storage devices…")
        devices = STOR.discover_all()
        print(f"  ✅ {len(devices)} device(s) found\n")

        action = _menu_devices(devices)

        if action is None:
            print("  ⚠️  Invalid choice. Please try again.")
            continue

        elif action == "quit":
            print("\n  👋 Goodbye!\n")
            break

        elif action == "gui":
            start_web_server()
            webbrowser.open(f"http://127.0.0.1:{cfg.LOCAL_SERVER_PORT}")
            print(f"\n  🌐 Web Drive opened in browser.\n")

        elif action == "status":
            path = input("\n  Enter path to check (or press Enter for home directory): ").strip()
            show_status(path or str(Path.home()))

        elif action == "rebuild":
            rebuild_index()

        elif action == "notion":
            url = f"https://app.notion.com/p/{cfg.NOTION_DATABASE_ID}"
            webbrowser.open(url)
            print(f"\n  🌐 Opened Notion database: {url}\n")

        elif isinstance(action, StorageDevice):
            device = action
            if device.is_android:
                # Confirm and sync
                print(f"\n  📱 Selected: {device.label}")
                confirm = input("  Start syncing? [Y/n]: ").strip().lower()
                if confirm not in ("n", "no"):
                    sync_android(device)
            else:
                # Direct sync without choosing subfolders
                target_path = device.path
                if device.path.upper().startswith("C:") and Path(r"C:\Users").exists():
                    target_path = r"C:\Users"

                print(f"\n  💽 Selected: {device.label} ({target_path})")
                confirm = input("  Start syncing? [Y/n]: ").strip().lower()
                if confirm not in ("n", "no"):
                    sync_local(target_path)

        print()  # spacing before next menu iteration


# ─────────────────────────────────────────────────────────────────────────────
# CLI argument parser (for power users)
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # Windows UTF-8 fix
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if len(sys.argv) == 1:
        interactive_menu()
        return

    parser = argparse.ArgumentParser(
        prog="notion_sync",
        description="Notion Unlimited Cloud — sync any storage device to Notion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  (no args)    Interactive device selector (recommended)
  status       Show git-style status for a path
  sync         Sync a specific path to Notion (incremental)
  sync-all     Force re-upload all files in a path
  watch        Auto-sync a path in real time
  gui          Open the Web Drive GUI in browser
  rebuild      Rebuild local index from Notion
        """,
    )
    parser.add_argument(
        "command", nargs="?", default="menu",
        choices=["menu", "status", "sync", "sync-all", "watch", "gui", "rebuild"],
    )
    parser.add_argument("--path", default=str(Path.home()), help="Path to sync")
    parser.add_argument("--interval", type=int, default=None, help="Watch interval in seconds")
    args = parser.parse_args()

    if args.command in ("menu", None):
        interactive_menu()
    elif args.command == "status":
        show_status(args.path)
    elif args.command == "sync":
        sync_local(args.path, force=False)
    elif args.command == "sync-all":
        sync_local(args.path, force=True)
    elif args.command == "watch":
        watch(args.path, args.interval)
    elif args.command == "gui":
        start_web_server()
        webbrowser.open(f"http://127.0.0.1:{cfg.LOCAL_SERVER_PORT}")
    elif args.command == "rebuild":
        rebuild_index()


if __name__ == "__main__":
    main()
