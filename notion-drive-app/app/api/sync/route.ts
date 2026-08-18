import { NextRequest, NextResponse } from "next/server";
import { syncFromNotion, getStats } from "@/lib/cache";

export const dynamic = "force-dynamic";

export async function GET() {
  const stats = getStats();
  return NextResponse.json({
    is_running: false,
    status_message: "Direct Notion Database Connection Active",
    percent: 100,
    total_files: stats.total_files,
    synced_files: stats.total_files,
  });
}

export async function POST(req: NextRequest) {
  try {
    await syncFromNotion(true);
    const stats = getStats();
    return NextResponse.json({
      success: true,
      message: "Synchronized directly with Notion database",
      total_files: stats.total_files,
      total_mb: stats.total_mb,
    });
  } catch (err: unknown) {
    return NextResponse.json({ success: false, error: (err as Error).message }, { status: 500 });
  }
}
