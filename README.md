# ☁️ Notion Unlimited Cloud & Web Drive

[![Node.js](https://img.shields.io/badge/Next.js-16-black.svg)](https://nextjs.org/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Notion API](https://img.shields.io/badge/Notion%20API-v2022--06--28-black.svg)](https://developers.notion.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform: Windows · macOS · Linux](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-0078D6.svg)](https://github.com/shishir0x/Notion-Unlimited-Cloud)

> Turn your **Notion database** into an unlimited cloud drive with a modern Google Drive-style Next.js web interface, Git-style incremental sync, real-time file watcher, and universal storage device support (local drives + Android via ADB).

---

## ✨ What It Does

| Feature | Description |
|---|---|
| 🌐 **Next.js Web Drive** | Modern Google Drive-style web app at `http://localhost:3000` — browse, search, preview, and upload files directly |
| 🔍 **Auto Device Discovery** | Detects all connected drives (C:, D:, USB sticks) and Android phones via ADB automatically — no hardcoding |
| ⚡ **Git-Style Incremental Sync** | Tracks file `mtime` + `size` — only uploads new or changed files. Skips unchanged files instantly with 0 API calls |
| 🔄 **No Duplicates** | Modified files are updated in-place via Notion PATCH (no duplicate rows ever created) |
| 📱 **Android USB Sync** | Sync phone Internal Storage and SD Card directly to Notion over USB — no PC disk space used |
| 👀 **Live File Watcher** | Monitors a folder in real time and auto-uploads any new or changed file |
| 💾 **Resumable** | State is saved after every single file, so interrupted syncs always resume where they left off |

---

## 🚀 Quick Start

### Step 1 — Configure Notion (First Run Only)

Run the setup wizard:
```bash
python setup.py
```
or create `.env`:
```ini
NOTION_TOKEN=ntn_your_integration_secret_here
NOTION_DATABASE_ID=your_32_character_database_id_here
```

### Step 2 — Run the Application

**Windows (1-Click Launcher):**
Double click `Notion_Sync.bat` or `start.bat`.

**Terminal (All Platforms):**
```bash
npm start
# or
python launcher.py
```

This starts the Next.js web application on **`http://localhost:3000`**, opens your browser automatically, and starts the terminal sync CLI in the foreground.

---

## 🌐 Next.js Web Drive Application

The web drive is located in `notion-drive-app/` and runs on port **3000**:
- 📂 **Google Drive Layout**: Sidebar (My Drive, Recent, Starred, Trash), breadcrumbs, grid and table views
- 🔍 **Real-Time Search**: Debounced global search across all files and folders
- ⬆️ **Drag & Drop Uploads**: Dropzone for direct uploads
- 👁️ **File Previews**: Modal viewer for images, video streaming, audio, PDFs, and syntax-highlighted code
- ⚡ **Direct Notion Connection**: Type-safe REST database queries and fast local SQLite caching

---

## ⌨️ Command Line Sync Engine

```bash
# Interactive device selector (recommended)
python notion_sync.py

# Check what needs syncing (like git status)
python notion_sync.py status --path "C:\Users\nitro\Documents"

# Sync a specific folder (incremental — only new/changed)
python notion_sync.py sync --path "C:\Users\nitro\Documents"

# Force re-upload everything
python notion_sync.py sync-all --path "C:\Users\nitro\Documents"

# Watch a folder and auto-upload changes
python notion_sync.py watch --path "C:\Users\nitro\Documents"

# Rebuild local index from Notion
python notion_sync.py rebuild
```

---

## 📁 Project Structure

```
Notion-Unlimited-Cloud/
│
├── launcher.py             Unified application runner (Next.js :3000 + terminal sync CLI)
├── Notion_Sync.bat         Windows 1-click launcher
├── start.bat               Launcher alias
├── notion_sync.py          CLI sync engine (devices, folders, Android ADB)
├── setup.py                First-time credential wizard
│
├── core/                   Core Python sync library
│   ├── config.py           Credential loading & shared constants
│   ├── storage.py          Universal storage device discovery
│   ├── filters.py          Unified file/folder ignore rules
│   ├── state.py            Git-style .notion_sync_state.json manager
│   ├── notion_api.py       Notion REST API wrapper (retry, pagination, cache)
│   └── sync_engine.py      Differential scan → diff → upload engine
│
└── notion-drive-app/       Next.js 16 Web Application (:3000)
    ├── app/                App router pages & API routes (/api/drive, /api/search, /api/upload, …)
    ├── components/         UI components (Sidebar, TopBar, FileGrid, PreviewModal, …)
    └── lib/                Direct Notion API client (notion.ts) & SQLite cache (cache.ts)
```
