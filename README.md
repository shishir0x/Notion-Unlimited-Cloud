# ☁️ Notion Unlimited Cloud & Web Drive

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Notion API](https://img.shields.io/badge/Notion%20API-v2022--06--28-black.svg)](https://developers.notion.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform: Windows · macOS · Linux](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-0078D6.svg)](https://github.com/shishir0x/Notion-Unlimited-Cloud)

> Turn your **Notion database** into an unlimited cloud drive with a Google Drive-style web interface, Git-style incremental sync, real-time file watcher, and universal storage device support (local drives + Android via ADB).

---

## ✨ What It Does

| Feature | Description |
|---|---|
| 🔍 **Auto Device Discovery** | Detects all connected drives (C:, D:, USB sticks) and Android phones via ADB automatically — no hardcoding |
| 📁 **Folder Selector** | Interactive menu to choose exactly which folders to upload |
| ⚡ **Git-Style Incremental Sync** | Tracks file `mtime` + `size` — only uploads new or changed files. Skips unchanged files instantly with 0 API calls |
| 🔄 **No Duplicates** | Modified files are updated in-place via Notion PATCH (no duplicate rows ever created) |
| 🌐 **Web Drive GUI** | Modern Google Drive-style web app at `http://127.0.0.1:8765` — browse, search, preview, and download files |
| 📱 **Android USB Sync** | Sync phone Internal Storage and SD Card directly to Notion over USB — no PC disk space used |
| 👀 **Live File Watcher** | Monitors a folder in real time and auto-uploads any new or changed file |
| 💾 **Resumable** | State is saved after every single file, so interrupted syncs always resume where they left off |

---

## 🚀 Quick Start (3 Steps)

### Step 1 — Clone & Install

```bash
git clone https://github.com/shishir0x/Notion-Unlimited-Cloud.git
cd Notion-Unlimited-Cloud
pip install -r requirements.txt
```

### Step 2 — Configure Notion (First Run Only)

**Option A — Automatic (recommended):**
```bash
python setup.py
```
The wizard walks you through creating a Notion integration and guides you to your Database ID.

**Option B — Manual:**
Create a `.env` file:
```ini
NOTION_TOKEN=ntn_your_integration_secret_here
NOTION_DATABASE_ID=your_32_character_database_id_here
```

> **How to get these values:**
> 1. Go to [Notion Integrations](https://www.notion.so/my-integrations) → `+ New Integration` → Copy the **Internal Integration Secret** (starts with `ntn_`)
> 2. Create a full-page **Database** in Notion → Click `...` → `Connections` → add your integration
> 3. Copy the **32-character ID** from the database URL: `https://notion.so/workspace/`**`DATABASE_ID`**`?v=...`

### Step 3 — Run

**Windows (double-click):**
```
Notion_Sync.bat
```

**All platforms (terminal):**
```bash
python notion_sync.py
```

You'll see the interactive device selector:

```
╔═══════════════════════════════════════════════════════════════╗
║  ☁️   NOTION UNLIMITED CLOUD & WEB DRIVE                      ║
║  Your personal unlimited cloud — powered by Notion API        ║
╚═══════════════════════════════════════════════════════════════╝

  🔍 Detecting connected storage devices…
  ✅ 3 device(s) found

  Select a storage source to sync:

  ── LOCAL DRIVES ─────────────────────────────────────────
    [1] 💽  Local Disk (C:)               238.4 GB used / 476.8 GB total
    [2] 💽  Local Disk (D:)               120.0 GB used / 240.0 GB total

  ── ANDROID (OnePlus Nord CE4 — connected via USB) ──────
    [3] 📱  OnePlus Nord CE4 — Internal Storage
    [4] 💾  OnePlus Nord CE4 — SD Card

  ── OTHER OPTIONS ────────────────────────────────────────
    [g] 🌐  Open Web Drive GUI (http://127.0.0.1:8765)
    [s] 📊  Show Sync Status (git status view)
    [r] 🔄  Rebuild index from Notion
    [n] 📝  Open Notion database in browser
    [q] ❌  Exit

  Enter your choice:
```

After selecting a drive, you pick a subfolder:

```
  📁 Choose what to sync from Local Disk (C:):

    [1] 👤  All User Folders (Desktop + Documents + Downloads + Pictures + Music + Videos)
    [2] 🖥️  Desktop
    [3] 📄  Documents
    [4] ⬇️  Downloads
    [5] 🖼️  Pictures
    [6] 🎵  Music
    [7] 🎬  Videos
    [8] 📁  Custom path...
```

---

## 🖥️ Web Drive GUI

Start the GUI server:
```bash
python notion_server.py
```
Open **`http://127.0.0.1:8765`** in your browser.

Features:
- 📂 Browse full folder tree (same structure as your local disk)
- 🔍 Real-time search across all files
- 👁️ In-browser preview for images, PDFs, text files, code
- 📥 Download any file or folder as a ZIP
- ⚡ Live Sync Activity panel — watch files upload in real time

---

## 🌐 NotionDrive Web App (Next.js)

`notion-drive-app/` is a **second, standalone web frontend** for the same Notion database — a modern Google Drive-style file manager built with **Next.js 16 + React 19 + Tailwind CSS**. It runs independently of the Python CLI/server on **port 3000**.

### Quick Start

```bash
cd notion-drive-app
npm install

# Create .env.local with your Notion credentials:
#   NOTION_TOKEN=ntn_your_integration_secret_here
#   NOTION_DATABASE_ID=your_32_character_database_id_here

npm run dev        # → http://localhost:3000
# or
npm run build && npm start
```

### Features

- 📂 Folder-tree browsing with breadcrumbs — grid or list view
- 🔍 Instant full-text search (SQLite FTS5 index) with file-type filters
- 🕐 **Recent**, ⭐ **Starred**, and 🗑️ **Trash** views
- ⭐ Star / unstar, ✏️ rename, 📦 move, 🗑️ archive & restore
- ⬆️ Drag-and-drop upload (creates metadata pages in Notion)
- 👁️ In-browser preview with signed-URL proxying
- ⚡ Live updates via SSE — polls Notion every 5 seconds and refreshes the UI automatically

### How it works

- Talks to Notion directly through `@notionhq/client` (`lib/notion.ts`)
- Caches every page in a local SQLite database (`notion_drive.db`, `lib/cache.ts`) so browsing and search never repeatedly hit the Notion API
- Server API routes: `/api/drive`, `/api/search`, `/api/action`, `/api/upload`, `/api/view`, `/api/stats`, `/api/events`
- Same metadata-only philosophy as the CLI: only file name/size/type/path are stored in Notion; file bytes are served via the `/api/view` proxy from wherever they live

> **Schema note:** both apps share the same database schema — files carry their extension in `File Extension`, and deleted items are flagged with the `Archived` checkbox that powers the Trash view in both UIs. Run `python setup.py` to create the base schema (including the `Archived` column) before first use.

---

## ⌨️ Command Line Usage

```bash
# Interactive device selector (recommended)
python notion_sync.py

# Check what needs syncing (like git status)
python notion_sync.py status --path "C:\Users\nitro\Documents"

# Sync a specific folder (incremental — only new/changed)
python notion_sync.py sync --path "C:\Users\nitro\Documents"

# Force re-upload everything
python notion_sync.py sync-all --path "C:\Users\nitro\Documents"

# Watch a folder and auto-upload changes every 4 seconds
python notion_sync.py watch --path "C:\Users\nitro\Documents" --interval 4

# Open web browser GUI
python notion_sync.py gui

# Rebuild local index from Notion (after database changes)
python notion_sync.py rebuild
```

---

## 📁 Project Structure

```
Notion-Unlimited-Cloud/
│
├── notion_sync.py          Main entry point — device selector & sync runner
├── notion_server.py        Web Drive GUI server (http://127.0.0.1:8765)
├── setup.py                First-time setup wizard
├── Notion_Sync.bat         Windows 1-click launcher
│
├── core/                   Shared library (no code duplication)
│   ├── config.py           Credential loading & shared constants
│   ├── storage.py          Universal storage device discovery
│   ├── filters.py          Unified file/folder ignore rules
│   ├── state.py            Git-style .notion_sync_state.json manager
│   ├── notion_api.py       Notion REST API wrapper (retry, pagination, cache)
│   └── sync_engine.py      Differential scan → diff → upload engine
│
├── notion-drive-app/       Standalone Next.js web drive (port 3000)
│   ├── app/                UI page + API routes (/api/drive, /api/search, …)
│   ├── components/         Drive UI (Sidebar, FileGrid, FileTable, PreviewModal…)
│   └── lib/                Notion SDK client + SQLite cache (notion_drive.db)
│
├── requirements.txt        Python dependencies
├── .env.example            Credential template
└── .env                    Your credentials (git-ignored, never committed)
```

---

## 📱 Android Phone Sync (ADB)

Requirements:
1. Install [ADB (Android Debug Bridge)](https://developer.android.com/tools/releases/platform-tools)
2. On your phone: **Settings → Developer Options → USB Debugging → Enable**
3. Connect phone via USB cable
4. Tap **"Allow"** on the USB Debugging authorization prompt
5. Run `adb devices` to verify — you should see your device ID

The app will automatically detect your phone and list both Internal Storage and SD Card as sync options. Files are streamed directly from phone to Notion — **0 bytes used on your PC disk**.

> **Android Media & App Data:** The `Android/media` directory (WhatsApp, Telegram, camera media) is included for backup on internal storage and SD cards, while private app data (`Android/data`, `Android/obb`, cache) and system junk are safely excluded.

---

## ⚡ How Incremental Sync Works (Git-Style)

Every file is tracked by its **path + modification time + size** in `.notion_sync_state.json`:

```
Local file scan
       │
       ▼
Compare mtime & size with .notion_sync_state.json
       │
   ┌───┴───────────────────────────────────────────────────────┐
   │                         │                                 │
   ▼                         ▼                                 ▼
[UP-TO-DATE]             [NEW FILE]                     [MODIFIED]
mtime & size             Never synced                   Size or date
unchanged                                               changed
   │                         │                                 │
   ▼                         ▼                                 ▼
⏩ SKIP                  ➕ POST to Notion            🔄 PATCH in Notion
(0 Notion API calls)    (Create new page)            (Update existing page)
                              │                                 │
                              └───────────────┬─────────────────┘
                                              │
                                              ▼
                               💾 Save to .notion_sync_state.json
                               🔴 LIVE update in Web Drive GUI
```

---

## 🛡️ Privacy & Safety

- **Credentials never committed:** `.env`, `.notion_sync_state.json`, and `.notion_drive_cache.json` are all git-ignored
- **System folders excluded:** `AppData`, `Windows`, `node_modules`, `$Recycle.Bin`, `__pycache__`, and all system-critical directories are automatically skipped
- **Android private app data excluded:** `Android/data`, `Android/obb`, `.thumbnails`, and `LOST.DIR` are excluded, while `Android/media` is preserved for backup
- **No file content sent:** Only file metadata (name, size, path, modification date) is stored in Notion. File content is served locally on demand via `http://127.0.0.1:8765`

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.
