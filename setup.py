"""
setup.py — First-Run Setup Wizard for Notion Unlimited Cloud.

Guides a brand new user through:
  1. Getting a Notion Integration Token
  2. Getting their Database ID
  3. Testing the connection
  4. Saving credentials to .env

Run this script directly:
    python setup.py
Or it is called automatically by Notion_Sync.bat on first launch.
"""

import sys
import os
from pathlib import Path

# ── Ensure project root is on sys.path so `core` can be imported ──────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import ENV_FILE, save_credentials


# ─────────────────────────────────────────────────────────────────────────────
# ASCII UI helpers
# ─────────────────────────────────────────────────────────────────────────────

def _header():
    print()
    print("=" * 65)
    print("  ☁️   NOTION UNLIMITED CLOUD — First Time Setup Wizard")
    print("=" * 65)
    print("  This wizard will connect your Notion database in 3 steps.")
    print("  It only runs once. Your credentials are saved to .env")
    print("=" * 65)
    print()


def _ok(msg):  print(f"  ✅  {msg}")
def _err(msg): print(f"  ❌  {msg}")
def _info(msg): print(f"  ℹ️   {msg}")
def _step(n, title): print(f"\n  ── STEP {n}: {title} ──")


# ─────────────────────────────────────────────────────────────────────────────
# Interactive steps
# ─────────────────────────────────────────────────────────────────────────────

def step1_get_token() -> str:
    _step(1, "Get your Notion Integration Token")
    print()
    print("  Follow these steps in your browser:")
    print()
    print("  1. Open  https://www.notion.so/my-integrations")
    print("  2. Click '+ New integration'")
    print("  3. Give it any name (e.g. 'My Cloud Drive')")
    print("  4. Click 'Submit'")
    print("  5. Copy the 'Internal Integration Secret'")
    print("     It starts with  ntn_  and is about 50 characters long.")
    print()

    while True:
        token = input("  Paste your Integration Token here: ").strip()
        if token.startswith("ntn_") and len(token) > 20:
            _ok("Token format looks correct.")
            return token
        elif token:
            print("  ⚠️  That doesn't look like a valid token (should start with 'ntn_').")
            retry = input("  Try again? [Y/n]: ").strip().lower()
            if retry == "n":
                return token
        else:
            print("  ⚠️  No token entered. Please paste your token.")


def step2_get_database_id() -> str:
    _step(2, "Get your Notion Database ID")
    print()
    print("  Follow these steps:")
    print()
    print("  1. Open Notion and create a new DATABASE (full-page table view)")
    print("     If you already have one, open it.")
    print()
    print("  2. Share it with your integration:")
    print("     Click '...' (top-right) → 'Connections' → find your integration → 'Confirm'")
    print()
    print("  3. Copy the Database ID from the URL:")
    print("     https://notion.so/your-workspace/ [DATABASE_ID_HERE] ?v=...")
    print("     It is the 32-character string between the last '/' and '?'")
    print()

    while True:
        raw = input("  Paste your Database ID here: ").strip()
        # Accept both raw IDs and full Notion URLs
        if "notion.so" in raw or "app.notion.com" in raw:
            # Extract the ID from the URL
            parts = raw.rstrip("/").split("/")[-1].split("?")[0].replace("-", "")
            if len(parts) == 32:
                _ok(f"Extracted Database ID: {parts}")
                return parts
        db_id = raw.replace("-", "").replace(" ", "")
        if len(db_id) == 32:
            _ok("Database ID format looks correct.")
            return db_id
        elif raw:
            print(f"  ⚠️  Expected a 32-character ID, got {len(db_id)} characters.")
            retry = input("  Try again? [Y/n]: ").strip().lower()
            if retry == "n":
                return raw
        else:
            print("  ⚠️  No Database ID entered.")


def step3_test_connection(token: str, db_id: str) -> bool:
    _step(3, "Testing your Notion connection")
    print()
    print("  Connecting to Notion API…")

    try:
        import requests
        from core.config import NOTION_VERSION
        r = requests.get(
            f"https://api.notion.com/v1/databases/{db_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
            },
            timeout=12,
        )
        if r.status_code == 200:
            title_list = r.json().get("title", [])
            name = title_list[0]["plain_text"] if title_list else "Untitled"
            _ok(f"Connected to Notion successfully!")
            _ok(f'Database found: "{name}"')
            return True
        elif r.status_code == 401:
            _err("Invalid token. Go back to Step 1 and recopy your token.")
        elif r.status_code == 404:
            _err("Database not found.")
            _info("Make sure you shared the database with your integration (Step 2, point 2).")
        else:
            _err(f"Unexpected response from Notion: HTTP {r.status_code}")
            _info(r.text[:300])
    except ImportError:
        _err("'requests' library not found. Run:  pip install -r requirements.txt")
    except Exception as e:
        _err(f"Network error: {e}")

    return False


def ensure_database_properties(token: str, db_id: str):
    """
    Make sure the Notion database has all required properties.
    Creates any missing columns automatically.
    """
    required_props = {
        "Type":           {"select": {"options": []}},
        "File Type":      {"select": {"options": []}},
        "File Extension": {"rich_text": {}},
        "File Size":      {"number": {"format": "number"}},
        "Open in Browser": {"url": {}},
        "Parent Folder":  {"relation": {"database_id": db_id, "single_property": {}}},
        "Description":    {"rich_text": {}},
        "Favorite":       {"checkbox": {}},
    }
    try:
        import requests
        from core.config import NOTION_VERSION
        headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }
        # Get existing properties
        r = requests.get(
            f"https://api.notion.com/v1/databases/{db_id}",
            headers=headers, timeout=12,
        )
        if r.status_code != 200:
            return

        existing = r.json().get("properties", {})
        missing = {k: v for k, v in required_props.items() if k not in existing}

        if missing:
            print(f"\n  ⚙️  Auto-creating {len(missing)} missing database column(s)…")
            patch_r = requests.patch(
                f"https://api.notion.com/v1/databases/{db_id}",
                headers=headers,
                json={"properties": missing},
                timeout=15,
            )
            if patch_r.status_code == 200:
                _ok(f"Database columns created: {', '.join(missing.keys())}")
            else:
                _info("Could not auto-create columns. You may need to add them manually.")
                _info("Required columns: Type, File Type, File Extension, File Size,")
                _info("                  Open in Browser, Parent Folder, Description, Favorite")
        else:
            _ok("All required database columns already exist.")
    except Exception as e:
        _info(f"Could not verify database schema: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Main wizard flow
# ─────────────────────────────────────────────────────────────────────────────

def run_setup():
    """Run the full first-time setup wizard. Returns True if setup succeeded."""
    _header()

    token = step1_get_token()
    db_id = step2_get_database_id()
    ok = step3_test_connection(token, db_id)

    if not ok:
        print()
        retry = input("  Connection failed. Would you like to re-enter credentials? [Y/n]: ").strip().lower()
        if retry != "n":
            return run_setup()  # Start over
        print("\n  Setup cancelled. You can run setup again at any time with:  python setup.py")
        return False

    # Auto-create missing database columns
    ensure_database_properties(token, db_id)

    # Save to .env
    save_credentials(token, db_id)

    print()
    print("=" * 65)
    print("  ✅  Setup Complete! Your credentials are saved to .env")
    print("=" * 65)
    print()
    print("  You can now run:  python notion_sync.py")
    print("  Or double-click:  Notion_Sync.bat")
    print()
    return True


if __name__ == "__main__":
    # Support --test flag for CI / verification
    if "--test" in sys.argv:
        from core.config import NOTION_TOKEN, NOTION_DATABASE_ID, has_credentials
        if not has_credentials():
            print("❌ No credentials found in .env")
            sys.exit(1)
        from core.notion_api import NotionAPI
        api = NotionAPI(NOTION_TOKEN, NOTION_DATABASE_ID)
        ok, msg = api.test_connection()
        print(("✅ " if ok else "❌ ") + msg)
        sys.exit(0 if ok else 1)

    success = run_setup()
    sys.exit(0 if success else 1)
