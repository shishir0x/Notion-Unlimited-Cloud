import { NextResponse } from "next/server";
import { backendJson, normalizeListing, type RawItem } from "@/lib/backend";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const data = await backendJson<{ items?: RawItem[] }>("/api/starred");
    return NextResponse.json(normalizeListing({ files: data.items ?? [], folders: [], breadcrumbs: [] }));
  } catch (err) {
    const status = (err as { status?: number }).status ?? 500;
    return NextResponse.json({ error: String(err) }, { status });
  }
}
