import { NextRequest, NextResponse } from "next/server";
import { notion, DB_ID, queryDatabase } from "@/lib/notion";
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
    const page = await notion.pages.create({
      parent: { database_id: DB_ID },
      properties: {
        Name: { title: [{ text: { content: name } }] },
        Type: { select: { name: "File" } },
        Extension: { rich_text: [{ text: { content: ext } }] },
        "File Size": { number: sizeMb },
        ...(folderId
          ? { "Parent Folder": { relation: [{ id: folderId }] } }
          : {}),
      },
    });

    // For small files (< 5MB), embed content as file block
    if (file.size < 5 * 1024 * 1024) {
      const arrayBuffer = await file.arrayBuffer();
      const base64 = Buffer.from(arrayBuffer).toString("base64");
      // Notion API doesn't support direct binary upload via API — store metadata only
      // The file_url will be set when uploaded via Notion UI or S3 pre-signed URL workflow
      void base64; // suppress unused warning
    }

    // Re-index this new item
    const items = await queryDatabase({ property: "Type", select: { equals: "File" } }, undefined, 5);
    upsertItems(items);

    return NextResponse.json({ success: true, id: (page as { id: string }).id.replace(/-/g, ""), name });
  } catch (err) {
    return NextResponse.json({ success: false, error: String(err) }, { status: 500 });
  }
}
