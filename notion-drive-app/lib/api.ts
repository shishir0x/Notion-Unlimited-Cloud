// lib/api.ts — client-side API helpers for the BFF routes.
// All secrets stay server-side; the browser only talks to /api/* here.

import {
  ApiError, type AuthStatus, type DriveItem, type DriveListing, type DriveStats,
  type SearchResults, type SyncState,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const data = (await res.json()) as { error?: string };
      if (data?.error) msg = data.error;
    } catch {
      // keep generic
    }
    throw new ApiError(res.status, msg);
  }
  return (await res.json()) as T;
}

export const api = {
  authStatus: () => request<AuthStatus>("/api/auth/status", { cache: "no-store" }),
  login: (password: string) =>
    request<{ success: boolean; error?: string }>("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    }),
  logout: () => request<{ success: boolean }>("/api/auth/logout", { method: "POST" }),

  drive: (folderId: string | null, sort: string, dir: string) => {
    const params = new URLSearchParams();
    if (folderId) params.set("folder_id", folderId);
    if (sort) params.set("sort", sort);
    if (dir) params.set("order", dir);
    return request<DriveListing>(`/api/drive?${params.toString()}`, { cache: "no-store" });
  },
  recent: () => request<DriveListing>("/api/recent", { cache: "no-store" }),
  starred: () => request<DriveListing>("/api/starred", { cache: "no-store" }),
  trash: () => request<DriveListing>("/api/trash", { cache: "no-store" }),
  stats: () => request<DriveStats>("/api/stats", { cache: "no-store" }),
  search: (q: string) => request<SearchResults>(`/api/search?q=${encodeURIComponent(q)}`, { cache: "no-store" }),
  syncStatus: () => request<SyncState>("/api/sync", { cache: "no-store" }),

  /** Run an action against one or more items (star, delete, rename, move…). */
  action: (action: string, ids: string[], payload?: Record<string, unknown>) =>
    request<{ success: boolean; error?: string }>("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ids, payload }),
    }),

  triggerSync: () =>
    request<{ status: string }>("/api/sync", { method: "POST" }),
};

export function downloadUrl(id: string, localPath: string, attachment = true): string {
  const params = new URLSearchParams();
  if (id) params.set("id", id);
  if (localPath) params.set("path", localPath);
  return `/api/${attachment ? "download" : "view"}?${params.toString()}`;
}

export function previewUrl(item: DriveItem): string {
  return downloadUrl(item.id, item.localPath, false);
}

export function folderZipUrl(item: DriveItem): string {
  const params = new URLSearchParams();
  if (item.id) params.set("id", item.id);
  if (item.localPath) params.set("path", item.localPath);
  return `/api/download-folder?${params.toString()}`;
}
