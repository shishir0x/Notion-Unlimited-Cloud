import { NextRequest, NextResponse } from "next/server";
import { backendJson, normalizeListing, type RawItem } from "@/lib/backend";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const params = new URLSearchParams();
    const folderId = searchParams.get("folder_id") ?? searchParams.get("folder");
    if (folderId) params.set("folder_id", folderId);
    const sort = searchParams.get("sort");
    const order = searchParams.get("order");
    if (sort) params.set("sort", sort);
    if (order) params.set("order", order);
    if (searchParams.get("limit")) params.set("limit", searchParams.get("limit")!);
    if (searchParams.get("offset")) params.set("offset", searchParams.get("offset")!);

    const data = await backendJson<{
      folders?: RawItem[]; files?: RawItem[]; breadcrumbs?: RawItem[];
      has_more?: boolean; total_files?: number; total_folders?: number; version?: number;
    }>(`/api/drive?${params.toString()}`);
    return NextResponse.json(normalizeListing(data));
  } catch (err) {
    const status = (err as { status?: number }).status ?? 500;
    return NextResponse.json(
      { error: status === 401 ? "Unauthorized" : `Drive unavailable: ${String(err)}` },
      { status },
    );
  }
}
