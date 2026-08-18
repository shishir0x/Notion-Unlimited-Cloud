// lib/config.ts — server-only environment configuration.
// Never import this from client components; secrets must stay on the server.

// ── Python backend (for sync status, SSE events, file streaming) ──────────────
export const PYTHON_BACKEND_URL: string = (
  process.env.PYTHON_BACKEND_URL ?? "http://127.0.0.1:8765"
).replace(/\/+$/, "");

// ── Notion API (for direct file management) ──────────────────────────────────
export const NOTION_TOKEN: string = process.env.NOTION_TOKEN ?? "";
export const NOTION_DATABASE_ID: string = (
  process.env.NOTION_DATABASE_ID ?? ""
).replace(/-/g, "");

// ── Upload settings ──────────────────────────────────────────────────────────
export const UPLOAD_MAX_MB: number = Number(process.env.UPLOAD_MAX_MB ?? 100);

// ── Drive password ───────────────────────────────────────────────────────────
export const DRIVE_PASSWORD: string = process.env.DRIVE_PASSWORD ?? "";

/** True when the backend has been configured with a password. */
export const BACKEND_PROTECTED: boolean = Boolean(DRIVE_PASSWORD);
