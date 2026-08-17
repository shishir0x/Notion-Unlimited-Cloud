import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/backend";

export const dynamic = "force-dynamic";

export async function GET() {
  const upstream = await backendFetch("/api/auth/status");
  const data = await upstream.json().catch(() => ({ protected: false, authenticated: false }));
  return NextResponse.json(data, { status: upstream.status });
}
