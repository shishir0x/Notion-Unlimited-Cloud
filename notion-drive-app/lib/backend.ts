// lib/backend.ts — server-side BFF layer.
// Proxies requests to the running Python backend (notion_server.py) and
// normalizes its responses into the frontend contract. Imported ONLY from
// Route Handlers (never from client components).

import { cookies } from "next/headers";
import {
  type Breadcrumb, type DriveItem, type DriveListing, type SearchResults,
} from "./types";
import { getFileType } from "./file-types";
import { PYTHON_BACKEND_URL } from "./config";

/** Build headers that forward the backend session cookie. */
async function authHeaders(extra?: HeadersInit): Promise<Headers> {
  const headers = new Headers(extra ?? {});
  try {
    const store = await cookies();
    const session = store.get("notion_session")?.value;
    if (session) headers.set("Cookie", `notion_session=${session}`);
  } catch {
    // cookies() unavailable (e.g. static generation) — backend will 401.
  }
  return headers;
}

/** Fetch a backend endpoint, forwarding the session cookie. */
export async function backendFetch(path: string, init?: RequestInit): Promise<Response> {
  const headers = await authHeaders(init?.headers);
  return fetch(`${PYTHON_BACKEND_URL}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
}

/** Parse a JSON backend response, propagating error status. */
export async function backendJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await backendFetch(path, init);
  if (!res.ok) {
    let msg = `Backend error (${res.status})`;
    try {
      const data = (await res.json()) as { error?: string };
      if (data?.error) msg = data.error;
    } catch {
      // keep generic message
    }
    throw new BackendRequestError(res.status, msg);
  }
  return (await res.json()) as T;
}

export class BackendRequestError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "BackendRequestError";
    this.status = status;
  }
}

export type RawItem = Record<string, unknown>;

function num(v: unknown): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function str(v: unknown): string {
  return String(v ?? "");
}

/** Normalize a raw backend item row into the frontend DriveItem contract. */
export function normalizeDriveItem(raw: RawItem): DriveItem {
  const id = str(raw.id);
  const type: "file" | "folder" = str(raw.type).toLowerCase() === "folder" ? "folder" : "file";
  const extension = str(raw.extension).toLowerCase();
  const sizeBytes = num(raw.size_bytes);
  const sizeMb = num(raw.size_mb) || sizeBytes / (1024 * 1024);
  const mtime = num(raw.mtime);
  const created = str(raw.created_time);
  const edited = str(raw.last_edited_time);
  const modifiedAt = mtime > 0 ? new Date(mtime * 1000).toISOString() : edited || created;
  const fileType = type === "folder"
    ? "Folder"
    : str(raw.file_type) || getFileType(extension).label;
  return {
    id,
    name: str(raw.name),
    type,
    fileType,
    extension,
    sizeBytes,
    sizeMb,
    parentId: str(raw.parent_id) || null,
    starred: Boolean(raw.starred),
    archived: Boolean(raw.archived),
    mtime,
    createdAt: created,
    modifiedAt,
    localPath: str(raw.local_path),
    notionUrl: `https://www.notion.so/${id}`,
    itemCount: num(raw.item_count),
    storageRoot: str(raw.storage_root),
  };
}

export function normalizeBreadcrumb(raw: RawItem): Breadcrumb {
  return { id: str(raw.id) || null, name: str(raw.name) };
}

/** Normalize a /api/drive response. */
export function normalizeListing(data: {
  folders?: RawItem[];
  files?: RawItem[];
  breadcrumbs?: RawItem[];
  has_more?: boolean;
  total_files?: number;
  total_folders?: number;
  version?: number;
}): DriveListing {
  const items = [
    ...(data.folders ?? []).map(normalizeDriveItem),
    ...(data.files ?? []).map(normalizeDriveItem),
  ];
  return {
    items,
    breadcrumbs: (data.breadcrumbs ?? []).map(normalizeBreadcrumb),
    total: num(data.total_files) + num(data.total_folders),
    hasMore: Boolean(data.has_more),
    version: num(data.version),
  };
}

/** Normalize a /api/search response. */
export function normalizeSearch(data: {
  folders?: RawItem[];
  files?: RawItem[];
  total_files?: number;
}): SearchResults {
  const items = [
    ...(data.folders ?? []).map(normalizeDriveItem),
    ...(data.files ?? []).map(normalizeDriveItem),
  ];
  return { items, total: num(data.total_files) + items.length - (data.files?.length ?? 0) };
}
