import { NextResponse } from "next/server";
import { backendJson } from "@/lib/backend";
import type { DriveStats } from "@/lib/types";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const data = await backendJson<DriveStats>("/api/stats");
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ total_files: 0, total_mb: 0 });
  }
}
