import { NextRequest, NextResponse } from "next/server";
import { searchItems, type DbItem } from "@/lib/cache";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const q = searchParams.get("q")?.trim() ?? "";
  const fileType = searchParams.get("type") ?? undefined;

  if (!q || q.length < 2) {
    return NextResponse.json({ items: [] });
  }

  const raw = searchItems(q, fileType);
  const items = raw.map((item: DbItem) => ({
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
  }));

  return NextResponse.json({ items });
}
