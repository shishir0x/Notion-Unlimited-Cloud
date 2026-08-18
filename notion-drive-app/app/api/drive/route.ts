import { NextRequest, NextResponse } from "next/server";
import {
  getFolderChildren,
  getBreadcrumbs,
  getRecent,
  getStarred,
  getTrash,
  syncFromNotion,
  getItemCount,
  type DbItem,
} from "@/lib/cache";

export const dynamic = "force-dynamic";

function toClient(item: DbItem) {
  return {
    id: item.id,
    name: item.name,
    type: item.type,
    fileType: item.file_type,
    extension: item.extension,
    sizeMb: item.size_mb,
    parentId: item.parent_id,
    starred: item.starred === 1,
    archived: item.archived === 1,
    createdAt: item.created_at,
    modifiedAt: item.modified_at,
    notionUrl: item.notion_url,
    fileUrl: item.file_url,
    description: item.description || "",
  };
}

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const view = searchParams.get("view") ?? "folder";
  const folderId = searchParams.get("folder") ?? null;
  const sort = (searchParams.get("sort") ?? "name") as "name" | "size" | "date";
  const dir = (searchParams.get("dir") ?? "asc") as "asc" | "desc";
  const forceSync = searchParams.get("sync") === "1";

  // If user explicitly requested sync or database is completely empty, perform sync
  const count = getItemCount();
  if (count === 0 && forceSync) {
    await syncFromNotion(true).catch(() => {});
  } else if (forceSync) {
    syncFromNotion(true).catch(() => {});
  }

  if (view === "recent") {
    const items = getRecent(30).map(toClient);
    return NextResponse.json({ items, breadcrumbs: [] });
  }
  if (view === "starred") {
    const items = getStarred().map(toClient);
    return NextResponse.json({ items, breadcrumbs: [] });
  }
  if (view === "trash") {
    const items = getTrash().map(toClient);
    return NextResponse.json({ items, breadcrumbs: [] });
  }

  const items = getFolderChildren(folderId, sort, dir).map(toClient);
  const breadcrumbs = folderId ? getBreadcrumbs(folderId).map(toClient) : [];
  return NextResponse.json({ items, breadcrumbs });
}
