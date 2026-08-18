import { NextRequest, NextResponse } from "next/server";
import { getFileUrlFromPage, retrievePageBlocksText } from "@/lib/notion";
import { getItemById } from "@/lib/cache";

export const dynamic = "force-dynamic";

// In-memory URL & Content Cache (1 hour TTL)
const urlCache = new Map<string, { url: string; ts: number }>();
const textCache = new Map<string, { content: string; ts: number }>();

const MIME_TYPES: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".mp4": "video/mp4",
  ".webm": "video/webm",
  ".mp3": "audio/mpeg",
  ".wav": "audio/wav",
  ".m4a": "audio/mp4",
  ".pdf": "application/pdf",
  ".txt": "text/plain; charset=utf-8",
  ".md": "text/markdown; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".ts": "text/plain; charset=utf-8",
  ".tsx": "text/plain; charset=utf-8",
  ".js": "text/plain; charset=utf-8",
  ".jsx": "text/plain; charset=utf-8",
  ".py": "text/plain; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".sql": "text/plain; charset=utf-8",
  ".sh": "text/plain; charset=utf-8",
  ".bat": "text/plain; charset=utf-8",
  ".yaml": "text/plain; charset=utf-8",
  ".yml": "text/plain; charset=utf-8",
  ".ini": "text/plain; charset=utf-8",
  ".env": "text/plain; charset=utf-8",
};

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const pageId = searchParams.get("id");
  const mode = searchParams.get("mode"); // "text" | "raw" | "download"

  if (!pageId) return new Response("Missing id", { status: 400 });

  const item = getItemById(pageId);
  const ext = item?.extension?.toLowerCase() || (item?.name?.includes(".") ? "." + item.name.split(".").pop()!.toLowerCase() : "");

  // 1. Text / Code mode: Fetch strictly from Notion Cloud Page Blocks
  if (mode === "text") {
    const cachedText = textCache.get(pageId);
    if (cachedText && Date.now() - cachedText.ts < 3_600_000) {
      return NextResponse.json({ success: true, content: cachedText.content, source: "notion_database" });
    }

    const textContent = await retrievePageBlocksText(pageId);
    if (textContent) {
      textCache.set(pageId, { content: textContent, ts: Date.now() });
      return NextResponse.json({ success: true, content: textContent, source: "notion_database" });
    }
  }

  // 2. Media / Document / Binary Stream: Fetch strictly from Notion S3 / Cloud URL
  let fileUrl = item?.file_url;
  if (!fileUrl) {
    const cached = urlCache.get(pageId);
    if (cached && Date.now() - cached.ts < 3_600_000) {
      fileUrl = cached.url;
    } else {
      const freshUrl = await getFileUrlFromPage(pageId);
      if (freshUrl) {
        urlCache.set(pageId, { url: freshUrl, ts: Date.now() });
        fileUrl = freshUrl;
      }
    }
  }

  if (fileUrl) {
    return proxyNotionStream(req, fileUrl, ext);
  }

  // 3. Fallback: Check if file content was embedded as Notion blocks
  const textContent = await retrievePageBlocksText(pageId);
  if (textContent) {
    return new Response(textContent, {
      headers: {
        "Content-Type": MIME_TYPES[ext] || "text/plain; charset=utf-8",
        "Cache-Control": "private, max-age=3600",
      },
    });
  }

  return new Response("File not found in Notion database", { status: 404 });
}

async function proxyNotionStream(req: NextRequest, url: string, ext: string) {
  const rangeHeader = req.headers.get("range");
  const upstreamHeaders: Record<string, string> = { "User-Agent": "NotionDriveCloud/1.0" };
  if (rangeHeader) upstreamHeaders["Range"] = rangeHeader;

  const upstream = await fetch(url, { headers: upstreamHeaders });
  const status = upstream.status; // 200 or 206
  const contentType = upstream.headers.get("content-type") || MIME_TYPES[ext] || "application/octet-stream";
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
