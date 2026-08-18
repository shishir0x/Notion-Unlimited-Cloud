"""
core/notion_api.py — Thin, reliable Notion API wrapper.

Handles all HTTP communication with Notion so the rest of the code
never has to deal with raw requests, pagination, or retry logic.
"""

import json
import time
import threading
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

import requests

from core.config import NOTION_VERSION


class NotionRateLimiter:
    """
    High-performance rate limiter for Notion API calls.
    Allows steady smooth throughput (~2.2 req/sec) without burst spikes.
    """
    def __init__(self, rate: float = 2.2, burst: int = 1):
        self.rate = rate
        self.burst = burst
        self.tokens = burst
        self.last_update = time.time()
        self._lock = threading.Lock()
        self._pending_429: Dict[str, float] = {}
        self._global_backoff = 0.0
        self._request_count = 0
        self._success_count = 0
        self._fail_count = 0
        self._rate_limit_count = 0

    def _refill_tokens(self):
        now = time.time()
        elapsed = now - self.last_update
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_update = now

    def acquire(self, url: str) -> bool:
        with self._lock:
            if self._global_backoff > 0:
                if time.time() < self._global_backoff:
                    return False
                self._global_backoff = 0.0

            if url in self._pending_429:
                if time.time() < self._pending_429[url]:
                    return False
                del self._pending_429[url]

            self._refill_tokens()
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                self._request_count += 1
                return True
            return False

    def record_success(self):
        with self._lock:
            self._success_count += 1

    def record_failure(self, is_rate_limit: bool = False, retry_after: Optional[float] = None, url: Optional[str] = None):
        with self._lock:
            self._fail_count += 1
            if is_rate_limit:
                self._rate_limit_count += 1
                backoff = retry_after if retry_after else 2.0
                self._global_backoff = time.time() + backoff
                if url:
                    self._pending_429[url] = time.time() + backoff

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "total_requests": self._request_count,
                "successes": self._success_count,
                "failures": self._fail_count,
                "rate_limits": self._rate_limit_count,
            }


# Global rate limiter instance (Smooth 2.2 req/sec, burst 1)
_rate_limiter = NotionRateLimiter(rate=2.2, burst=1)


def is_page_archived(props: Dict[str, Any]) -> bool:
    """Return True if a Notion page's properties mark it as archived (trash)."""
    return bool(props.get("Archived", {}).get("checkbox"))


class NotionAPI:
    """High-performance Notion API client with persistent HTTP session and caching."""

    def __init__(self, token: str, db_id: str):
        self.db_id = db_id.replace("-", "")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_VERSION,
        }
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=1)
        self.session.mount("https://", adapter)
        self.session.headers.update(self.headers)

        self._folder_cache: Dict[Tuple[str, Optional[str]], str] = {}
        self._request_cache: Dict[str, Tuple[Any, float]] = {}
        self._cache_ttl = 5.0

    def _post(self, url: str, payload: Dict) -> Optional[Dict]:
        """POST with persistent connection, safe pacing, and transparent 429 retry."""
        from core.sync_engine import log_sync_event
        for attempt in range(4):
            try:
                while not _rate_limiter.acquire(url):
                    time.sleep(0.1)

                r = self.session.post(url, json=payload, timeout=25)
                if r.status_code == 429:
                    retry_after = float(r.headers.get("Retry-After", 5))
                    _rate_limiter.record_failure(is_rate_limit=True, retry_after=retry_after, url=url)
                    log_sync_event("warn", f"Notion API rate limit active. Auto-resuming in {int(retry_after)}s...")
                    print(f"\n  ⏳ Notion rate limit cooldown: resuming in {int(retry_after)}s...", end="", flush=True)
                    # Countdown in 1s steps so user sees live feedback
                    remaining = int(retry_after)
                    while remaining > 0:
                        time.sleep(min(1.0, remaining))
                        remaining -= 1
                    print(" ✅ Resuming sync!\n")
                    continue

                if r.ok:
                    _rate_limiter.record_success()
                    return r.json()
                else:
                    _rate_limiter.record_failure()
                    return None
            except Exception:
                _rate_limiter.record_failure()
                time.sleep(1.0 * (attempt + 1))
        return None

    def _patch(self, url: str, payload: Dict) -> Optional[Dict]:
        """PATCH with persistent connection, safe pacing, and transparent 429 retry."""
        from core.sync_engine import log_sync_event
        for attempt in range(4):
            try:
                while not _rate_limiter.acquire(url):
                    time.sleep(0.1)

                r = self.session.patch(url, json=payload, timeout=25)
                if r.status_code == 429:
                    retry_after = float(r.headers.get("Retry-After", 5))
                    _rate_limiter.record_failure(is_rate_limit=True, retry_after=retry_after, url=url)
                    log_sync_event("warn", f"Notion API rate limit active. Auto-resuming in {int(retry_after)}s...")
                    print(f"\n  ⏳ Notion rate limit cooldown: resuming in {int(retry_after)}s...", end="", flush=True)
                    remaining = int(retry_after)
                    while remaining > 0:
                        time.sleep(min(1.0, remaining))
                        remaining -= 1
                    print(" ✅ Resuming sync!\n")
                    continue

                if r.ok:
                    _rate_limiter.record_success()
                    return r.json()
                else:
                    _rate_limiter.record_failure()
                    return None
            except Exception:
                _rate_limiter.record_failure()
                time.sleep(1.0 * (attempt + 1))
        return None
    
    def _get_cached(self, key: str) -> Optional[Any]:
        """Get cached response if not expired."""
        if key in self._request_cache:
            result, timestamp = self._request_cache[key]
            if time.time() - timestamp < self._cache_ttl:
                return result
            del self._request_cache[key]
        return None
    
    def _set_cached(self, key: str, value: Any):
        """Cache a response."""
        self._request_cache[key] = (value, time.time())
        # Limit cache size
        if len(self._request_cache) > 1000:
            oldest = min(self._request_cache.items(), key=lambda x: x[1][1])
            del self._request_cache[oldest[0]]

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

    def delete_page(self, notion_id: str) -> bool:
        """Archive (delete) a page in Notion. Returns True on success.

        Sets the "Archived" checkbox so the item lands in the trash view of
        both the Python web GUI and the Next.js app. Falls back to Notion's
        native archive for databases that lack the checkbox property.
        """
        nid = notion_id.replace("-", "")
        for attempt in range(3):
            try:
                r = requests.patch(
                    f"https://api.notion.com/v1/pages/{nid}",
                    headers=self.headers,
                    json={"properties": {"Archived": {"checkbox": True}}},
                    timeout=25,
                )
                if r.status_code == 200:
                    return True
                if r.status_code == 429:
                    time.sleep(float(r.headers.get("Retry-After", 2)))
                    continue
                # "Archived" may not exist on older databases → native archive fallback
                r2 = requests.patch(
                    f"https://api.notion.com/v1/pages/{nid}",
                    headers=self.headers,
                    json={"archived": True},
                    timeout=25,
                )
                return r2.status_code == 200
            except requests.RequestException:
                time.sleep(1.5 * (attempt + 1))
        return False

    # ──────────────────────────────────────────────────────────────────────────
    # Folder hierarchy management
    # ──────────────────────────────────────────────────────────────────────────

    def preload_folders(self):
        """
        Fetch all existing Folder pages into the local cache.
        Loads instantly from SQLite local database and sync state in <5ms.
        """
        project_root = Path(__file__).resolve().parent.parent

        # 1. High-speed SQLite index (Instant 1-2ms)
        db_path = project_root / ".notion_drive_index.db"
        if db_path.exists():
            import sqlite3
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()
                cursor.execute("SELECT id, name, parent_id FROM items WHERE is_folder = 1")
                for row in cursor.fetchall():
                    nid, name, pid = row
                    clean_name = str(name).replace("📁 ", "").strip()
                    if clean_name:
                        self._folder_cache[(clean_name, pid)] = str(nid)
                        if (clean_name, None) not in self._folder_cache:
                            self._folder_cache[(clean_name, None)] = str(nid)
                conn.close()
            except Exception:
                pass

        # 2. JSON Cache & State files
        cache_paths = [
            project_root / ".notion_drive_cache.json",
            Path.home() / ".notion_drive_cache.json",
            Path.cwd() / ".notion_drive_cache.json",
        ]
        for cache_path in cache_paths:
            if cache_path.exists():
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for nid, it in data.get("items", {}).items():
                        if it.get("type") == "Folder":
                            clean_name = it.get("name", "").replace("📁 ", "").strip()
                            parent_id = it.get("parent_id")
                            if clean_name:
                                self._folder_cache[(clean_name, parent_id)] = nid
                                if (clean_name, None) not in self._folder_cache:
                                    self._folder_cache[(clean_name, None)] = nid
                except Exception:
                    pass

        state_paths = [
            project_root / ".notion_sync_state.json",
            Path.home() / ".notion_sync_state.json",
            Path.cwd() / ".notion_sync_state.json",
        ]
        for sp in state_paths:
            if sp.exists():
                try:
                    with open(sp, "r", encoding="utf-8") as f:
                        sdata = json.load(f)
                    for b in ("folders", "android_folders"):
                        for fpath, finfo in sdata.get(b, {}).items():
                            nid = finfo.get("notion_id")
                            fname = Path(fpath).name if b == "folders" else fpath.rstrip("/").split("/")[-1]
                            if nid and fname:
                                self._folder_cache[(fname, None)] = nid
                except Exception:
                    pass

    def append_block_children(self, block_id: str, children: List[Dict]) -> bool:
        """Append child blocks (e.g. text/code content) to a Notion page."""
        bid = block_id.replace("-", "")
        url = f"https://api.notion.com/v1/blocks/{bid}/children"
        res = self._patch(url, {"children": children})
        return res is not None

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
        if parent_id is None and (name, None) in self._folder_cache:
            return self._folder_cache[(name, None)]

        # Fast SQLite lookup (0.1ms)
        project_root = Path(__file__).resolve().parent.parent
        db_path = project_root / ".notion_drive_index.db"
        if db_path.exists():
            import sqlite3
            try:
                conn = sqlite3.connect(str(db_path))
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id FROM items WHERE is_folder = 1 AND (name = ? OR name = ?) LIMIT 1",
                    (name, f"📁 {name}")
                )
                row = cursor.fetchone()
                conn.close()
                if row:
                    ex_id = str(row[0])
                    self._folder_cache[cache_key] = ex_id
                    return ex_id
            except Exception:
                pass

        props: Dict[str, Any] = {
            "Name": {"title": [{"text": {"content": name}}]},
            "Type": {"select": {"name": "Folder"}},
            "Favorite": {"checkbox": is_root},
        }
        if parent_id:
            props["Parent Folder"] = {"relation": [{"id": parent_id}]}

        notion_id = self.create_page(props, icon_emoji=emoji)
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
