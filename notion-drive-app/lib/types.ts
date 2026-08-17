// lib/types.ts — shared data model for the drive application.
// These types describe the normalized contract produced by the BFF layer
// (which proxies the Python backend). No raw Notion shapes leak into the UI.

export type DriveItemType = "file" | "folder";

export interface DriveItem {
  id: string;
  name: string;
  type: DriveItemType;
  /** Human-readable category: PDF, Image, Video, Code, Folder, … */
  fileType: string;
  /** Extension including the dot, e.g. ".pdf". Empty for folders. */
  extension: string;
  sizeBytes: number;
  sizeMb: number;
  parentId: string | null;
  starred: boolean;
  archived: boolean;
  /** Epoch seconds (when known) */
  mtime: number;
  createdAt: string;
  modifiedAt: string;
  /** Local/ADB path backing this item ("" when cloud-only). */
  localPath: string;
  notionUrl: string;
  /** Number of children for folders. */
  itemCount: number;
  storageRoot: string;
}

export interface Breadcrumb {
  id: string | null;
  name: string;
}

export interface DriveListing {
  items: DriveItem[];
  breadcrumbs: Breadcrumb[];
  total: number;
  hasMore: boolean;
  version: number;
}

export interface SearchResults {
  items: DriveItem[];
  total: number;
}

export interface DriveStats {
  total_files: number;
  total_mb: number;
}

export interface AuthStatus {
  protected: boolean;
  authenticated: boolean;
}

export interface SyncState {
  is_running: boolean;
  current_target: string;
  total_files: number;
  synced_files: number;
  remaining_files: number;
  percent: number;
  current_file: string;
  current_path: string;
  current_size_str: string;
  speed_str: string;
  status_message: string;
  queue: unknown[];
  history: unknown[];
  logs: string[];
  cache_version?: number;
}

export type UploadStatus = "pending" | "uploading" | "done" | "error";

export interface UploadTask {
  id: string;
  name: string;
  size: number;
  progress: number; // 0-100
  status: UploadStatus;
  error?: string;
  speedBytesPerSec?: number;
  etaSeconds?: number;
}

export type ViewMode = "folder" | "recent" | "starred" | "trash";

export type SortKey = "name" | "size" | "mtime" | "type";
export type SortDir = "asc" | "desc";

export type ThemePreference = "light" | "dark" | "system";

export interface DriveActionPayload {
  action: string;
  ids: string[];
  payload?: Record<string, unknown>;
}

/** Error thrown by the client API layer with an HTTP status. */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}
