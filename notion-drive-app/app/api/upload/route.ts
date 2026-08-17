import { NextResponse } from "next/server";
import { PYTHON_BACKEND_URL } from "@/lib/config";
import { cookies } from "next/headers";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(req: Request) {
  try {
    const contentType = req.headers.get("content-type") ?? "";
    if (!contentType.includes("multipart/form-data")) {
      return NextResponse.json({ success: false, error: "Expected multipart/form-data" }, { status: 400 });
    }

    const store = await cookies();
    const session = store.get("notion_session")?.value;
    const headers: Record<string, string> = { "Content-Type": contentType };
    // The Python backend reads Content-Length to size the body read, so the
    // streamed proxy request must carry the original length.
    const incomingLength = req.headers.get("content-length");
    if (incomingLength) headers["Content-Length"] = incomingLength;
    if (session) headers["Cookie"] = `notion_session=${session}`;

    // Stream the raw request body straight to the Python backend — no
    // buffering of the file in Next.js memory. `duplex` is required by
    // undici when the body is a stream; RequestInit predates it, so cast.
    const upstream = await fetch(`${PYTHON_BACKEND_URL}/api/upload-multipart`, {
      method: "POST",
      headers,
      body: req.body as ReadableStream<Uint8Array>,
      cache: "no-store",
      duplex: "half",
    } as RequestInit & { duplex: "half" });

    const data = await upstream.json().catch(() => ({}));
    return NextResponse.json(data, { status: upstream.status });
  } catch (err) {
    return NextResponse.json(
      { success: false, error: `Upload failed: ${String(err)}` },
      { status: 500 },
    );
  }
}
