// lib/file-types.ts — reusable file-type classification with icons and colors.
// Pure utility: safe to import from both server (BFF) and client components.

import {
  Archive, AudioLines, Code2, File, FileSpreadsheet, FileText, Film,
  Folder, Image, Presentation, type LucideIcon,
} from "lucide-react";

export interface FileTypeInfo {
  /** Stable category used for icons/grouping: "folder" | "image" | … */
  kind: string;
  /** Human-readable label, e.g. "PDF", "Image". */
  label: string;
  icon: LucideIcon;
  /** Tailwind text color class. */
  color: string;
}

const ICONS = {
  folder: { icon: Folder, color: "text-amber-400" },
  image: { icon: Image, color: "text-violet-400" },
  video: { icon: Film, color: "text-blue-400" },
  audio: { icon: AudioLines, color: "text-emerald-400" },
  code: { icon: Code2, color: "text-emerald-400" },
  text: { icon: FileText, color: "text-sky-400" },
  pdf: { icon: FileText, color: "text-red-400" },
  sheet: { icon: FileSpreadsheet, color: "text-green-400" },
  slides: { icon: Presentation, color: "text-orange-400" },
  archive: { icon: Archive, color: "text-yellow-400" },
  file: { icon: File, color: "text-gray-400" },
};

// Extension → (kind, label). Unknown extensions fall back to "file".
const EXT_MAP: Record<string, [string, string]> = {
  ".jpg": ["image", "Image"], ".jpeg": ["image", "Image"], ".png": ["image", "Image"],
  ".gif": ["image", "Image"], ".webp": ["image", "Image"], ".svg": ["image", "Image"],
  ".bmp": ["image", "Image"], ".ico": ["image", "Image"], ".heic": ["image", "Image"],
  ".avif": ["image", "Image"],
  ".mp4": ["video", "Video"], ".mkv": ["video", "Video"], ".mov": ["video", "Video"],
  ".avi": ["video", "Video"], ".webm": ["video", "Video"], ".flv": ["video", "Video"],
  ".m4v": ["video", "Video"], ".wmv": ["video", "Video"],
  ".mp3": ["audio", "Audio"], ".wav": ["audio", "Audio"], ".m4a": ["audio", "Audio"],
  ".aac": ["audio", "Audio"], ".ogg": ["audio", "Audio"], ".opus": ["audio", "Audio"],
  ".flac": ["audio", "Audio"],
  ".pdf": ["pdf", "PDF"],
  ".doc": ["text", "Word"], ".docx": ["text", "Word"], ".rtf": ["text", "Text"],
  ".odt": ["text", "Text"],
  ".xls": ["sheet", "Excel"], ".xlsx": ["sheet", "Excel"], ".csv": ["sheet", "CSV"],
  ".ods": ["sheet", "Sheets"],
  ".ppt": ["slides", "PowerPoint"], ".pptx": ["slides", "PowerPoint"],
  ".txt": ["text", "Text"], ".md": ["text", "Markdown"], ".log": ["text", "Log"],
  ".xml": ["code", "XML"], ".yaml": ["code", "YAML"], ".yml": ["code", "YAML"],
  ".json": ["code", "JSON"], ".js": ["code", "JavaScript"], ".jsx": ["code", "JSX"],
  ".ts": ["code", "TypeScript"], ".tsx": ["code", "TSX"], ".py": ["code", "Python"],
  ".rb": ["code", "Ruby"], ".go": ["code", "Go"], ".rs": ["code", "Rust"],
  ".java": ["code", "Java"], ".c": ["code", "C"], ".cpp": ["code", "C++"],
  ".cs": ["code", "C#"], ".php": ["code", "PHP"], ".sh": ["code", "Shell"],
  ".bat": ["code", "Batch"], ".html": ["code", "HTML"], ".css": ["code", "CSS"],
  ".sql": ["code", "SQL"], ".toml": ["code", "TOML"], ".ini": ["code", "INI"],
  ".env": ["code", "Env"],
  ".zip": ["archive", "ZIP"], ".rar": ["archive", "RAR"], ".7z": ["archive", "7Z"],
  ".tar": ["archive", "TAR"], ".gz": ["archive", "GZ"], ".bz2": ["archive", "BZ2"],
  ".iso": ["archive", "ISO"],
  ".apk": ["file", "APK"], ".exe": ["file", "EXE"],
};

export const FOLDER_INFO: FileTypeInfo = {
  kind: "folder",
  label: "Folder",
  icon: ICONS.folder.icon,
  color: ICONS.folder.color,
};

const UNKNOWN: FileTypeInfo = { kind: "file", label: "File", icon: ICONS.file.icon, color: ICONS.file.color };

/** Classify a file by extension. Never throws for unknown extensions. */
export function getFileType(extension: string): FileTypeInfo {
  const key = extension.toLowerCase();
  const hit = EXT_MAP[key];
  if (!hit) return UNKNOWN;
  const [kind, label] = hit;
  const iconDef = ICONS[kind as keyof typeof ICONS] ?? ICONS.file;
  return { kind, label, icon: iconDef.icon, color: iconDef.color };
}

/** True when a file can be rendered inline in the browser preview. */
export function isPreviewable(extension: string): boolean {
  const kind = getFileType(extension).kind;
  return ["image", "video", "audio", "pdf", "text", "code"].includes(kind);
}

export const IMAGE_EXTS = new Set([".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".avif", ".heic", ".ico"]);
export const VIDEO_EXTS = new Set([".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v", ".wmv"]);
export const AUDIO_EXTS = new Set([".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac"]);
export const CODE_EXTS = new Set([
  ".txt", ".md", ".log", ".xml", ".yaml", ".yml", ".json", ".js", ".jsx", ".ts", ".tsx",
  ".py", ".rb", ".go", ".rs", ".java", ".c", ".cpp", ".cs", ".php", ".sh", ".bat",
  ".html", ".css", ".sql", ".toml", ".ini",
]);
