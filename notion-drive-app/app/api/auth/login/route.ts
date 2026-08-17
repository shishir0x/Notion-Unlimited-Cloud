import { NextResponse } from "next/server";
import { PYTHON_BACKEND_URL } from "@/lib/config";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  let body: unknown = {};
  try {
    body = await req.json();
  } catch {
    // empty body
  }
  const upstream = await fetch(`${PYTHON_BACKEND_URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const data = await upstream.json().catch(() => ({}));
  const res = NextResponse.json(data, { status: upstream.status });
  // Pass the backend session cookie through to the browser (HttpOnly).
  const setCookie = upstream.headers.get("set-cookie");
  if (setCookie) res.headers.set("Set-Cookie", setCookie);
  return res;
}
