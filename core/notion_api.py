"""
core/notion_api.py — Thin, reliable Notion API wrapper.

Handles all HTTP communication with Notion so the rest of the code
never has to deal with raw requests, pagination, or retry logic.
"""

import time
from typing import Any, Dict, Generator, List, Optional, Tuple

import requests

from core.config import NOTION_VERSION


class NotionAPI:
    """Minimal Notion API client with folder caching and retry support."""

    def __init__(self, token: str, db_id: str):
        self.db_id = db_id.replace("-", "")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        }
        # In-memory cache: (folder_name, parent_notion_id) → notion_page_id
        # parent_notion_id is None for root-level folders.
        self._folder_cache: Dict[Tuple[str, Optional[str]], str] = {}

    # ──────────────────────────────────────────────────────────────────────────
    # Low-level helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _post(self, url: str, payload: Dict) -> Optional[Dict]:
        """POST with simple retry (network blips are common during large syncs)."""
        for attempt in range(3):
            try:
                r = requests.post(url, headers=self.headers, json=payload, timeout=25)
                if r.status_code == 429:           # rate-limited
                    time.sleep(float(r.headers.get("Retry-After", 2)))
                    continue
                return r.json() if r.ok else None
            except requests.RequestException:
                time.sleep(1.5 * (attempt + 1))
        return None

    def _patch(self, url: str, payload: Dict) -> Optional[Dict]:
        for attempt in range(3):
            try:
                r = requests.patch(url, headers=self.headers, json=payload, timeout=25)
                if r.status_code == 429:
                    time.sleep(float(r.headers.get("Retry-After", 2)))
                    continue
                return r.json() if r.ok else None
            except requests.RequestException:
                time.sleep(1.5 * (attempt + 1))
        return None

    # ──────────────────────────────────────────────────────────────────────────
    # Database queries
    # ──────────────────────────────────────────────────────────────────────────

    def query_all(self, extra_filter: Optional[Dict] = None) -> Generator[Dict, None, None]:
        """Yield every page in the database, handling pagination automatically."""
        cursor = None
        while True:
            payload: Dict[str, Any] = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            if extra_filter:
                payload["filter"] = extra_filter
            data = self._post(
                f"https://api.notion.com/v1/databases/{self.db_id}/query", payload
            )
            if not data:
                break
            for item in data.get("results", []):
                yield item
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

    def test_connection(self) -> Tuple[bool, str]:
        """
        Test that the token + db_id are valid.
        Returns (success: bool, message: str).
        """
        try:
            r = requests.get(
                f"https://api.notion.com/v1/databases/{self.db_id}",
                headers=self.headers,
                timeout=10,
            )
            if r.status_code == 200:
                title_list = r.json().get("title", [])
                name = title_list[0]["plain_text"] if title_list else "Untitled"
                return True, f'Database found: "{name}"'
            elif r.status_code == 401:
                return False, "Invalid Notion token. Check your NOTION_TOKEN."
            elif r.status_code == 404:
                return False, "Database not found. Check your NOTION_DATABASE_ID and share the DB with your integration."
            else:
                return False, f"Unexpected Notion API error: {r.status_code}"
        except Exception as e:
            return False, f"Network error: {e}"

    # ──────────────────────────────────────────────────────────────────────────
    # Page creation / update
    # ──────────────────────────────────────────────────────────────────────────

    def create_page(self, properties: Dict, icon_emoji: str = "📄") -> Optional[str]:
        """Create a page in the database. Returns its Notion ID or None on failure."""
        payload = {
            "parent": {"database_id": self.db_id},
            "icon": {"type": "emoji", "emoji": icon_emoji},
            "properties": properties,
        }
        result = self._post("https://api.notion.com/v1/pages", payload)
        if result and result.get("id"):
            return result["id"].replace("-", "")
        return None

    def update_page(self, notion_id: str, properties: Dict) -> bool:
        """PATCH an existing page's properties. Returns True on success."""
        result = self._patch(
            f"https://api.notion.com/v1/pages/{notion_id}",
            {"properties": properties},
        )
        return result is not None

    # ──────────────────────────────────────────────────────────────────────────
    # Folder hierarchy management
    # ──────────────────────────────────────────────────────────────────────────

    def preload_folders(self):
        """
        Fetch all existing Folder pages into the local cache.
        Loads instantly from local disk cache (<0.01s) if available.
        """
        import json
        from pathlib import Path
        cache_path = Path.home() / ".notion_drive_cache.json"
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                loaded = 0
                for nid, it in data.get("items", {}).items():
                    if it.get("type") == "Folder":
                        clean_name = it.get("name", "").replace("📁 ", "").strip()
                        parent_id = it.get("parent_id")
                        if clean_name:
                            self._folder_cache[(clean_name, parent_id)] = nid
                            loaded += 1
                if loaded > 0:
                    return
            except Exception:
                pass

        for item in self.query_all({"property": "Type", "select": {"equals": "Folder"}}):
            nid = item["id"].replace("-", "")
            props = item.get("properties", {})
            title_list = props.get("Name", {}).get("title", [])
            name = title_list[0].get("plain_text", "").strip() if title_list else ""
            name = name.replace("📁 ", "").strip()
            parents = props.get("Parent Folder", {}).get("relation", [])
            parent_id = parents[0]["id"].replace("-", "") if parents else None
            if name:
                self._folder_cache[(name, parent_id)] = nid

    def ensure_folder(
        self,
        name: str,
        parent_id: Optional[str],
        emoji: str = "📁",
        is_root: bool = False,
    ) -> Optional[str]:
        """
        Find or create a folder by name under parent_id.
        Root folders (is_root=True) are created with Favorite=True.
        Returns the Notion page ID.
        """
        cache_key = (name, parent_id)
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        props: Dict[str, Any] = {
            "Name": {"title": [{"text": {"content": name}}]},
            "Type": {"select": {"name": "Folder"}},
            "Favorite": {"checkbox": is_root},
        }
        if parent_id:
            props["Parent Folder"] = {"relation": [{"id": parent_id}]}

        notion_id = self.create_page(props, icon_emoji=emoji)

        # Some Notion setups don't allow sub-item relation hierarchy — retry without it
        if notion_id is None and parent_id:
            props_no_parent = {k: v for k, v in props.items() if k != "Parent Folder"}
            notion_id = self.create_page(props_no_parent, icon_emoji=emoji)

        if notion_id:
            self._folder_cache[cache_key] = notion_id
        return notion_id

    def build_folder_path(self, parts: List[str], root_id: str) -> str:
        """
        Recursively ensure every folder in `parts` exists under `root_id`.
        Returns the Notion ID of the deepest (leaf) folder.
        """
        current_id = root_id
        for part in parts:
            current_id = self.ensure_folder(part, current_id) or current_id
        return current_id
