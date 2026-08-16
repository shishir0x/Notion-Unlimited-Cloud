import { NextResponse } from "next/server";
import { getStats } from "@/lib/cache";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const stats = getStats();
    return NextResponse.json(stats);
  } catch {
    return NextResponse.json({ total_files: 0, total_mb: 0 });
  }
}
