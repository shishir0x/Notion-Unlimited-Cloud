import { NextRequest, NextResponse } from "next/server";
import { createFile, queryDatabase } from "@/lib/notion";
import { upsertItems } from "@/lib/cache";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const formData = await req.formData();
    const file = formData.get("file") as File | null;
    const folderId = formData.get("folder_id") as string | null;

    if (!file) {
      return NextResponse.json({ success: false, error: "No file provided" }, { status: 400 });
    }

    const name = file.name;
    const ext = name.includes(".") ? "." + name.split(".").pop()!.toLowerCase() : "";
    const sizeMb = +(file.size / (1024 * 1024)).toFixed(3);

    // Create Notion page for this file
    const page = await createFile(name, ext, sizeMb, folderId);

    // Re-index this new item
    const items = await queryDatabase({ property: "Type", select: { equals: "File" } }, undefined, 5);
    upsertItems(items);

    const newId = (page as { id?: string })?.id?.replace(/-/g, "") || "";
    return NextResponse.json({ success: true, id: newId, name });
  } catch (err) {
    return NextResponse.json({ success: false, error: String(err) }, { status: 500 });
  }
}
