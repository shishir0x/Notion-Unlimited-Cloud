import { NextRequest, NextResponse } from "next/server";
import { updateItem, moveItem } from "@/lib/notion";
import { upsertItems } from "@/lib/cache";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { action, ids, payload } = body as {
      action: string;
      ids: string[];
      payload?: Record<string, unknown>;
    };

    for (const id of ids) {
      if (action === "star") await updateItem(id, { starred: true });
      else if (action === "unstar") await updateItem(id, { starred: false });
      else if (action === "delete") await updateItem(id, { archived: true });
      else if (action === "restore") await updateItem(id, { archived: false });
      else if (action === "rename" && payload?.name) {
        await updateItem(id, { name: payload.name as string });
      } else if (action === "move") {
        await moveItem(id, (payload?.parentId as string) ?? null);
      }
    }
    return NextResponse.json({ success: true });
  } catch (err) {
    return NextResponse.json({ success: false, error: String(err) }, { status: 500 });
  }
}
