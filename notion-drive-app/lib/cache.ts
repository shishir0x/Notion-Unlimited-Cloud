import Database from "better-sqlite3";
import path from "path";
import { queryDatabaseStream, type DriveItem } from "./notion";

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
  if (!items || items.length === 0) return;
  const db = getDb();
  const upsert = db.prepare(`
    INSERT INTO items (id, name, type, file_type, extension, size_mb, parent_id, starred, archived, created_at, modified_at, notion_url, file_url, synced_at)
    VALUES (@id, @name, @type, @fileType, @extension, @sizeMb, @parentId, @starred, @archived, @createdAt, @modifiedAt, @notionUrl, @fileUrl, @syncedAt)
    ON CONFLICT(id) DO UPDATE SET
      name=excluded.name, type=excluded.type, file_type=excluded.file_type,
      extension=excluded.extension, size_mb=excluded.size_mb, parent_id=excluded.parent_id,
      starred=excluded.starred, archived=excluded.archived, modified_at=excluded.modified_at,
      notion_url=excluded.notion_url, file_url=excluded.file_url,
      synced_at=excluded.synced_at
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
        parentId: item.parentId || null,
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

export function getItemCount(): number {
  const db = getDb();
  const res = db.prepare("SELECT COUNT(*) as count FROM items WHERE archived=0").get() as { count: number };
  return res?.count || 0;
}

// ── Storage Device Root IDs ────────────────────────────────────────────────
export const DEVICE_ROOTS = {
  DISK_C: "device-disk-c",
  DISK_D: "device-disk-d",
  PHONE: "device-phone",
  SDCARD: "device-sdcard",
};

// Known Notion IDs for synced containers
export const KNOWN_CONTAINERS = {
  PHONE: "3bd3d81b2f3681dd92bbe3ec6dc917eb",
  SDCARD: "3bd3d81b2f3681dbb28beffde4cc3b67",
  DISK_C: "3bd3d81b2f3681a7b3e0eed40b9b5153",
};

export function getDeviceRootItems(): DbItem[] {
  const now = new Date().toISOString();
  return [
    {
      id: DEVICE_ROOTS.DISK_C,
      name: "Local Disk (C:)",
      type: "folder",
      file_type: "Device",
      extension: "",
      size_mb: 0,
      parent_id: null,
      starred: 0,
      archived: 0,
      created_at: now,
      modified_at: now,
      notion_url: "",
      file_url: null,
      synced_at: Date.now(),
    },
    {
      id: DEVICE_ROOTS.DISK_D,
      name: "Local Disk (D:)",
      type: "folder",
      file_type: "Device",
      extension: "",
      size_mb: 0,
      parent_id: null,
      starred: 0,
      archived: 0,
      created_at: now,
      modified_at: now,
      notion_url: "",
      file_url: null,
      synced_at: Date.now(),
    },
    {
      id: DEVICE_ROOTS.PHONE,
      name: "Phone (Internal Storage)",
      type: "folder",
      file_type: "Device",
      extension: "",
      size_mb: 0,
      parent_id: null,
      starred: 0,
      archived: 0,
      created_at: now,
      modified_at: now,
      notion_url: "",
      file_url: null,
      synced_at: Date.now(),
    },
    {
      id: DEVICE_ROOTS.SDCARD,
      name: "SD Card",
      type: "folder",
      file_type: "Device",
      extension: "",
      size_mb: 0,
      parent_id: null,
      starred: 0,
      archived: 0,
      created_at: now,
      modified_at: now,
      notion_url: "",
      file_url: null,
      synced_at: Date.now(),
    },
  ];
}

export function getFolderChildren(
  parentId: string | null,
  sort: "name" | "size" | "date" = "name",
  dir: "asc" | "desc" = "asc"
): DbItem[] {
  const db = getDb();
  const orderMap = { name: "name", size: "size_mb", date: "modified_at" };
  const col = orderMap[sort] || "name";
  const direction = dir.toUpperCase() === "DESC" ? "DESC" : "ASC";

  // Root view: display the 4 storage device drives
  if (!parentId) {
    return getDeviceRootItems();
  }

  // 1. Local Disk (C:)
  if (parentId === DEVICE_ROOTS.DISK_C || parentId === KNOWN_CONTAINERS.DISK_C) {
    return db
      .prepare(`SELECT * FROM items WHERE parent_id=? AND archived=0 ORDER BY type DESC, ${col} ${direction}`)
      .all(KNOWN_CONTAINERS.DISK_C) as DbItem[];
  }

  // 2. Local Disk (D:)
  if (parentId === DEVICE_ROOTS.DISK_D) {
    const diskDRow = db.prepare("SELECT id FROM items WHERE name LIKE '%Local Disk (D:)%' AND type='folder' LIMIT 1").get() as { id: string } | undefined;
    if (diskDRow?.id) {
      return db.prepare(`SELECT * FROM items WHERE parent_id=? AND archived=0 ORDER BY type DESC, ${col} ${direction}`).all(diskDRow.id) as DbItem[];
    }
    return [];
  }

  // 3. Phone (Internal Storage)
  if (parentId === DEVICE_ROOTS.PHONE || parentId === KNOWN_CONTAINERS.PHONE) {
    return db
      .prepare(`SELECT * FROM items WHERE parent_id=? AND archived=0 ORDER BY type DESC, ${col} ${direction}`)
      .all(KNOWN_CONTAINERS.PHONE) as DbItem[];
  }

  // 4. SD Card (External Storage)
  if (parentId === DEVICE_ROOTS.SDCARD || parentId === KNOWN_CONTAINERS.SDCARD) {
    return db
      .prepare(`SELECT * FROM items WHERE parent_id=? AND archived=0 ORDER BY type DESC, ${col} ${direction}`)
      .all(KNOWN_CONTAINERS.SDCARD) as DbItem[];
  }

  // Any folder drill-down (DCIM, Camera, WhatsApp, Users, notion-drive-app, etc.)
  return db
    .prepare(`SELECT * FROM items WHERE parent_id=? AND archived=0 ORDER BY type DESC, ${col} ${direction}`)
    .all(parentId) as DbItem[];
}

export function searchItems(query: string, fileType?: string): DbItem[] {
  const db = getDb();
  try {
    const cleanQ = query.replace(/[^\w\s.-]/g, "").trim();
    if (!cleanQ) return [];
    let sql = `
      SELECT i.* FROM items i
      WHERE (i.name LIKE ? OR i.extension LIKE ?) AND i.archived=0
    `;
    const params: unknown[] = [`%${cleanQ}%`, `%${cleanQ}%`];
    if (fileType) {
      sql += " AND i.file_type=?";
      params.push(fileType);
    }
    sql += " ORDER BY i.type DESC, i.name ASC LIMIT 100";
    return db.prepare(sql).all(...params) as DbItem[];
  } catch (err) {
    console.error("Search error:", err);
    return [];
  }
}

export function getStats() {
  const db = getDb();
  const row = db
    .prepare(
      "SELECT COUNT(*) as total_files, COALESCE(SUM(size_mb),0) as total_mb FROM items WHERE archived=0"
    )
    .get() as { total_files: number; total_mb: number };
  return row || { total_files: 0, total_mb: 0 };
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

  // Check if leafId is a device root itself
  for (const root of getDeviceRootItems()) {
    if (root.id === leafId) {
      return [root];
    }
  }

  let current: DbItem | undefined = db.prepare("SELECT * FROM items WHERE id=?").get(leafId) as DbItem;
  let depth = 0;
  while (current && depth < 20) {
    trail.unshift(current);
    const pId = current.parent_id;
    if (!pId) break;

    // Check if parent connects to a device container
    if (pId === KNOWN_CONTAINERS.PHONE || pId === DEVICE_ROOTS.PHONE) {
      const phoneRoot = getDeviceRootItems().find((r) => r.id === DEVICE_ROOTS.PHONE);
      if (phoneRoot) trail.unshift(phoneRoot);
      break;
    }
    if (pId === KNOWN_CONTAINERS.SDCARD || pId === DEVICE_ROOTS.SDCARD) {
      const sdRoot = getDeviceRootItems().find((r) => r.id === DEVICE_ROOTS.SDCARD);
      if (sdRoot) trail.unshift(sdRoot);
      break;
    }
    if (pId === KNOWN_CONTAINERS.DISK_C || pId === DEVICE_ROOTS.DISK_C) {
      const diskCRoot = getDeviceRootItems().find((r) => r.id === DEVICE_ROOTS.DISK_C);
      if (diskCRoot) trail.unshift(diskCRoot);
      break;
    }

    current = db.prepare("SELECT * FROM items WHERE id=?").get(pId) as DbItem;
    depth++;
  }
  return trail;
}

export function getItemById(id: string): DbItem | null {
  const db = getDb();
  return (db.prepare("SELECT * FROM items WHERE id=?").get(id) as DbItem) || null;
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

// ── Background streaming sync from Notion ──────────────────────────────────
let syncRunning = false;
let lastSyncAt = 0;

export async function syncFromNotion(force = false): Promise<number> {
  if (syncRunning) return 0;
  const now = Date.now();
  if (!force && now - lastSyncAt < 30_000) return 0;
  syncRunning = true;
  let count = 0;
  try {
    count = await queryDatabaseStream(
      (batch) => {
        if (batch && batch.length > 0) {
          upsertItems(batch);
        }
      },
      undefined,
      [{ timestamp: "last_edited_time", direction: "descending" }],
      100
    );
    lastSyncAt = now;
  } catch (err) {
    console.error("Error syncing from Notion database:", err);
  } finally {
    syncRunning = false;
  }
  return count;
}
