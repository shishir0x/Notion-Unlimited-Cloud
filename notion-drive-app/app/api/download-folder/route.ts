import { NextRequest } from "next/server";
import { backendFetch } from "@/lib/backend";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const params = new URLSearchParams();
    if (searchParams.get("id")) params.set("id", searchParams.get("id")!);
    if (searchParams.get("path")) params.set("path", searchParams.get("path")!);

    const upstream = await backendFetch(`/download-folder?${params.toString()}`);
    if (!upstream.body) {
      return new Response("Not found", { status: upstream.status });
    }
    const headers = new Headers();
    const contentType = upstream.headers.get("content-type");
    const disposition = upstream.headers.get("content-disposition");
    if (contentType) headers.set("Content-Type", contentType);
    if (disposition) headers.set("Content-Disposition", disposition);
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch (err) {
    return new Response(`Download unavailable: ${String(err)}`, { status: 502 });
  }
}
