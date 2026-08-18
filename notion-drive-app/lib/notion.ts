// lib/notion.ts — Direct Notion API client for file management.
// Used by Route Handlers to query/mutate the Notion database directly.

import { Client } from "@notionhq/client";
import type { PageObjectResponse } from "@notionhq/client/build/src/api-endpoints";

// ── Notion client singleton ──────────────────────────────────────────────────

const NOTION_TOKEN = process.env.NOTION_TOKEN ?? "";
const NOTION_DATABASE_ID = (process.env.NOTION_DATABASE_ID ?? "").replace(/-/g, "");

let _client: Client | null = null;

export function getNotionClient(): Client {
  if (!_client) {
    if (!NOTION_TOKEN) throw new Error("NOTION_TOKEN is not configured");
    _client = new Client({ auth: NOTION_TOKEN });
  }
  return _client;
}

export function getDatabaseId(): string {
  if (!NOTION_DATABASE_ID) throw new Error("NOTION_DATABASE_ID is not configured");
  return NOTION_DATABASE_ID;
}

// ── Types ────────────────────────────────────────────────────────────────────

export interface NotionDriveItem {
  id: string;
  name: string;
  type: "File" | "Folder";
  fileType: string;
  extension: string;
  sizeMb: number;
  sizeBytes: number;
  parentId: string | null;
  starred: boolean;
  archived: boolean;
  description: string;
  localPath: string;
  createdAt: string;
  lastEditedAt: string;
}

export interface NotionDatabaseQueryResult {
  results: Array<Record<string, unknown>>;
  has_more: boolean;
  next_cursor: string | null;
}

/** Helper to query databases across Notion SDK versions via client.request */
async function queryDatabase(
  client: Client,
  dbId: string,
  params: Record<string, unknown>
): Promise<NotionDatabaseQueryResult> {
  const reqFn = (client as unknown as {
    request: (args: { path: string; method: string; body?: unknown }) => Promise<NotionDatabaseQueryResult>;
  }).request;

  return reqFn.call(client, {
    path: `databases/${dbId}/query`,
    method: "post",
    body: params,
  });
}

// ── Property helpers ─────────────────────────────────────────────────────────

function getTitle(props: Record<string, unknown>, key: string): string {
  const prop = props[key] as { title?: Array<{ plain_text?: string }> } | undefined;
  return prop?.title?.[0]?.plain_text ?? "";
}

function getSelect(props: Record<string, unknown>, key: string): string {
  const prop = props[key] as { select?: { name?: string } } | undefined;
  return prop?.select?.name ?? "";
}

function getRichText(props: Record<string, unknown>, key: string): string {
  const prop = props[key] as { rich_text?: Array<{ plain_text?: string }> } | undefined;
  return prop?.rich_text?.[0]?.plain_text ?? "";
}

function getNumber(props: Record<string, unknown>, key: string): number {
  const prop = props[key] as { number?: number } | undefined;
  return prop?.number ?? 0;
}

function getCheckbox(props: Record<string, unknown>, key: string): boolean {
  const prop = props[key] as { checkbox?: boolean } | undefined;
  return prop?.checkbox ?? false;
}

function getRelationId(props: Record<string, unknown>, key: string): string | null {
  const prop = props[key] as { relation?: Array<{ id?: string }> } | undefined;
  const id = prop?.relation?.[0]?.id;
  return id ? id.replace(/-/g, "") : null;
}

// ── Normalize a Notion page into a drive item ────────────────────────────────

export function normalizeNotionPage(page: PageObjectResponse | Record<string, unknown>): NotionDriveItem {
  const props = (page.properties ?? {}) as Record<string, unknown>;
  const name = getTitle(props, "Name");
  const type = (getSelect(props, "Type") || "File") as "File" | "Folder";
  const fileType = getSelect(props, "File Type");
  const extension = getRichText(props, "File Extension");
  const sizeMb = getNumber(props, "File Size");
  const description = getRichText(props, "Description");
  const starred = getCheckbox(props, "Favorite");
  const archived = getCheckbox(props, "Archived");
  const parentId = getRelationId(props, "Parent Folder");

  // Extract local path from Description field
  const localPath = description
    .replace(/^Path:\s*/, "")
    .replace(/^Local:\s*/, "")
    .replace(/\s*\(Updated\)\s*$/, "")
    .replace(/\s*\(Modified\)\s*$/, "")
    .trim();

  const sizeBytes = Math.round(sizeMb * 1024 * 1024);
  const pageId = String(page.id ?? "").replace(/-/g, "");

  return {
    id: pageId,
    name,
    type: type || "File",
    fileType: fileType || "Other",
    extension: extension || "",
    sizeMb: sizeMb || 0,
    sizeBytes,
    parentId,
    starred,
    archived,
    description,
    localPath,
    createdAt: String(page.created_time ?? ""),
    lastEditedAt: String(page.last_edited_time ?? ""),
  };
}

// ── Database queries ──────────────────────────────────────────────────────────

export interface QueryOptions {
  parentId?: string | null;
  sort?: string;
  order?: "asc" | "desc";
  typeFilter?: "File" | "Folder" | "";
  offset?: number;
  limit?: number;
}

const SORT_MAP: Record<string, string> = {
  name: "Name",
  size: "File Size",
  mtime: "last_edited_time",
  type: "Type",
  fileType: "File Type",
};

export async function queryDriveItems(
  opts: QueryOptions = {}
): Promise<{ items: NotionDriveItem[]; hasMore: boolean; nextCursor?: string }> {
  const client = getNotionClient();
  const dbId = getDatabaseId();

  const {
    parentId = null,
    sort = "name",
    order = "asc",
    typeFilter = "",
    offset = 0,
    limit = 200,
  } = opts;

  // Build filter
  const filters: unknown[] = [];

  // Exclude archived items by default
  filters.push({
    property: "Archived",
    checkbox: { equals: false },
  });

  // Filter by parent
  if (parentId) {
    filters.push({
      property: "Parent Folder",
      relation: { contains: parentId },
    });
  } else {
    // Root level: items with no parent
    filters.push({
      property: "Parent Folder",
      relation: { is_empty: true },
    });
  }

  // Filter by type
  if (typeFilter) {
    filters.push({
      property: "Type",
      select: { equals: typeFilter },
    });
  }

  const sortKey = SORT_MAP[sort] ?? "Name";
  const sortDirection = order === "desc" ? "descending" : "ascending";

  const sortConfig =
    sortKey === "last_edited_time"
      ? [{ timestamp: "last_edited_time" as const, direction: sortDirection }]
      : [{ property: sortKey, direction: sortDirection }];

  try {
    const results: NotionDriveItem[] = [];
    let cursor: string | undefined = undefined;
    let fetched = 0;
    const totalWanted = offset + limit;

    while (fetched < totalWanted) {
      const pageSize = Math.min(100, totalWanted - fetched + 50);
      const response = await queryDatabase(client, dbId, {
        filter: filters.length === 1 ? filters[0] : { and: filters },
        sorts: sortConfig,
        page_size: pageSize,
        start_cursor: cursor,
      });

      for (const page of response.results) {
        if (page && typeof page === "object" && "properties" in page) {
          fetched++;
          if (fetched > offset) {
            results.push(normalizeNotionPage(page));
          }
        }
      }

      if (!response.has_more || results.length >= limit) break;
      cursor = response.next_cursor ?? undefined;
    }

    return {
      items: results.slice(0, limit),
      hasMore: cursor !== undefined || results.length >= limit,
      nextCursor: cursor,
    };
  } catch (err) {
    console.error("Notion query error:", err);
    return { items: [], hasMore: false };
  }
}

// ── Get children of a folder ─────────────────────────────────────────────────

export async function getChildren(
  parentId: string | null,
  opts: Omit<QueryOptions, "parentId"> = {}
): Promise<{ folders: NotionDriveItem[]; files: NotionDriveItem[]; hasMore: boolean }> {
  const [folderResult, fileResult] = await Promise.all([
    queryDriveItems({ ...opts, parentId, typeFilter: "Folder", limit: 500 }),
    queryDriveItems({ ...opts, parentId, typeFilter: "File" }),
  ]);

  return {
    folders: folderResult.items,
    files: fileResult.items,
    hasMore: folderResult.hasMore || fileResult.hasMore,
  };
}

// ── Get breadcrumbs for a folder ─────────────────────────────────────────────

export async function getBreadcrumbs(
  folderId: string
): Promise<Array<{ id: string; name: string }>> {
  const client = getNotionClient();
  const crumbs: Array<{ id: string; name: string }> = [];
  let currentId = folderId;
  const seen = new Set<string>();

  while (currentId && !seen.has(currentId)) {
    seen.add(currentId);
    try {
      const page = await client.pages.retrieve({ page_id: currentId });
      if ("properties" in page) {
        const props = page.properties as Record<string, unknown>;
        const name = getTitle(props, "Name");
        crumbs.unshift({ id: currentId.replace(/-/g, ""), name });
        currentId = getRelationId(props, "Parent Folder") ?? "";
      } else {
        break;
      }
    } catch {
      break;
    }
  }

  return crumbs;
}

// ── Search ───────────────────────────────────────────────────────────────────

export async function searchItems(
  query: string,
  category: string = "all",
  limit: number = 100
): Promise<{ folders: NotionDriveItem[]; files: NotionDriveItem[] }> {
  const client = getNotionClient();
  const dbId = getDatabaseId();

  const filter: unknown[] = [
    { property: "Archived", checkbox: { equals: false } },
  ];

  if (query) {
    filter.push({
      property: "Name",
      title: { contains: query },
    });
  }

  if (category && category !== "all" && category !== "folder") {
    const categoryMap: Record<string, string[]> = {
      image: ["Image"],
      document: ["PDF", "Word", "Excel", "PowerPoint"],
      video: ["Video"],
      audio: ["Audio"],
      code: ["Code"],
    };
    const types = categoryMap[category];
    if (types) {
      filter.push({
        property: "File Type",
        select: { is_not_empty: true },
      });
    }
  }

  try {
    const response = await queryDatabase(client, dbId, {
      filter: { and: filter },
      page_size: Math.min(limit, 100),
    });

    const folders: NotionDriveItem[] = [];
    const files: NotionDriveItem[] = [];

    for (const page of response.results) {
      if (page && typeof page === "object" && "properties" in page) {
        const item = normalizeNotionPage(page);
        if (item.type === "Folder") {
          folders.push(item);
        } else {
          files.push(item);
        }
      }
    }

    return { folders, files };
  } catch (err) {
    console.error("Notion search error:", err);
    return { folders: [], files: [] };
  }
}

// ── Mutations ────────────────────────────────────────────────────────────────

export async function createPage(
  properties: Record<string, unknown>,
  parentId?: string | null
): Promise<{ id: string } | null> {
  const client = getNotionClient();
  const dbId = getDatabaseId();

  try {
    const result = await client.pages.create({
      parent: { database_id: dbId },
      properties: properties as never,
    });
    return { id: result.id.replace(/-/g, "") };
  } catch (err) {
    console.error("Create page error:", err);
    return null;
  }
}

export async function updatePage(
  pageId: string,
  properties: Record<string, unknown>
): Promise<boolean> {
  const client = getNotionClient();
  try {
    await client.pages.update({
      page_id: pageId,
      properties: properties as never,
    });
    return true;
  } catch (err) {
    console.error("Update page error:", err);
    return false;
  }
}

export async function archivePage(pageId: string): Promise<boolean> {
  return updatePage(pageId, {
    Archived: { checkbox: true },
  });
}

export async function unarchivePage(pageId: string): Promise<boolean> {
  return updatePage(pageId, {
    Archived: { checkbox: false },
  });
}

export async function permanentlyDeletePage(pageId: string): Promise<boolean> {
  const client = getNotionClient();
  try {
    await client.pages.update({
      page_id: pageId,
      archived: true,
    });
    return true;
  } catch (err) {
    console.error("Permanent delete error:", err);
    return false;
  }
}

export async function renameItem(
  pageId: string,
  newName: string
): Promise<boolean> {
  return updatePage(pageId, {
    Name: { title: [{ text: { content: newName } }] },
  });
}

export async function moveItem(
  pageId: string,
  newParentId: string | null
): Promise<boolean> {
  return updatePage(pageId, {
    "Parent Folder": newParentId
      ? { relation: [{ id: newParentId }] }
      : { relation: [] },
  });
}

export async function starItem(
  pageId: string,
  starred: boolean
): Promise<boolean> {
  return updatePage(pageId, {
    Favorite: { checkbox: starred },
  });
}

export async function createFolder(
  name: string,
  parentId?: string | null
): Promise<{ id: string; name: string } | null> {
  const properties: Record<string, unknown> = {
    Name: { title: [{ text: { content: name } }] },
    Type: { select: { name: "Folder" } },
    Favorite: { checkbox: false },
    Archived: { checkbox: false },
  };

  if (parentId) {
    properties["Parent Folder"] = { relation: [{ id: parentId }] };
  }

  const result = await createPage(properties, parentId);
  return result ? { id: result.id, name } : null;
}

// ── Starred / Recent / Trash ─────────────────────────────────────────────────

export async function getStarredItems(
  limit: number = 100
): Promise<NotionDriveItem[]> {
  const client = getNotionClient();
  const dbId = getDatabaseId();

  try {
    const response = await queryDatabase(client, dbId, {
      filter: {
        and: [
          { property: "Archived", checkbox: { equals: false } },
          { property: "Favorite", checkbox: { equals: true } },
        ],
      },
      page_size: Math.min(limit, 100),
    });

    return response.results
      .filter((p): p is Record<string, unknown> => Boolean(p && typeof p === "object" && "properties" in p))
      .map(normalizeNotionPage);
  } catch (err) {
    console.error("Starred query error:", err);
    return [];
  }
}

export async function getRecentItems(
  limit: number = 50
): Promise<NotionDriveItem[]> {
  const client = getNotionClient();
  const dbId = getDatabaseId();

  try {
    const response = await queryDatabase(client, dbId, {
      filter: {
        and: [
          { property: "Archived", checkbox: { equals: false } },
          { property: "Type", select: { equals: "File" } },
        ],
      },
      sorts: [{ timestamp: "last_edited_time", direction: "descending" }],
      page_size: Math.min(limit, 100),
    });

    return response.results
      .filter((p): p is Record<string, unknown> => Boolean(p && typeof p === "object" && "properties" in p))
      .map(normalizeNotionPage);
  } catch (err) {
    console.error("Recent query error:", err);
    return [];
  }
}

export async function getTrashItems(
  limit: number = 200
): Promise<NotionDriveItem[]> {
  const client = getNotionClient();
  const dbId = getDatabaseId();

  try {
    const response = await queryDatabase(client, dbId, {
      filter: { property: "Archived", checkbox: { equals: true } },
      page_size: Math.min(limit, 100),
    });

    return response.results
      .filter((p): p is Record<string, unknown> => Boolean(p && typeof p === "object" && "properties" in p))
      .map(normalizeNotionPage);
  } catch (err) {
    console.error("Trash query error:", err);
    return [];
  }
}

// ── Stats ────────────────────────────────────────────────────────────────────

export async function getStats(): Promise<{
  total_files: number;
  total_mb: number;
}> {
  const client = getNotionClient();
  const dbId = getDatabaseId();

  try {
    let totalFiles = 0;
    let totalMb = 0;
    let cursor: string | undefined;

    do {
      const batch = await queryDatabase(client, dbId, {
        filter: {
          and: [
            { property: "Archived", checkbox: { equals: false } },
            { property: "Type", select: { equals: "File" } },
          ],
        },
        page_size: 100,
        start_cursor: cursor,
      });

      for (const page of batch.results) {
        if (page && typeof page === "object" && "properties" in page) {
          totalFiles++;
          const props = page.properties as Record<string, unknown>;
          totalMb += getNumber(props, "File Size");
        }
      }

      cursor = batch.has_more ? batch.next_cursor ?? undefined : undefined;
    } while (cursor);

    return {
      total_files: totalFiles,
      total_mb: Math.round(totalMb * 100) / 100,
    };
  } catch (err) {
    console.error("Stats query error:", err);
    return { total_files: 0, total_mb: 0 };
  }
}
