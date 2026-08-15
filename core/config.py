"""
core/config.py — Shared configuration & credential loading.

Loads .env from the project directory or home directory.
All other modules import from here so credentials are never duplicated.
"""

import os
import sys
from pathlib import Path

# ─── Locate the project root (the folder containing this core/ package) ───────
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env():
    """Load .env file into os.environ. Checks multiple locations."""
    search_paths = [
        PROJECT_ROOT / ".env",
        Path.home() / ".notion_env",
        Path.home() / ".env",
    ]
    for p in search_paths:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, _, val = line.partition("=")
                            os.environ.setdefault(
                                key.strip(),
                                val.strip().strip('"').strip("'"),
                            )
            except Exception:
                pass


# Load immediately on import
load_env()

# ─── Exported constants (all other modules import from here) ──────────────────
NOTION_TOKEN: str = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID: str = os.getenv("NOTION_DATABASE_ID", "").replace("-", "")
DRIVE_PASSWORD: str = os.getenv("DRIVE_PASSWORD", "")
LOCAL_SERVER_PORT: int = int(os.getenv("LOCAL_SERVER_PORT", "8765"))
POLL_INTERVAL: int = int(os.getenv("POLL_INTERVAL_SECONDS", "4"))
NOTION_VERSION: str = "2022-06-28"

# Path to persistent files (in project root, git-ignored)
STATE_FILE: Path = PROJECT_ROOT / ".notion_sync_state.json"
CACHE_FILE: Path = PROJECT_ROOT / ".notion_drive_cache.json"
ENV_FILE: Path = PROJECT_ROOT / ".env"


def has_credentials() -> bool:
    """Return True if both NOTION_TOKEN and NOTION_DATABASE_ID are set."""
    return bool(NOTION_TOKEN and NOTION_DATABASE_ID)


def save_credentials(token: str, db_id: str, password: str = ""):
    """Persist credentials to the project .env file."""
    content = (
        f"# Notion API Credentials\n"
        f"NOTION_TOKEN={token}\n"
        f"NOTION_DATABASE_ID={db_id}\n"
        f"\n"
        f"# Security (Optional web password for cloud deployment)\n"
        f"DRIVE_PASSWORD={password}\n"
        f"\n"
        f"# Local Sync Settings\n"
        f"LOCAL_SERVER_PORT={LOCAL_SERVER_PORT}\n"
        f"POLL_INTERVAL_SECONDS={POLL_INTERVAL}\n"
    )
    ENV_FILE.write_text(content, encoding="utf-8")
    # Update in-memory values for this session
    os.environ["NOTION_TOKEN"] = token
    os.environ["NOTION_DATABASE_ID"] = db_id.replace("-", "")
    os.environ["DRIVE_PASSWORD"] = password


def reload():
    """Reload credentials from .env into module-level constants."""
    global NOTION_TOKEN, NOTION_DATABASE_ID, DRIVE_PASSWORD
    load_env()
    NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
    NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "").replace("-", "")
    DRIVE_PASSWORD = os.getenv("DRIVE_PASSWORD", "")
