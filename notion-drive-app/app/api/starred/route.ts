import { NextResponse } from "next/server";
import { getStarredItems, type NotionDriveItem } from "@/lib/notion";

export const dynamic = "force-dynamic";

function normalizeForClient(item: NotionDriveItem) {
  return {
    id: item.id,
    name: item.name,
    type: item.type === "Folder" ? "folder" : "file",
    file_type: item.fileType,
    extension: item.extension,
    size_mb: item.sizeMb,
    size_bytes: item.sizeBytes,
    parent_id: item.parentId,
    starred: item.starred,
    archived: item.archived,
    mtime: 0,
    created_time: item.createdAt,
    last_edited_time: item.lastEditedAt,
    local_path: item.localPath,
    item_count: 0,
    storage_root: "Notion Cloud",
  };
}

export async function GET() {
  try {
    const items = await getStarredItems(100);
    return NextResponse.json({
      items: items.map(normalizeForClient),
      breadcrumbs: [{ id: "starred", name: "Starred" }],
      total: items.length,
    });
  } catch (err) {
    const status = (err as { status?: number }).status ?? 500;
    return NextResponse.json({ error: String(err) }, { status });
  }
}
