import { NextRequest, NextResponse } from "next/server";
import {
  renameItem,
  moveItem,
  starItem,
  archivePage,
  unarchivePage,
  permanentlyDeletePage,
  createFolder,
} from "@/lib/notion";

export const dynamic = "force-dynamic";

interface ActionRequest {
  action: string;
  ids: string[];
  payload?: Record<string, unknown>;
}

export async function POST(req: NextRequest) {
  let body: ActionRequest;
  try {
    body = (await req.json()) as ActionRequest;
  } catch {
    return NextResponse.json({ success: false, error: "Invalid JSON body" }, { status: 400 });
  }

  const { action, ids, payload } = body;
  const id = ids?.[0] ?? "";

  // Create-folder is a single operation
  if (action === "new-folder") {
    const name = String(payload?.name ?? "").trim();
    const parentId = String(payload?.parent_folder_id ?? payload?.parentId ?? "").replace(/-/g, "") || null;
    if (!name) {
      return NextResponse.json({ success: false, error: "Folder name is required" }, { status: 400 });
    }
    const result = await createFolder(name, parentId);
    if (result) {
      return NextResponse.json({ success: true, id: result.id, name: result.name });
    }
    return NextResponse.json({ success: false, error: "Failed to create folder" }, { status: 500 });
  }

  if (!id) {
    return NextResponse.json({ success: false, error: "No item id provided" }, { status: 400 });
  }

  const results: Array<{ id: string; ok: boolean; error?: string }> = [];

  for (const itemId of ids) {
    let ok = false;
    let error: string | undefined;

    try {
      switch (action) {
        case "star":
          ok = await starItem(itemId, true);
          break;
        case "unstar":
          ok = await starItem(itemId, false);
          break;
        case "delete":
          ok = await archivePage(itemId);
          break;
        case "delete-permanent":
          ok = await permanentlyDeletePage(itemId);
          break;
        case "restore":
          ok = await unarchivePage(itemId);
          break;
        case "rename": {
          const newName = String(payload?.name ?? "").trim();
          if (!newName) {
            error = "Name is required";
          } else {
            ok = await renameItem(itemId, newName);
          }
          break;
        }
        case "move": {
          const newParentId = String(payload?.parent_folder_id ?? payload?.parentId ?? "").replace(/-/g, "") || null;
          ok = await moveItem(itemId, newParentId);
          break;
        }
        default:
          error = `Unknown action: ${action}`;
      }
    } catch (err) {
      error = String(err);
    }

    results.push({ id: itemId, ok, error });
  }

  const failed = results.find((r) => !r.ok);
  if (failed) {
    return NextResponse.json(
      { success: false, error: failed.error ?? "Operation failed", results },
      { status: 400 },
    );
  }
  return NextResponse.json({ success: true, results });
}
