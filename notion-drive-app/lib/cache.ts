import Database from "better-sqlite3";
import path from "path";
import { queryDatabase, type DriveItem } from "./notion";

const DB_PATH = path.join(process.cwd(), "notion_drive.db");

let _db: ReturnType<typeof Database> | null = null;

export function getDb() {
  if (!_db) {
    _db = new Database(DB_PATH);
    _db.pragma("journal_mode = WAL");
    _db.exec(`
      CREATE TABLE IF NOT EXISTS items (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        file_type TEXT DEFAULT 'Other',
        extension TEXT DEFAULT '',
        size_mb REAL DEFAULT 0,
        parent_id TEXT,
        starred INTEGER DEFAULT 0,
        archived INTEGER DEFAULT 0,
        created_at TEXT,
        modified_at TEXT,
        notion_url TEXT,
        file_url TEXT,
        synced_at INTEGER DEFAULT 0
      );
      CREATE INDEX IF NOT EXISTS idx_parent ON items(parent_id);
      CREATE INDEX IF NOT EXISTS idx_archived ON items(archived);
      CREATE INDEX IF NOT EXISTS idx_starred ON items(starred);
      CREATE INDEX IF NOT EXISTS idx_modified ON items(modified_at);
      CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
        id UNINDEXED, name, extension, file_type,
        content=items, content_rowid=rowid
      );
    `);
  }
  return _db;
}

export function upsertItems(items: DriveItem[]) {
  const db = getDb();
  const upsert = db.prepare(`
    INSERT INTO items (id, name, type, file_type, extension, size_mb, parent_id, starred, archived, created_at, modified_at, notion_url, file_url, synced_at)
    VALUES (@id, @name, @type, @fileType, @extension, @sizeMb, @parentId, @starred, @archived, @createdAt, @modifiedAt, @notionUrl, @fileUrl, @syncedAt)
    ON CONFLICT(id) DO UPDATE SET
      name=excluded.name, type=excluded.type, file_type=excluded.file_type,
      extension=excluded.extension, size_mb=excluded.size_mb, parent_id=excluded.parent_id,
      starred=excluded.starred, archived=excluded.archived, modified_at=excluded.modified_at,
      notion_url=excluded.notion_url, file_url=excluded.file_url, synced_at=excluded.synced_at
  `);
  const insertFts = db.prepare(`
    INSERT OR REPLACE INTO items_fts (id, name, extension, file_type)
    VALUES (?, ?, ?, ?)
  `);
  const tx = db.transaction(() => {
    for (const item of items) {
      upsert.run({
        ...item,
        fileType: item.fileType,
        sizeMb: item.sizeMb,
        parentId: item.parentId,
        starred: item.starred ? 1 : 0,
        archived: item.archived ? 1 : 0,
        createdAt: item.createdAt,
        modifiedAt: item.modifiedAt,
        notionUrl: item.notionUrl,
        fileUrl: item.fileUrl ?? null,
        syncedAt: Date.now(),
      });
      insertFts.run(item.id, item.name, item.extension, item.fileType);
    }
  });
  tx();
}

export function getFolderChildren(
  parentId: string | null,
  sort: "name" | "size" | "date" = "name",
  dir: "asc" | "desc" = "asc"
): DbItem[] {
  const db = getDb();
  const orderMap = { name: "name", size: "size_mb", date: "modified_at" };
  const col = orderMap[sort];
  const direction = dir.toUpperCase();

  if (parentId === null) {
    return db
      .prepare(
        `SELECT * FROM items WHERE parent_id IS NULL AND archived=0 ORDER BY type DESC, ${col} ${direction}`
      )
      .all() as DbItem[];
  }
  return db
    .prepare(
      `SELECT * FROM items WHERE parent_id=? AND archived=0 ORDER BY type DESC, ${col} ${direction}`
    )
    .all(parentId) as DbItem[];
}

export function searchItems(query: string, fileType?: string): DbItem[] {
  const db = getDb();
  let sql = `
    SELECT i.* FROM items i
    JOIN items_fts f ON i.id = f.id
    WHERE items_fts MATCH ? AND i.archived=0
  `;
  const params: unknown[] = [query + "*"];
  if (fileType) {
    sql += " AND i.file_type=?";
    params.push(fileType);
  }
  sql += " LIMIT 100";
  return db.prepare(sql).all(...params) as DbItem[];
}

export function getStats() {
  const db = getDb();
  const row = db
    .prepare(
      "SELECT COUNT(*) as total_files, COALESCE(SUM(size_mb),0) as total_mb FROM items WHERE archived=0"
    )
    .get() as { total_files: number; total_mb: number };
  return row;
}

export function getRecent(limit = 20): DbItem[] {
  return getDb()
    .prepare(
      "SELECT * FROM items WHERE type='file' AND archived=0 ORDER BY modified_at DESC LIMIT ?"
    )
    .all(limit) as DbItem[];
}

export function getStarred(): DbItem[] {
  return getDb()
    .prepare("SELECT * FROM items WHERE starred=1 AND archived=0 ORDER BY name ASC")
    .all() as DbItem[];
}

export function getTrash(): DbItem[] {
  return getDb()
    .prepare("SELECT * FROM items WHERE archived=1 ORDER BY modified_at DESC")
    .all() as DbItem[];
}

export function getBreadcrumbs(leafId: string): DbItem[] {
  const db = getDb();
  const trail: DbItem[] = [];
  let current: DbItem | undefined = db.prepare("SELECT * FROM items WHERE id=?").get(leafId) as DbItem;
  while (current) {
    trail.unshift(current);
    if (!current.parent_id) break;
    current = db.prepare("SELECT * FROM items WHERE id=?").get(current.parent_id) as DbItem;
  }
  return trail;
}

export interface DbItem {
  id: string;
  name: string;
  type: string;
  file_type: string;
  extension: string;
  size_mb: number;
  parent_id: string | null;
  starred: number;
  archived: number;
  created_at: string;
  modified_at: string;
  notion_url: string;
  file_url: string | null;
  synced_at: number;
}

// ── Background sync ────────────────────────────────────────────────────────
let syncRunning = false;
let lastSyncAt = 0;

export async function syncFromNotion(force = false) {
  if (syncRunning) return;
  const now = Date.now();
  if (!force && now - lastSyncAt < 30_000) return; // debounce 30s
  syncRunning = true;
  try {
    const items = await queryDatabase(
      undefined,
      [{ timestamp: "last_edited_time", direction: "descending" }],
      100
    );
    upsertItems(items);
    lastSyncAt = now;
  } catch {
    // ignore
  } finally {
    syncRunning = false;
  }
}
