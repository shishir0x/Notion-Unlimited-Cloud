"""
notion_sync.py — Main CLI entry point for Notion Unlimited Cloud.

Run this script to:
  - Auto-discover all connected storage devices (Local drives, Android USB ADB)
  - Run Git-style incremental sync (only new/changed files uploaded)
  - Monitor live CLI progress in the terminal
  - Check status, watch folders, or rebuild local state index

Usage:
    python notion_sync.py                  # Interactive device selector menu
    python notion_sync.py status           # Show git-style status of source
    python notion_sync.py sync --path C:\\Users\\nitro\\Documents
    python notion_sync.py sync-all --path C:\\Users\\nitro\\Documents
    python notion_sync.py watch --path C:\\Users\\nitro
    python notion_sync.py rebuild          # Rebuild local index from Notion
"""

import argparse
import os
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, List, Optional

# ── Ensure the project root is on sys.path ────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import config as cfg
from core import state as S
from core import storage as STOR
from core import sync_engine as ENGINE
from core.notion_api import NotionAPI, is_page_archived
from core.storage import StorageDevice


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
# Progress bar (CLI)
# ─────────────────────────────────────────────────────────────────────────────

_SYNC_START_TIME = None
_BYTES_TRANSFERRED = 0


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


# ─────────────────────────────────────────────────────────────────────────────
# SYNC — local directory
# ─────────────────────────────────────────────────────────────────────────────

def sync_local(path: str, force: bool = False):
    """Scan a local directory and sync new/modified files to Notion."""
    _ensure_credentials()
    api = NotionAPI(cfg.NOTION_TOKEN, cfg.NOTION_DATABASE_ID)
    state = S.load_state()

    print(f"\n  🔍 Scanning: {path}")
    print(f"  🔗 Preloading Notion folder cache…")
    api.preload_folders()

    # ── Step 1: Scan and sync folders first ──────────────────────────────────
    print(f"  📁 Scanning folders…")
    all_folders = list(ENGINE.scan_folders(path))
    print(f"  📂 Found {len(all_folders):,} folders")

    if force:
        for folder in all_folders:
            folder.status_tag = "NEW"
        folders_to_sync = all_folders
        folders_skipped = 0
    else:
        folders_to_sync, folders_skipped = ENGINE.compute_folder_diff(all_folders, state)

    folders_new = sum(1 for f in folders_to_sync if f.status_tag == "NEW")

    print()
    print(f"  ✅ {folders_skipped:,} folders up-to-date (skipped)")
    print(f"  🟢 {folders_new:,} new folders to create")
    print()

    if folders_to_sync:
        print(f"  ⚡ Syncing {len(folders_to_sync):,} folders…\n")
        folder_result = ENGINE.run_folder_sync(
            api, state, folders_to_sync,
            on_progress=_progress,
        )
        print(f"\n  ✅ Folder sync complete!")
        print(f"     Created : {folder_result.uploaded:,}")
        if folder_result.failed:
            print(f"     ❌ Failed : {folder_result.failed:,}")
        print()

    # ── Step 2: Scan and sync files ──────────────────────────────────────────
    all_items = list(ENGINE.scan_local(path))
    print(f"  📂 Found {len(all_items):,} files")

    if force:
        for item in all_items:
            item.status_tag = "NEW"
        to_sync = all_items
        skipped = 0
    else:
        to_sync, skipped = ENGINE.compute_diff(all_items, state)

    new_c = sum(1 for i in to_sync if i.status_tag == "NEW")
    mod_c = sum(1 for i in to_sync if i.status_tag == "MODIFIED")

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
            delete_missing=True,
            all_scanned_paths=all_scanned_paths,
            root_path=path,
        )

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

    if folders_to_sync:
        print()
        print(f"  ✅ {folders_skipped:,} folders up-to-date (skipped)")
        print(f"  🟢 {folders_new:,} new folders to create")
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
        adb_root=device.adb_path,
        container_name="Internal shared storage" if device.device_type == "android_internal" else "SD card",
        container_emoji=container_emoji,
        delete_missing=True,
        all_scanned_paths=all_scanned_paths,
        root_path=device.adb_path,
    )

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
        if is_page_archived(props):
            continue
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
    print("║  ☁️   NOTION UNLIMITED CLOUD — TERMINAL SYNC ENGINE           ║")
    print("║  Fast Git-style incremental sync to your Notion database      ║")
    print("╚" + "═" * 63 + "╝")
    print()


def _menu_devices(devices: List[StorageDevice]) -> Optional[str]:
    """Show device list and return the user's choice (index string or command)."""
    print("  Select a storage source to sync:\n")

    local_devices = [d for d in devices if not d.is_android]
    android_devices = [d for d in devices if d.is_android]

    idx = 1
    device_map = {}

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
    print("  ── ACTIONS & TOOLS ──────────────────────────────────────────")
    print(f"    [c] 📁  Sync Custom Folder Path")
    print(f"    [w] 👀  Watch Folder (Real-time Auto-Sync)")
    print(f"    [s] 📊  Show Git-Style Sync Status")
    print(f"    [r] 🔄  Rebuild Local Index from Notion")
    print(f"    [n] 📝  Open Notion Database in Browser")
    print(f"    [q] ❌  Exit")
    print()

    device_map["c"] = "custom"
    device_map["w"] = "watch"
    device_map["s"] = "status"
    device_map["r"] = "rebuild"
    device_map["n"] = "notion"
    device_map["q"] = "quit"

    choice = input("  Enter your choice: ").strip().lower()
    return device_map.get(choice)


def interactive_menu():
    """The main interactive loop shown when user runs `python notion_sync.py`."""
    _ensure_credentials()
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

        elif action == "custom":
            custom_path = input("\n  Enter full directory path to sync: ").strip().strip('"').strip("'")
            if custom_path and Path(custom_path).exists():
                sync_local(custom_path)
            elif custom_path:
                print(f"  ❌ Directory not found: {custom_path}")

        elif action == "watch":
            watch_path = input("\n  Enter directory path to watch (Enter for home): ").strip().strip('"').strip("'")
            target = watch_path if (watch_path and Path(watch_path).exists()) else str(Path.home())
            watch(target)

        elif action == "status":
            path = input("\n  Enter path to check (or press Enter for home directory): ").strip().strip('"').strip("'")
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
                print(f"\n  📱 Selected: {device.label}")
                confirm = input("  Start syncing? [Y/n]: ").strip().lower()
                if confirm not in ("n", "no"):
                    sync_android(device)
            else:
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
  rebuild      Rebuild local index from Notion
        """,
    )
    parser.add_argument(
        "command", nargs="?", default="menu",
        choices=["menu", "status", "sync", "sync-all", "watch", "rebuild"],
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
    elif args.command == "rebuild":
        rebuild_index()


if __name__ == "__main__":
    main()
