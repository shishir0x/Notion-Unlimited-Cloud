import { NextRequest, NextResponse } from "next/server";
import { searchItems, type NotionDriveItem } from "@/lib/notion";

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

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const q = (searchParams.get("q") ?? "").trim();
    const category = searchParams.get("cat") ?? "all";

    if (!q || q.length < 2) {
      return NextResponse.json({ items: [], total: 0 });
    }

    const results = await searchItems(q, category, 100);
    const items = [
      ...results.folders.map(normalizeForClient),
      ...results.files.map(normalizeForClient),
    ];

    return NextResponse.json({ items, total: items.length });
  } catch (err) {
    const status = (err as { status?: number }).status ?? 500;
    return NextResponse.json(
      { error: status === 401 ? "Unauthorized" : `Search unavailable: ${String(err)}` },
      { status },
    );
  }
}
