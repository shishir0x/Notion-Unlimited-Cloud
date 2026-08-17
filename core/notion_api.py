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
    Centralized rate limiter for Notion API calls.
    
    Implements:
    - Token bucket algorithm for smooth rate limiting
    - Exponential backoff on 429 responses
    - Request deduplication
    - Concurrency control
    """
    
    def __init__(self, rate: float = 1.0, burst: int = 3):
        """
        Args:
            rate: Requests per second (default 1.0 = 1 req/sec)
            burst: Max burst size (allows short bursts up to this many requests)
        """
        self.rate = rate
        self.burst = burst
        self.tokens = burst
        self.last_update = time.time()
        self._lock = threading.Lock()
        self._pending_429: Dict[str, float] = {}  # url -> retry_after timestamp
        self._global_backoff = 0.0
        self._request_count = 0
        self._success_count = 0
        self._fail_count = 0
        self._rate_limit_count = 0
    
    def _refill_tokens(self):
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_update
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_update = now
    
    def acquire(self, url: str) -> bool:
        """
        Acquire permission to make a request.
        Returns True if allowed, False if rate limited.
        """
        with self._lock:
            # Check if we're in global backoff
            if self._global_backoff > 0:
                if time.time() < self._global_backoff:
                    return False
                self._global_backoff = 0.0
            
            # Check pending 429s
            if url in self._pending_429:
                retry_after = self._pending_429[url]
                if time.time() < retry_after:
                    return False
                del self._pending_429[url]
            
            self._refill_tokens()
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                self._request_count += 1
                return True
            return False
    
    def record_success(self):
        """Record a successful request."""
        with self._lock:
            self._success_count += 1
    
    def record_failure(self, is_rate_limit: bool = False, retry_after: Optional[float] = None, url: Optional[str] = None):
        """Record a failed request."""
        with self._lock:
            self._fail_count += 1
            if is_rate_limit:
                self._rate_limit_count += 1
                # Exponential backoff: 2s, 4s, 8s, 16s, max 60s
                backoff = min(60.0, 2.0 ** min(5, self._rate_limit_count))
                self._global_backoff = time.time() + backoff
                
                if retry_after and url:
                    self._pending_429[url] = time.time() + retry_after
    
    def get_stats(self) -> Dict[str, int]:
        """Get rate limiter statistics."""
        with self._lock:
            return {
                "total_requests": self._request_count,
                "successes": self._success_count,
                "failures": self._fail_count,
                "rate_limits": self._rate_limit_count,
            }


# Global rate limiter instance
_rate_limiter = NotionRateLimiter(rate=1.0, burst=3)


def is_page_archived(props: Dict[str, Any]) -> bool:
    """Return True if a Notion page's properties mark it as archived (trash).

    Both the Python engine and the Next.js web app treat the "Archived"
    checkbox as the single source of truth for the trash view.
    """
    return bool(props.get("Archived", {}).get("checkbox"))


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
        self._request_cache: Dict[str, Tuple[Any, float]] = {}  # url -> (response, timestamp)
        self._cache_ttl = 5.0  # Cache responses for 5 seconds

    # ──────────────────────────────────────────────────────────────────────────
    # Low-level helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _post(self, url: str, payload: Dict) -> Optional[Dict]:
        """POST with rate limiting, retry, and caching."""
        # Check cache first
        cache_key = f"POST:{url}:{json.dumps(payload, sort_keys=True)}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        
        for attempt in range(3):
            try:
                # Wait for rate limiter
                while not _rate_limiter.acquire(url):
                    time.sleep(0.5)
                
                r = requests.post(url, headers=self.headers, json=payload, timeout=25)
                
                if r.status_code == 429:  # rate-limited
                    retry_after = float(r.headers.get("Retry-After", 2))
                    _rate_limiter.record_failure(is_rate_limit=True, retry_after=retry_after, url=url)
                    time.sleep(retry_after)
                    continue
                
                _rate_limiter.record_success()
                result = r.json() if r.ok else None
                self._set_cached(cache_key, result)
                return result
            except requests.RequestException:
                _rate_limiter.record_failure()
                time.sleep(1.5 * (attempt + 1))
        return None

    def _patch(self, url: str, payload: Dict) -> Optional[Dict]:
        """PATCH with rate limiting and retry."""
        for attempt in range(3):
            try:
                while not _rate_limiter.acquire(url):
                    time.sleep(0.5)
                
                r = requests.patch(url, headers=self.headers, json=payload, timeout=25)
                
                if r.status_code == 429:
                    retry_after = float(r.headers.get("Retry-After", 2))
                    _rate_limiter.record_failure(is_rate_limit=True, retry_after=retry_after, url=url)
                    time.sleep(retry_after)
                    continue
                
                _rate_limiter.record_success()
                return r.json() if r.ok else None
            except requests.RequestException:
                _rate_limiter.record_failure()
                time.sleep(1.5 * (attempt + 1))
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
        Loads instantly from local disk cache and sync state files.
        """
        project_root = Path(__file__).resolve().parent.parent
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

        try:
            for item in self.query_all({"property": "Type", "select": {"equals": "Folder"}}):
                nid = item["id"].replace("-", "")
                props = item.get("properties", {})
                if is_page_archived(props):
                    continue
                title_list = props.get("Name", {}).get("title", [])
                name = title_list[0].get("plain_text", "").strip() if title_list else ""
                name = name.replace("📁 ", "").strip()
                parents = props.get("Parent Folder", {}).get("relation", [])
                parent_id = parents[0]["id"].replace("-", "") if parents else None
                if name:
                    self._folder_cache[(name, parent_id)] = nid
                    if (name, None) not in self._folder_cache:
                        self._folder_cache[(name, None)] = nid
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

        # Double-check Notion database before creating to prevent duplicate folder creation
        try:
            filter_payload = {
                "and": [
                    {"property": "Name", "title": {"equals": name}},
                    {"property": "Type", "select": {"equals": "Folder"}},
                ]
            }
            for existing in self.query_all(filter_payload):
                props = existing.get("properties", {})
                if is_page_archived(props):
                    continue
                parents = props.get("Parent Folder", {}).get("relation", [])
                existing_pid = parents[0]["id"].replace("-", "") if parents else None
                if existing_pid == parent_id or (not existing_pid and not parent_id):
                    ex_id = existing["id"].replace("-", "")
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

        # Some Notion setups don't allow sub-item relation hierarchy — retry without it
        if notion_id is None and parent_id:
            props_no_parent = {k: v for k, v in props.items() if k != "Parent Folder"}
            notion_id = self.create_page(props_no_parent, icon_emoji=emoji)

        if notion_id:
            cloud_url = f"https://www.notion.so/{notion_id}"
            self.update_page(notion_id, {"Open in Browser": {"url": cloud_url}})
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
