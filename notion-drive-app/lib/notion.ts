/**
 * Type-Safe, Throttled Notion REST API Client.
 * Designed to strictly adhere to Cloudflare and Notion API rate limits (< 3 req/sec),
 * with exponential backoff, delta querying, and in-depth block content retrieval.
 */

export interface DriveItem {
  id: string;
  name: string;
  type: "file" | "folder";
  fileType: string;
  extension: string;
  sizeMb: number;
  parentId: string | null;
  starred: boolean;
  archived: boolean;
  createdAt: string;
  modifiedAt: string;
  notionUrl: string;
  fileUrl?: string;
  description?: string;
}

const NOTION_VERSION = "2022-06-28";

function getCredentials() {
  const token = process.env.NOTION_TOKEN || "";
  const rawDbId = process.env.NOTION_DATABASE_ID || "";
  const dbId = rawDbId.replace(/-/g, "").trim();
  return { token, dbId };
}

// Polite request throttling to protect against Cloudflare 429
let lastRequestTime = 0;
const MIN_REQUEST_INTERVAL_MS = 340; // ~3 requests per second max

async function throttle() {
  const now = Date.now();
  const elapsed = now - lastRequestTime;
  if (elapsed < MIN_REQUEST_INTERVAL_MS) {
    await new Promise((r) => setTimeout(r, MIN_REQUEST_INTERVAL_MS - elapsed));
  }
  lastRequestTime = Date.now();
}

async function fetchNotion(path: string, options: RequestInit = {}): Promise<any> {
  const { token } = getCredentials();
  const url = path.startsWith("http") ? path : `https://api.notion.com/v1/${path.replace(/^\//, "")}`;

  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) || {}),
  };

  for (let attempt = 0; attempt < 4; attempt++) {
    await throttle();
    try {
      const res = await fetch(url, {
        ...options,
        headers,
        cache: "no-store",
      });

      if (res.status === 429) {
        const retryAfter = Number(res.headers.get("Retry-After") || (attempt + 1) * 2);
        console.warn(`[Notion API] 429 Rate limited. Cooling down for ${retryAfter}s...`);
        await new Promise((r) => setTimeout(r, retryAfter * 1000 + 500));
        continue;
      }

      if (!res.ok) {
        const errBody = await res.text();
        throw new Error(`Notion API HTTP ${res.status}: ${errBody}`);
      }

      return await res.json();
    } catch (err: unknown) {
      if (attempt === 3) throw err;
      const backoff = (attempt + 1) * 1500;
      await new Promise((r) => setTimeout(r, backoff));
    }
  }
}

// ── Page → DriveItem Transformer ───────────────────────────────────────────
export function pageToItem(page: Record<string, any>): DriveItem {
  const props = page.properties || {};

  const title = props["Name"]?.title?.[0]?.plain_text || "Untitled";

  const typeVal = props["Type"]?.select?.name?.toLowerCase();
  const type: "file" | "folder" = typeVal === "folder" ? "folder" : "file";

  const fileType = props["File Type"]?.select?.name || "Other";

  const extension =
    props["File Extension"]?.rich_text?.[0]?.plain_text ||
    props["Extension"]?.rich_text?.[0]?.plain_text ||
    "";

  const sizeMb = props["File Size"]?.number || 0;

  const parentRelation = props["Parent Folder"]?.relation;
  const parentId =
    parentRelation && parentRelation.length > 0 && parentRelation[0]?.id
      ? parentRelation[0].id.replace(/-/g, "")
      : null;

  const starred = props["Favorite"]?.checkbox ?? false;
  const archived = props["Archived"]?.checkbox ?? false;

  const filesArr = props["Files"]?.files;
  let fileUrl: string | undefined;
  if (filesArr && filesArr.length > 0) {
    fileUrl = filesArr[0].file?.url || filesArr[0].external?.url;
  }

  const description = props["Description"]?.rich_text?.[0]?.plain_text || "";

  const pageId = String(page.id || "").replace(/-/g, "");

  return {
    id: pageId,
    name: title,
    type,
    fileType,
    extension,
    sizeMb,
    parentId,
    starred,
    archived,
    createdAt: String(page.created_time || new Date().toISOString()),
    modifiedAt: String(page.last_edited_time || new Date().toISOString()),
    notionUrl: `https://www.notion.so/${pageId}`,
    fileUrl,
    description,
  };
}

// ── Stream database queries in batches ────────────────────────────────────
export async function queryDatabaseStream(
  onBatch: (items: DriveItem[], isFirst: boolean, hasMore: boolean) => void | Promise<void>,
  filter?: Record<string, unknown>,
  sorts?: Array<Record<string, unknown>>,
  pageSize = 100
): Promise<number> {
  const { dbId } = getCredentials();
  if (!dbId) return 0;

  let cursor: string | undefined;
  let total = 0;
  let isFirst = true;

  do {
    const body: Record<string, unknown> = {
      page_size: pageSize,
      ...(filter ? { filter } : {}),
      ...(sorts ? { sorts } : {}),
      ...(cursor ? { start_cursor: cursor } : {}),
    };

    const resp = await fetchNotion(`databases/${dbId}/query`, {
      method: "POST",
      body: JSON.stringify(body),
    });

    const batch: DriveItem[] = [];
    for (const page of resp.results || []) {
      if (page.object === "page") {
        batch.push(pageToItem(page));
      }
    }
    total += batch.length;
    cursor = resp.has_more ? resp.next_cursor : undefined;
    await onBatch(batch, isFirst, !!cursor);
    isFirst = false;
  } while (cursor);

  return total;
}

// ── Query database via REST ───────────────────────────────────────────────
export async function queryDatabase(
  filter?: Record<string, unknown>,
  sorts?: Array<Record<string, unknown>>,
  pageSize = 100
): Promise<DriveItem[]> {
  const items: DriveItem[] = [];
  await queryDatabaseStream(
    (batch) => {
      items.push(...batch);
    },
    filter,
    sorts,
    pageSize
  );
  return items;
}

// ── Retrieve text/code content from Notion page blocks ────────────────────
export async function retrievePageBlocksText(pageId: string): Promise<string | null> {
  try {
    const blocks = await fetchNotion(`blocks/${pageId}/children?page_size=100`);
    const texts: string[] = [];
    for (const block of blocks.results || []) {
      const btype = String(block.type || "");
      if (btype === "code") {
        const snippet = block.code?.rich_text?.[0]?.plain_text;
        if (snippet) texts.push(snippet);
      } else if (btype === "paragraph") {
        const pText = block.paragraph?.rich_text?.map((t: any) => t.plain_text).join("") || "";
        if (pText) texts.push(pText);
      }
    }
    if (texts.length > 0) {
      return texts.join("\n");
    }
  } catch (err) {
    console.warn(`[retrievePageBlocksText] Error fetching blocks for ${pageId}:`, err);
  }
  return null;
}

// ── Get page file URL or blocks ──────────────────────────────────────────
export async function getFileUrlFromPage(pageId: string): Promise<string | null> {
  try {
    const fetchWithTimeout = async () => {
      const page = await fetchNotion(`pages/${pageId}`);
      const item = pageToItem(page);
      if (item.fileUrl) return item.fileUrl;

      const blocks = await fetchNotion(`blocks/${pageId}/children?page_size=30`);
      for (const block of blocks?.results || []) {
        const btype = String(block.type || "");
        if (["image", "file", "video", "pdf", "audio"].includes(btype)) {
          const obj = block[btype];
          const url = obj?.file?.url || obj?.external?.url;
          if (url) return url;
        }
      }
      return null;
    };

    return await Promise.race([
      fetchWithTimeout(),
      new Promise<null>((r) => setTimeout(() => r(null), 3000)),
    ]);
  } catch {
    return null;
  }
}

// ── Create folder ──────────────────────────────────────────────────────────
export async function createFolder(name: string, parentId?: string) {
  const { dbId } = getCredentials();
  const properties: Record<string, any> = {
    Name: { title: [{ text: { content: name } }] },
    Type: { select: { name: "Folder" } },
  };
  if (parentId) {
    properties["Parent Folder"] = { relation: [{ id: parentId }] };
  }

  return fetchNotion("pages", {
    method: "POST",
    body: JSON.stringify({
      parent: { database_id: dbId },
      icon: { type: "emoji", emoji: "📁" },
      properties,
    }),
  });
}

// ── Create file ────────────────────────────────────────────────────────────
export async function createFile(name: string, ext: string, sizeMb: number, folderId?: string | null) {
  const { dbId } = getCredentials();
  const properties: Record<string, any> = {
    Name: { title: [{ text: { content: name } }] },
    Type: { select: { name: "File" } },
    "File Extension": { rich_text: [{ text: { content: ext } }] },
    "File Size": { number: sizeMb },
  };
  if (folderId) {
    properties["Parent Folder"] = { relation: [{ id: folderId }] };
  }

  return fetchNotion("pages", {
    method: "POST",
    body: JSON.stringify({
      parent: { database_id: dbId },
      icon: { type: "emoji", emoji: "📄" },
      properties,
    }),
  });
}

// ── Star / Archive / Rename ────────────────────────────────────────────────
export async function updateItem(
  pageId: string,
  updates: { starred?: boolean; archived?: boolean; name?: string }
) {
  const properties: Record<string, any> = {};
  if (updates.starred !== undefined) properties["Favorite"] = { checkbox: updates.starred };
  if (updates.archived !== undefined) properties["Archived"] = { checkbox: updates.archived };
  if (updates.name !== undefined) properties["Name"] = { title: [{ text: { content: updates.name } }] };

  return fetchNotion(`pages/${pageId}`, {
    method: "PATCH",
    body: JSON.stringify({ properties }),
  });
}

// ── Move (update parent relation) ──────────────────────────────────────────
export async function moveItem(pageId: string, newParentId: string | null) {
  const properties: Record<string, any> = {
    "Parent Folder": {
      relation: newParentId ? [{ id: newParentId }] : [],
    },
  };
  return fetchNotion(`pages/${pageId}`, {
    method: "PATCH",
    body: JSON.stringify({ properties }),
  });
}

// ── Get recently modified files ────────────────────────────────────────────
export async function getRecent(limit = 20): Promise<DriveItem[]> {
  return queryDatabase(
    {
      and: [
        { property: "Type", select: { equals: "File" } },
        { property: "Archived", checkbox: { equals: false } },
      ],
    },
    [{ timestamp: "last_edited_time", direction: "descending" }],
    limit
  );
}

// ── Get starred items ──────────────────────────────────────────────────────
export async function getStarred(): Promise<DriveItem[]> {
  return queryDatabase({
    and: [
      { property: "Favorite", checkbox: { equals: true } },
      { property: "Archived", checkbox: { equals: false } },
    ],
  });
}

// ── Get trash ─────────────────────────────────────────────────────────────
export async function getTrash(): Promise<DriveItem[]> {
  return queryDatabase({ property: "Archived", checkbox: { equals: true } });
}
