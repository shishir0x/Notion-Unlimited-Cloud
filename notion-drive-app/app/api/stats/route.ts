import { NextResponse } from "next/server";
import { getStats } from "@/lib/notion";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const stats = await getStats();
    return NextResponse.json(stats);
  } catch {
    return NextResponse.json({ total_files: 0, total_mb: 0 });
  }
}
