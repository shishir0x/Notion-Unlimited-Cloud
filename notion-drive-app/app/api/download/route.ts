import { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const PASSTHROUGH_HEADERS = [
  "content-type",
  "content-disposition",
  "content-length",
  "content-range",
  "accept-ranges",
  "cache-control",
  "location",
];

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const params = new URLSearchParams();
    if (searchParams.get("id")) params.set("id", searchParams.get("id")!);
    if (searchParams.get("path")) params.set("path", searchParams.get("path")!);

    const upstream = await backendFetch(`/download?${params.toString()}`, {
      headers: req.headers.get("range") ? { Range: req.headers.get("range")! } : undefined,
    });
    if (!upstream.body) {
      return new Response("Not found", { status: upstream.status });
    }

    const headers = new Headers();
    for (const h of PASSTHROUGH_HEADERS) {
      const v = upstream.headers.get(h);
      if (v) headers.set(h, v);
    }
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch (err) {
    return new Response(`Download unavailable: ${String(err)}`, { status: 502 });
  }
}
