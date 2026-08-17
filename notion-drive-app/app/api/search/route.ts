import { NextRequest, NextResponse } from "next/server";
import { backendJson, normalizeSearch, type RawItem } from "@/lib/backend";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const q = (searchParams.get("q") ?? "").trim();
    if (!q || q.length < 2) {
      return NextResponse.json({ items: [], total: 0 });
    }
    const data = await backendJson<{ folders?: RawItem[]; files?: RawItem[]; total_files?: number }>(
      `/api/search?q=${encodeURIComponent(q)}`,
    );
    return NextResponse.json(normalizeSearch(data));
  } catch (err) {
    const status = (err as { status?: number }).status ?? 500;
    return NextResponse.json(
      { error: status === 401 ? "Unauthorized" : `Search unavailable: ${String(err)}` },
      { status },
    );
  }
}
