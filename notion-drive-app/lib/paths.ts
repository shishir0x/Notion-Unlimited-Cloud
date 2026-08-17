// lib/paths.ts — path normalization for Windows, ADB/Android, and web paths.
// The backend mixes Windows paths (C:\Users\…) and ADB paths
// (/storage/emulated/0/…) — this module keeps the two formats straight.

/** Normalize any path to forward slashes (web-friendly, comparisons-safe). */
export function normalizePath(p: string): string {
  return String(p ?? "").replace(/\\/g, "/").trim();
}

/** True when a path belongs to an Android device (ADB or Windows Explorer label). */
export function isAndroidPath(p: string): boolean {
  const norm = normalizePath(p);
  return (
    norm.startsWith("/storage/") ||
    norm.startsWith("/sdcard") ||
    norm.includes("Internal shared storage") ||
    norm.includes("Internal Storage") ||
    norm.includes("SD card") ||
    norm.includes("SD Card") ||
    norm.includes("OnePlus Nord CE4")
  );
}

/** Human-readable source label for an item path. */
export function sourceLabel(p: string): string {
  const norm = normalizePath(p);
  if (isAndroidPath(norm)) {
    if (norm.includes("SD card") || norm.includes("SD Card") || norm.includes("/storage/4A21")) return "Android SD card";
    return "Android";
  }
  if (/^[a-z]:/.test(norm)) return "Local PC";
  return "Notion Cloud";
}

/** Basename of a path (last segment). */
export function baseName(p: string): string {
  const norm = normalizePath(p).replace(/\/+$/, "");
  const parts = norm.split("/");
  return parts[parts.length - 1] ?? "";
}

/** Parent path of a path (empty when none). */
export function parentPath(p: string): string {
  const norm = normalizePath(p).replace(/\/+$/, "");
  const idx = norm.lastIndexOf("/");
  return idx > 0 ? norm.slice(0, idx) : "";
}

/** Extension (with dot) of a filename; "" when none. */
export function extensionOf(name: string): string {
  const n = baseName(name);
  const idx = n.lastIndexOf(".");
  if (idx <= 0) return "";
  return n.slice(idx).toLowerCase();
}

/** Safe display name for a raw filename (strips any directory components). */
export function safeName(name: string): string {
  return baseName(normalizePath(name));
}
