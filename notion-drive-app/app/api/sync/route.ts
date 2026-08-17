import { NextResponse } from "next/server";
import { backendFetch, backendJson } from "@/lib/backend";
import type { SyncState } from "@/lib/types";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const data = await backendJson<SyncState>("/api/sync/status");
    return NextResponse.json(data);
  } catch (err) {
    const status = (err as { status?: number }).status ?? 500;
    return NextResponse.json({ error: String(err) }, { status });
  }
}

export async function POST() {
  try {
    const res = await backendFetch("/api/sync/start", { method: "POST" });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
