import { NextRequest, NextResponse } from "next/server";
import { backendFetch } from "@/lib/backend";

export const dynamic = "force-dynamic";

interface ActionRequest {
  action: string;
  ids: string[];
  payload?: Record<string, unknown>;
}

async function postToBackend(path: string, body: Record<string, unknown>) {
  const res = await backendFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    return { ok: false, error: (data as { error?: string }).error ?? `Backend error (${res.status})`, status: res.status };
  }
  return { ok: true, error: null, status: res.status };
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

  // Create-folder is a single operation (no per-item loop).
  if (action === "new-folder") {
    const name = String(payload?.name ?? "").trim();
    const parentId = String(payload?.parent_folder_id ?? payload?.parentId ?? "").replace(/-/g, "") || null;
    if (!name) {
      return NextResponse.json({ success: false, error: "Folder name is required" }, { status: 400 });
    }
    const out = await postToBackend("/api/folder/create", { name, parent_folder_id: parentId });
    return NextResponse.json({ success: out.ok, error: out.error }, { status: out.status });
  }

  if (!id) {
    return NextResponse.json({ success: false, error: "No item id provided" }, { status: 400 });
  }

  let path = "";
  let bodyPayload: Record<string, unknown> = { id };
  switch (action) {
    case "star":
      path = "/api/file/star";
      bodyPayload = { id, starred: true };
      break;
    case "unstar":
      path = "/api/file/star";
      bodyPayload = { id, starred: false };
      break;
    case "delete":
      path = "/api/file/delete";
      bodyPayload = { id };
      break;
    case "delete-permanent":
      path = "/api/file/delete-permanent";
      bodyPayload = { id };
      break;
    case "restore":
      path = "/api/file/restore";
      bodyPayload = { id };
      break;
    case "rename":
      path = "/api/file/rename";
      bodyPayload = { id, name: String(payload?.name ?? "") };
      break;
    case "move":
      path = "/api/file/move";
      bodyPayload = {
        id,
        parent_folder_id: String(payload?.parent_folder_id ?? payload?.parentId ?? "").replace(/-/g, "") || null,
      };
      break;
    default:
      return NextResponse.json({ success: false, error: `Unknown action: ${action}` }, { status: 400 });
  }

  const results = [];
  for (const itemId of ids) {
    const out = await postToBackend(path, { ...bodyPayload, id: itemId });
    results.push({ id: itemId, ok: out.ok, error: out.error });
  }
  const failed = results.find((r) => !r.ok);
  if (failed) {
    return NextResponse.json({ success: false, error: failed.error ?? "Operation failed", results }, { status: 400 });
  }
  return NextResponse.json({ success: true, results });
}
