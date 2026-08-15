# ☁️ Notion Unlimited Cloud & Web Drive File Manager

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Notion API](https://img.shields.io/badge/Notion%20API-v2022--06--28-black.svg)](https://developers.notion.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows-0078D6.svg)](https://microsoft.com)

Transform your **Notion Database** into a high-speed, unlimited cloud storage drive with a modern **Google Drive & OneDrive Web GUI**, **Git-like differential sync engine**, **live real-time filesystem watcher**, and **1-click browser preview & dynamic folder ZIP downloads**.

---

## 🌟 Key Features

### 🌐 1. Google Drive & OneDrive Web GUI (`http://127.0.0.1:8765`)
- **Familiar Drive Interface**: Dark-themed SPA (Single Page Application) with folder cards, file grids, and storage metrics.
- **Interactive Breadcrumb Navigation**: Seamlessly navigate through deep nested directory structures (e.g., `My Drive > Users > nitro > Documents > Projects`).
- **Instant File Search**: Real-time multi-level search across all files and folders.
- **1-Click In-Browser Previews**: Built-in modal viewer & direct Edge/Chrome tab streaming for PDFs, images, text, and code files.
- **Dynamic ZIP Folder Downloads**: Pack and download entire directory trees on the fly into `.zip` archives with a single click.

### ⚡ 2. Git-Style Differential Sync Engine
- **Delta-Only Syncing**: Tracks file modification times (`mtime`) and sizes via local state tracking (`.notion_sync_state.json`), updating *only* changed or newly added files.
- **Hierarchical Relational Structure**: Mirrored exact parent-child relations directly in Notion using multi-parent relations.
- **Rich File Metadata**: Automatically categorizes files (PDF, Code, Image, Word, Excel, ZIP, etc.) with custom emojis, extensions, and file sizes.
- **Hidden & Dot-File Support**: Full support for syncing hidden configurations and dot-directories (`.vscode`, `.gitconfig`, `.gitignore`).

### 👀 3. Live Auto-Upload File Watcher
- **Real-Time Daemon**: Continuously monitors your local filesystem (e.g., `C:\Users`).
- **Dynamic Progress Bar**: Beautiful terminal UI with visual progress bars (`[██████░░] 75% (3/4) [filename.pdf] [Rem: 1]`).
- **Automatic Uploads**: Instantly pushes newly created or modified files into your Notion database in the background.

### 🚀 4. 1-Click Desktop Launcher (`Notion_Sync.bat`)
- Clean Windows batch runner that automatically starts the background web server and opens the interactive menu.

---

## 📁 Repository Structure

```text
Notion-Unlimited-Cloud/
│
├── notion_git_sync.py      # Git-like CLI synchronization & live watcher engine
├── notion_server.py        # Local multithreaded server & Google Drive Web GUI SPA
├── Notion_Sync.bat         # 1-click Windows Desktop batch launcher
├── requirements.txt        # Python package dependencies
├── .env.example            # Environment configuration template
├── .gitignore              # Ignored cache, state, and temp files
└── README.md               # Documentation and usage guide
```

---

## 🚀 Quick Start & Installation

### 1. Clone the Repository
```bash
git clone https://github.com/shishir0x/Notion-Unlimited-Cloud.git
cd Notion-Unlimited-Cloud
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Notion API Credentials
Create a `.env` file or export your environment variables:
```ini
NOTION_TOKEN=ntn_your_notion_integration_token_here
NOTION_DATABASE_ID=your_32_character_database_id_here
```

> **How to get your Notion Token & Database ID:**
> 1. Go to [Notion Integrations](https://www.notion.so/my-integrations) and create an **Internal Integration**.
> 2. Copy the **Internal Integration Secret** (`ntn_...`).
> 3. Create a Database in Notion and share it with your integration (`...` $\rightarrow$ `Connections` $\rightarrow$ `Add Connection`).
> 4. Copy the 32-character ID from your Notion database URL (`https://notion.so/<DATABASE_ID>?v=...`).

---

## 🖥️ Usage

### 🖱️ Method A: 1-Click Desktop Launcher
Simply double-click **`Notion_Sync.bat`** to start the background server and display the CLI dashboard:

```text
====================================================================
        📁 NOTION GOOGLE DRIVE AUTO-SYNC & WEB GUI ENGINE
====================================================================
  [1] 🚀 Start Live Auto-Upload Watcher (Syncs changes to Notion)
  [2] 🌐 Launch Web Drive File Manager GUI (Google Drive in Browser)
  [3] ⚡ Run Incremental Sync Now (Push pending changes)
  [4] 📊 Check Status & Storage Usage (Git-style inspect)
  [5] 📝 Open Notion Database in Browser
  [6] ❌ Exit
====================================================================
Select an option [1-6]:
```

### ⌨️ Method B: Command Line (CLI)

#### 1. Launch the Web Drive GUI Server:
```bash
python notion_server.py
```
Open **`http://127.0.0.1:8765`** in your web browser.

#### 2. Start Live Auto-Sync Watcher:
```bash
python notion_git_sync.py watch --path "C:\Users" --interval 4
```

#### 3. Run Incremental Sync:
```bash
python notion_git_sync.py sync --path "C:\Users"
```

#### 4. Inspect Sync Status:
```bash
python notion_git_sync.py status --path "C:\Users"
```

---

## 🛡️ Privacy & Safety
- **Git-Ignored State**: Local state tracking (`.notion_sync_state.json`) and cache files (`.notion_drive_cache.json`) are strictly excluded from version control.
- **Safety Filters**: System-critical folders (`AppData`, `node_modules`, `__pycache__`, `$Recycle.Bin`) and sensitive locked files (`ntuser.dat`, registry hives) are automatically skipped.

---

## 📄 License
This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
