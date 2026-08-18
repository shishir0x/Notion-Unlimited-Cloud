"""
core/local_index.py — SQLite-backed local metadata index for scalable folder browsing.

Provides fast, indexed queries for:
- Folder contents (children by parent_id)
- Search across all items
- Recent files
- Starred items
- Pagination with cursor

This eliminates the need to scan physical disks or query Notion for every UI action.
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from core.config import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / ".notion_drive_index.db"


def get_connection() -> sqlite3.Connection:
    """Get a thread-local SQLite connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrency
    conn.execute("PRAGMA synchronous=NORMAL")  # Balance safety/speed
    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
    # Multiple writer threads (watcher, async cache upserts, request handlers)
    # collide on writes; wait instead of failing with "database is locked".
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_connection()
    try:
        # Add the archived column if it is missing (migration for older DBs)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(items)").fetchall()}
        if cols and "archived" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN archived INTEGER DEFAULT 0")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('File', 'Folder')),
                extension TEXT DEFAULT '',
                size_mb REAL DEFAULT 0,
                size_bytes INTEGER DEFAULT 0,
                mtime REAL DEFAULT 0,
                ctime REAL DEFAULT 0,
                created_time TEXT DEFAULT '',
                last_edited_time TEXT DEFAULT '',
                parent_id TEXT,
                local_path TEXT UNIQUE,
                storage_root TEXT DEFAULT '',
                notion_id TEXT,
                sync_status TEXT DEFAULT 'synced' CHECK(sync_status IN ('synced', 'pending', 'conflict', 'error')),
                starred INTEGER DEFAULT 0,
                item_count INTEGER DEFAULT 0,
                last_seen REAL DEFAULT 0
            );
            
            CREATE INDEX IF NOT EXISTS idx_parent ON items(parent_id);
            CREATE INDEX IF NOT EXISTS idx_type ON items(type);
            CREATE INDEX IF NOT EXISTS idx_name ON items(name);
            CREATE INDEX IF NOT EXISTS idx_mtime ON items(mtime DESC);
            CREATE INDEX IF NOT EXISTS idx_storage ON items(storage_root);
            CREATE INDEX IF NOT EXISTS idx_sync ON items(sync_status);
            CREATE INDEX IF NOT EXISTS idx_starred ON items(starred);
            CREATE INDEX IF NOT EXISTS idx_local_path ON items(local_path);
            CREATE INDEX IF NOT EXISTS idx_archived ON items(archived);
            
            CREATE TABLE IF NOT EXISTS sync_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT UNIQUE NOT NULL,
                file_id TEXT,
                operation TEXT NOT NULL CHECK(operation IN ('create', 'update', 'delete', 'move', 'rename')),
                priority INTEGER DEFAULT 0,
                timestamp REAL NOT NULL,
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 5,
                status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'processing', 'completed', 'failed', 'retrying', 'conflict', 'cancelled')),
                payload TEXT,
                error TEXT,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            );
            
            CREATE INDEX IF NOT EXISTS sq_status ON sync_queue(status);
            CREATE INDEX IF NOT EXISTS sq_priority ON sync_queue(priority DESC, timestamp ASC);
            
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                item_id TEXT,
                data TEXT,
                timestamp REAL DEFAULT (strftime('%s', 'now'))
            );
            
            CREATE INDEX IF NOT EXISTS ev_type_time ON events(event_type, timestamp DESC);
        """)
        conn.commit()
    finally:
        conn.close()


def upsert_item(item: Dict[str, Any]):
    """Insert or update a single item in the index."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO items (id, name, type, extension, size_mb, size_bytes, mtime, ctime,
                              created_time, last_edited_time, parent_id, local_path, storage_root,
                              notion_id, sync_status, starred, archived, item_count, last_seen)
            VALUES (:id, :name, :type, :extension, :size_mb, :size_bytes, :mtime, :ctime,
                    :created_time, :last_edited_time, :parent_id, :local_path, :storage_root,
                    :notion_id, :sync_status, :starred, :archived, :item_count, :last_seen)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                size_mb=excluded.size_mb,
                size_bytes=excluded.size_bytes,
                mtime=excluded.mtime,
                ctime=excluded.ctime,
                last_edited_time=excluded.last_edited_time,
                parent_id=excluded.parent_id,
                local_path=excluded.local_path,
                sync_status=excluded.sync_status,
                starred=excluded.starred,
                archived=excluded.archived,
                item_count=excluded.item_count,
                last_seen=excluded.last_seen
        """, {
            'id': item.get('id'),
            'name': item.get('name', ''),
            'type': item.get('type', 'File'),
            'extension': item.get('extension', ''),
            'size_mb': item.get('size_mb', 0),
            'size_bytes': item.get('size_bytes', 0),
            'mtime': item.get('mtime', 0),
            'ctime': item.get('ctime', 0),
            'created_time': item.get('created_time', ''),
            'last_edited_time': item.get('last_edited_time', ''),
            'parent_id': item.get('parent_id'),
            'local_path': item.get('local_path', ''),
            'storage_root': item.get('storage_root', ''),
            'notion_id': item.get('notion_id', item.get('id', '')),
            'sync_status': item.get('sync_status', 'synced'),
            'starred': 1 if item.get('starred') else 0,
            'archived': 1 if item.get('archived') else 0,
            'item_count': item.get('item_count', 0),
            'last_seen': time.time(),
        })
        conn.commit()
    finally:
        conn.close()


def upsert_many(items: List[Dict[str, Any]]):
    """Batch upsert for better performance."""
    if not items:
        return
    conn = get_connection()
    try:
        data = []
        for item in items:
            data.append({
                'id': item.get('id'),
                'name': item.get('name', ''),
                'type': item.get('type', 'File'),
                'extension': item.get('extension', ''),
                'size_mb': item.get('size_mb', 0),
                'size_bytes': item.get('size_bytes', 0),
                'mtime': item.get('mtime', 0),
                'ctime': item.get('ctime', 0),
                'created_time': item.get('created_time', ''),
                'last_edited_time': item.get('last_edited_time', ''),
                'parent_id': item.get('parent_id'),
                'local_path': item.get('local_path') or None,  # normalize '' to NULL
                'storage_root': item.get('storage_root', ''),
                'notion_id': item.get('notion_id', item.get('id', '')),
                'sync_status': item.get('sync_status', 'synced'),
                'starred': 1 if item.get('starred') else 0,
                'archived': 1 if item.get('archived') else 0,
                'item_count': item.get('item_count', 0),
                'last_seen': time.time(),
            })
        conn.executemany("""
            INSERT OR REPLACE INTO items (id, name, type, extension, size_mb, size_bytes, mtime, ctime,
                              created_time, last_edited_time, parent_id, local_path, storage_root,
                              notion_id, sync_status, starred, archived, item_count, last_seen)
            VALUES (:id, :name, :type, :extension, :size_mb, :size_bytes, :mtime, :ctime,
                    :created_time, :last_edited_time, :parent_id, :local_path, :storage_root,
                    :notion_id, :sync_status, :starred, :archived, :item_count, :last_seen)
        """, data)
        conn.commit()
    finally:
        conn.close()


def get_children(parent_id: Optional[str] = None, offset: int = 0, limit: int = 200,
                 sort: str = 'name', order: str = 'asc', type_filter: str = '',
                 include_archived: bool = False) -> Tuple[List[Dict], int, bool]:
    """
    Get children of a folder (or root items if parent_id is None).
    Returns (items, total_count, has_more).
    """
    conn = get_connection()
    try:
        # Build query
        where = []
        params = {}
        
        if parent_id:
            where.append("parent_id = :parent_id")
            params['parent_id'] = parent_id
        else:
            where.append("(parent_id IS NULL OR parent_id = '')")
        
        if type_filter:
            where.append("type = :type_filter")
            params['type_filter'] = type_filter.capitalize()
        
        if not include_archived:
            where.append("archived = 0")
        
        where_sql = " AND ".join(where) if where else "1=1"
        sort_dir = "DESC" if order == 'desc' else "ASC"
        
        # Validate sort key to prevent SQL injection
        valid_sorts = {'name', 'mtime', 'size_bytes', 'size_mb', 'created_time', 'type'}
        sort_col = sort if sort in valid_sorts else 'name'
        
        # Get total count
        count_row = conn.execute(f"SELECT COUNT(*) FROM items WHERE {where_sql}", params).fetchone()
        total = count_row[0] if count_row else 0
        
        # Get items
        rows = conn.execute(f"""
            SELECT items.id, items.name, items.type, items.extension, items.size_mb, items.size_bytes,
                   items.mtime, items.ctime, items.created_time, items.last_edited_time, items.parent_id,
                   items.local_path, items.storage_root, items.notion_id, items.sync_status, items.starred, items.archived,
                   CASE WHEN items.type = 'Folder' 
                        THEN (SELECT COUNT(*) FROM items child WHERE child.parent_id = items.id AND child.archived = 0)
                        ELSE items.item_count
                   END AS item_count,
                   items.last_seen
            FROM items 
            WHERE {where_sql}
            ORDER BY {sort_col} {sort_dir}, name {sort_dir}
            LIMIT :limit OFFSET :offset
        """, {**params, 'limit': limit, 'offset': offset}).fetchall()
        
        items = [dict(row) for row in rows]
        has_more = (offset + limit) < total
        return items, total, has_more
    finally:
        conn.close()


def search_items(query: str, category: str = 'all', limit: int = 60) -> Tuple[List[Dict], List[Dict]]:
    """Search items by name with category filtering. Returns (folders, files)."""
    if not query and category == 'all':
        return [], []
    
    conn = get_connection()
    try:
        search_term = f"%{query.lower()}%" if query else "%"
        params: Dict[str, Any] = {'q': search_term, 'limit': limit}
        
        where_clauses = ["LOWER(name) LIKE :q", "archived = 0"]
        
        CATEGORY_EXTENSIONS = {
            'image': ("'.jpg'", "'.jpeg'", "'.png'", "'.webp'", "'.gif'", "'.svg'", "'.bmp'", "'.ico'"),
            'document': ("'.pdf'", "'.doc'", "'.docx'", "'.txt'", "'.rtf'", "'.odt'", "'.pages'", "'.xls'", "'.xlsx'", "'.csv'", "'.ppt'", "'.pptx'"),
            'video': ("'.mp4'", "'.mkv'", "'.mov'", "'.webm'", "'.avi'", "'.flv'", "'.wmv'"),
            'audio': ("'.mp3'", "'.wav'", "'.ogg'", "'.m4a'", "'.flac'", "'.opus'", "'.aac'"),
            'code': ("'.py'", "'.js'", "'.ts'", "'.html'", "'.css'", "'.json'", "'.yaml'", "'.yml'", "'.sh'", "'.sql'", "'.java'", "'.cpp'", "'.c'", "'.go'", "'.rs'"),
        }
        
        cat_lower = category.lower()
        if cat_lower == 'folder':
            where_clauses.append("type = 'Folder'")
        elif cat_lower in CATEGORY_EXTENSIONS:
            ext_list = ", ".join(CATEGORY_EXTENSIONS[cat_lower])
            where_clauses.append(f"(type = 'File' AND LOWER(extension) IN ({ext_list}))")
        
        where_sql = " AND ".join(where_clauses)
        
        rows = conn.execute(f"""
            SELECT * FROM items 
            WHERE {where_sql}
            ORDER BY 
                CASE WHEN type = 'Folder' THEN 0 ELSE 1 END,
                mtime DESC
            LIMIT :limit
        """, params).fetchall()
        
        folders = [dict(r) for r in rows if r['type'] == 'Folder']
        files = [dict(r) for r in rows if r['type'] == 'File']
        return folders, files
    finally:
        conn.close()


def get_recent(limit: int = 50) -> List[Dict]:
    """Get recently accessed files."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM items 
            WHERE type = 'File' AND last_seen > 0 AND archived = 0
            ORDER BY last_seen DESC
            LIMIT :limit
        """, {'limit': limit}).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_starred() -> List[Dict]:
    """Get starred items."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM items 
            WHERE starred = 1 AND archived = 0
            ORDER BY type, name
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_trash(limit: int = 200) -> List[Dict]:
    """Get archived (trashed) items."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM items 
            WHERE archived = 1
            ORDER BY last_edited_time DESC
            LIMIT :limit
        """, {'limit': limit}).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_archived(item_id: str, archived: bool = True):
    """Mark an item as archived (trash) or restore it in the index."""
    conn = get_connection()
    try:
        conn.execute("UPDATE items SET archived = ? WHERE id = ?", (1 if archived else 0, item_id))
        conn.commit()
    finally:
        conn.close()


def get_breadcrumbs(item_id: str) -> List[Dict]:
    """Get breadcrumb path from root to item."""
    conn = get_connection()
    try:
        breadcrumbs = []
        current = item_id
        seen = set()
        
        while current and current not in seen:
            seen.add(current)
            row = conn.execute("SELECT id, name, parent_id FROM items WHERE id = ?", (current,)).fetchone()
            if not row:
                break
            breadcrumbs.insert(0, {'id': row['id'], 'name': row['name']})
            current = row['parent_id']
        
        return breadcrumbs
    finally:
        conn.close()


def delete_item(item_id: str):
    """Remove an item from the index."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()


def delete_item_by_path(path: str):
    """Remove an item from the index by its local path."""
    if not path:
        return
    conn = get_connection()
    try:
        conn.execute("DELETE FROM items WHERE local_path = ?", (path,))
        conn.commit()
    finally:
        conn.close()


def delete_items_by_notion_id(notion_id: str):
    """Remove all items with a given notion_id."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM items WHERE notion_id = ?", (notion_id,))
        conn.commit()
    finally:
        conn.close()


def get_stats() -> Dict[str, Any]:
    """Get index statistics."""
    conn = get_connection()
    try:
        total_files = conn.execute("SELECT COUNT(*) FROM items WHERE type = 'File'").fetchone()[0]
        total_folders = conn.execute("SELECT COUNT(*) FROM items WHERE type = 'Folder'").fetchone()[0]
        total_size = conn.execute("SELECT SUM(size_bytes) FROM items WHERE type = 'File'").fetchone()[0] or 0
        by_storage = conn.execute("""
            SELECT storage_root, COUNT(*) as cnt, SUM(size_bytes) as size 
            FROM items 
            WHERE type = 'File' AND storage_root != ''
            GROUP BY storage_root
        """).fetchall()
        
        return {
            'total_files': total_files,
            'total_folders': total_folders,
            'total_size_bytes': total_size,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'by_storage': [dict(r) for r in by_storage]
        }
    finally:
        conn.close()


def add_sync_op(operation_id: str, file_id: str, operation: str, priority: int = 0,
                payload: str = '', max_attempts: int = 5) -> int:
    """Add an operation to the sync queue. Returns queue ID."""
    conn = get_connection()
    try:
        cursor = conn.execute("""
            INSERT INTO sync_queue (operation_id, file_id, operation, priority, timestamp, payload, max_attempts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (operation_id, file_id, operation, priority, time.time(), payload, max_attempts))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_next_sync_op() -> Optional[Dict]:
    """Get the next pending operation from the queue (ordered by priority, then time)."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT * FROM sync_queue 
            WHERE status = 'pending' AND attempts < max_attempts
            ORDER BY priority DESC, timestamp ASC
            LIMIT 1
        """).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_sync_op(queue_id: int, **kwargs):
    """Update a sync queue operation."""
    if not kwargs:
        return
    conn = get_connection()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
        values = list(kwargs.values()) + [queue_id]
        conn.execute(f"UPDATE sync_queue SET {set_clause} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


def record_event(event_type: str, item_id: Optional[str] = None, data: Optional[str] = None):
    """Record an event for observability."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO events (event_type, item_id, data)
            VALUES (?, ?, ?)
        """, (event_type, item_id, data))
        # Keep only last 10000 events
        conn.execute("""
            DELETE FROM events WHERE id NOT IN (
                SELECT id FROM events ORDER BY timestamp DESC LIMIT 10000
            )
        """)
        conn.commit()
    finally:
        conn.close()


def get_pending_count() -> int:
    """Get count of pending sync operations."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) FROM sync_queue WHERE status = 'pending'").fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def prune_stale_items(max_age_days: int = 30):
    """Remove items not seen in max_age_days."""
    conn = get_connection()
    try:
        cutoff = time.time() - (max_age_days * 86400)
        cursor = conn.execute("DELETE FROM items WHERE last_seen < ? AND type = 'File'", (cutoff,))
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()


# Initialize on import
init_db()