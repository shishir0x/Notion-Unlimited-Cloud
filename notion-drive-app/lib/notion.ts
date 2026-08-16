import { Client } from "@notionhq/client";

export const notion = new Client({ auth: process.env.NOTION_TOKEN });
export const DB_ID = process.env.NOTION_DATABASE_ID!;

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
}

// ── Retry helper ───────────────────────────────────────────────────────────
async function withRetry<T>(fn: () => Promise<T>, maxRetries = 3): Promise<T> {
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      return await fn();
    } catch (err: unknown) {
      const status = (err as { status?: number }).status;
      if (status === 429 && attempt < maxRetries - 1) {
        await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
      } else {
        throw err;
      }
    }
  }
  throw new Error("Max retries exceeded");
}

// ── Page → DriveItem ───────────────────────────────────────────────────────
export function pageToItem(page: Record<string, unknown>): DriveItem {
  const props = (page.properties || {}) as Record<string, unknown>;

  const title =
    (props["Name"] as { title?: Array<{ plain_text?: string }> })?.title?.[0]
      ?.plain_text ?? "Untitled";

  const typeVal = (
    props["Type"] as { select?: { name?: string } | null }
  )?.select?.name?.toLowerCase();
  const type: "file" | "folder" =
    typeVal === "folder" ? "folder" : "file";

  const fileType =
    (props["File Type"] as { select?: { name?: string } | null })?.select
      ?.name ?? "Other";

  const extension =
    (
      props["Extension"] as {
        rich_text?: Array<{ plain_text?: string }>;
      }
    )?.rich_text?.[0]?.plain_text ?? "";

  const sizeMb =
    (props["File Size"] as { number?: number | null })?.number ?? 0;

  const parentRelation = (
    props["Parent Folder"] as {
      relation?: Array<{ id?: string }>;
    }
  )?.relation;
  const parentId =
    parentRelation && parentRelation.length > 0 && parentRelation[0].id
      ? parentRelation[0].id.replace(/-/g, "")
      : null;

  const starred =
    (props["Favorite"] as { checkbox?: boolean })?.checkbox ?? false;
  const archived =
    (props["Archived"] as { checkbox?: boolean })?.checkbox ?? false;

  const filesArr = (
    props["Files"] as {
      files?: Array<{
        type: string;
        file?: { url: string };
        external?: { url: string };
      }>;
    }
  )?.files;
  let fileUrl: string | undefined;
  if (filesArr && filesArr.length > 0) {
    const f = filesArr[0];
    fileUrl = f.file?.url ?? f.external?.url;
  }

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
  };
}

// ── Query database via REST / SDK request ─────────────────────────────────
export async function queryDatabase(
  filter?: Record<string, unknown>,
  sorts?: Array<Record<string, unknown>>,
  pageSize = 100
): Promise<DriveItem[]> {
  const items: DriveItem[] = [];
  let cursor: string | undefined;

  do {
    const body: Record<string, unknown> = {
      page_size: pageSize,
      ...(filter ? { filter } : {}),
      ...(sorts ? { sorts } : {}),
      ...(cursor ? { start_cursor: cursor } : {}),
    };

    const resp = (await withRetry(() =>
      notion.request({
        path: `databases/${DB_ID}/query`,
        method: "post",
        body,
      })
    )) as { results: Array<Record<string, unknown>>; has_more: boolean; next_cursor?: string | null };

    for (const page of resp.results || []) {
      if (page.object === "page") {
        items.push(pageToItem(page));
      }
    }
    cursor = resp.has_more ? (resp.next_cursor ?? undefined) : undefined;
  } while (cursor);

  return items;
}

// ── Get page blocks to find file URL ──────────────────────────────────────
export async function getFileUrlFromPage(pageId: string): Promise<string | null> {
  try {
    const page = (await withRetry(() =>
      notion.pages.retrieve({ page_id: pageId })
    )) as Record<string, unknown>;
    const item = pageToItem(page);
    if (item.fileUrl) return item.fileUrl;

    const blocks = (await withRetry(() =>
      notion.blocks.children.list({ block_id: pageId, page_size: 50 })
    )) as { results: Array<Record<string, unknown>> };

    for (const block of blocks.results || []) {
      const btype = String(block.type || "");
      if (["image", "file", "video", "pdf"].includes(btype)) {
        const obj = block[btype] as {
          type?: string;
          file?: { url: string };
          external?: { url: string };
        };
        const url = obj?.file?.url ?? obj?.external?.url;
        if (url) return url;
      }
    }
  } catch {
    // ignore
  }
  return null;
}

// ── Create folder ──────────────────────────────────────────────────────────
export async function createFolder(name: string, parentId?: string) {
  return withRetry(() =>
    notion.pages.create({
      parent: { database_id: DB_ID },
      properties: {
        Name: { title: [{ text: { content: name } }] },
        Type: { select: { name: "Folder" } },
        ...(parentId
          ? {
              "Parent Folder": {
                relation: [{ id: parentId }],
              },
            }
          : {}),
      } as never,
    })
  );
}

// ── Star / Archive / Rename ────────────────────────────────────────────────
export async function updateItem(
  pageId: string,
  updates: { starred?: boolean; archived?: boolean; name?: string }
) {
  const props: Record<string, unknown> = {};
  if (updates.starred !== undefined)
    props["Favorite"] = { checkbox: updates.starred };
  if (updates.archived !== undefined)
    props["Archived"] = { checkbox: updates.archived };
  if (updates.name !== undefined)
    props["Name"] = { title: [{ text: { content: updates.name } }] };

  return withRetry(() =>
    notion.pages.update({ page_id: pageId, properties: props as never })
  );
}

// ── Move (update parent relation) ──────────────────────────────────────────
export async function moveItem(pageId: string, newParentId: string | null) {
  return withRetry(() =>
    notion.pages.update({
      page_id: pageId,
      properties: {
        "Parent Folder": {
          relation: newParentId ? [{ id: newParentId }] : [],
        },
      } as never,
    })
  );
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
