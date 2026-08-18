import { NextRequest, NextResponse } from "next/server";
import { getChildren, getBreadcrumbs, type NotionDriveItem } from "@/lib/notion";

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
    const folderId = searchParams.get("folder_id") ?? searchParams.get("folder");
    const sort = searchParams.get("sort") ?? "name";
    const order = (searchParams.get("order") ?? "asc") as "asc" | "desc";
    const limit = Math.min(Number(searchParams.get("limit") ?? "200"), 500);
    const offset = Number(searchParams.get("offset") ?? "0");

    const [folderResult, fileResult] = await Promise.all([
      getChildren(folderId, { sort, order, typeFilter: "Folder", limit: 500 }),
      getChildren(folderId, { sort, order, typeFilter: "File", offset, limit }),
    ]);

    const breadcrumbs = folderId ? await getBreadcrumbs(folderId) : [];

    return NextResponse.json({
      items: [
        ...folderResult.folders.map(normalizeForClient),
        ...fileResult.files.map(normalizeForClient),
      ],
      breadcrumbs,
      total: folderResult.folders.length + fileResult.files.length,
      hasMore: folderResult.hasMore || fileResult.hasMore,
      version: Date.now(),
    });
  } catch (err) {
    const status = (err as { status?: number }).status ?? 500;
    return NextResponse.json(
      { error: status === 401 ? "Unauthorized" : `Drive unavailable: ${String(err)}` },
      { status },
    );
  }
}
