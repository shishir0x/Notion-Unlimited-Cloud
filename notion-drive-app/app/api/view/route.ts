import { NextRequest } from "next/server";
import { getFileUrlFromPage } from "@/lib/notion";

export const dynamic = "force-dynamic";

// In-memory URL cache (Notion signed URLs expire in ~1hr)
const urlCache = new Map<string, { url: string; ts: number }>();

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const pageId = searchParams.get("id");
  if (!pageId) return new Response("Missing id", { status: 400 });

  // Cache lookup
  const cached = urlCache.get(pageId);
  if (cached && Date.now() - cached.ts < 3_600_000) {
    return proxyStream(req, cached.url);
  }

  const url = await getFileUrlFromPage(pageId);
  if (!url) {
    return new Response("File not found in Notion", { status: 404 });
  }
  urlCache.set(pageId, { url, ts: Date.now() });
  return proxyStream(req, url);
}

async function proxyStream(req: NextRequest, url: string) {
  const rangeHeader = req.headers.get("range");
  const upstreamHeaders: Record<string, string> = { "User-Agent": "NotionDrive/1.0" };
  if (rangeHeader) upstreamHeaders["Range"] = rangeHeader;

  const upstream = await fetch(url, { headers: upstreamHeaders });
  const status = upstream.status; // 200 or 206
  const contentType = upstream.headers.get("content-type") ?? "application/octet-stream";
  const contentLength = upstream.headers.get("content-length");
  const contentRange = upstream.headers.get("content-range");
  const acceptRanges = upstream.headers.get("accept-ranges") ?? "bytes";

  const responseHeaders: Record<string, string> = {
    "Content-Type": contentType,
    "Accept-Ranges": acceptRanges,
    "Cache-Control": "private, max-age=3600",
    "Access-Control-Allow-Origin": "*",
  };
  if (contentLength) responseHeaders["Content-Length"] = contentLength;
  if (contentRange) responseHeaders["Content-Range"] = contentRange;

  return new Response(upstream.body, { status, headers: responseHeaders });
}
