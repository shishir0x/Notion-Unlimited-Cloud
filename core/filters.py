"""
core/filters.py — Unified ignore/filter rules.

All ignore logic lives here so CLI and Web server behave identically.
"""

from pathlib import Path

# ─── File extensions to never upload ─────────────────────────────────────────
IGNORED_EXTENSIONS = {
    ".tmp", ".temp", ".log", ".blf", ".bak", ".swp", ".lock", ".pid",
    ".cache", ".pyc", ".pyo", ".pyd", ".class", ".o", ".obj",
    ".dll", ".exe", ".so", ".dylib", ".sys", ".iso", ".vmdk", ".vdi",
    ".pdb", ".ilk", ".map", ".regtrans-ms", ".dat", ".search-ms",
    ".lnk", ".url", ".idx", ".pack",
}

# ─── File name prefixes to skip ───────────────────────────────────────────────
IGNORED_PREFIXES = (
    "ntuser.dat", "ntuser.rhk", "desktop.ini", "~$",
    "sti_trace.log", "_viminfo", ".notion_", "thumbs.db",
    ".ds_store", "#", ".#", "~",
)

# ─── Directory names to never descend into ───────────────────────────────────
IGNORED_DIRS = {
    "appdata", "application data", "local settings",
    "$recycle.bin", "system volume information",
    "__pycache__", "node_modules", ".git",
    ".gemini", ".antigravity", "extensions",
    ".cache", ".gradle", ".m2", ".npm",
    ".rustup", ".cargo", ".nuget",
    ".venv", "venv", "env", "site-packages", "dist-info",
    ".android", ".jdks", "crossdevice", "scoop",
    "saved games", "searches", "contacts", "links", "favorites",
    ".bun", ".cline", ".config", ".copilot", ".dotnet", ".expo",
    ".installer", ".ipython", ".lmstudio", ".local",
    ".sbx-denybin", ".semantic_search", ".ssh",
    ".virtualbox", ".vscode-shared", "onedrive",
    ".notion drive", "agent-plugins", "microsoft",
    "program files", "program files (x86)", "programdata",
    "windows", "recovery", "$windows.~bt",
}

# ─── Android-specific paths to always exclude ────────────────────────────────
ANDROID_EXCLUDED_PATHS = (
    "/Android/data",
    "/Android/obb",
    "/Android/sandbox",
    "/.thumbnails",
    "/LOST.DIR",
    "/.trash",
    "/.FileManagerRecycler",
    "/.Trash",
)

# ─── File type classification ─────────────────────────────────────────────────
FILE_TYPE_MAP = {
    ".pdf": "PDF",
    ".doc": "Word", ".docx": "Word",
    ".xls": "Excel", ".xlsx": "Excel", ".csv": "Excel",
    ".ppt": "PowerPoint", ".pptx": "PowerPoint",
    ".jpg": "Image", ".jpeg": "Image", ".png": "Image",
    ".gif": "Image", ".webp": "Image", ".svg": "Image",
    ".heic": "Image", ".bmp": "Image", ".tiff": "Image",
    ".mp4": "Video", ".mkv": "Video", ".mov": "Video",
    ".avi": "Video", ".webm": "Video", ".flv": "Video",
    ".mp3": "Audio", ".wav": "Audio", ".aac": "Audio",
    ".opus": "Audio", ".m4a": "Audio", ".flac": "Audio",
    ".zip": "ZIP", ".rar": "ZIP", ".7z": "ZIP",
    ".tar": "ZIP", ".gz": "ZIP", ".bz2": "ZIP",
    ".py": "Code", ".js": "Code", ".ts": "Code",
    ".html": "Code", ".css": "Code", ".java": "Code",
    ".cpp": "Code", ".c": "Code", ".json": "Code",
    ".yaml": "Code", ".yml": "Code", ".sql": "Code",
    ".go": "Code", ".rs": "Code", ".kt": "Code",
    ".txt": "Other", ".md": "Other", ".rtf": "Other",
}

EMOJI_MAP = {
    "PDF": "📕", "Word": "📝", "Excel": "📊",
    "PowerPoint": "📊", "Image": "🖼️", "Video": "🎬",
    "Audio": "🎵", "ZIP": "📦", "Code": "💻", "Other": "📄",
}


def classify_file(ext: str) -> tuple[str, str]:
    """Return (file_type, emoji) for a given file extension."""
    ftype = FILE_TYPE_MAP.get(ext.lower(), "Other")
    return ftype, EMOJI_MAP.get(ftype, "📄")


def should_ignore_dir(name: str) -> bool:
    """Return True if a directory should be skipped entirely."""
    return name.lower() in IGNORED_DIRS


def should_ignore_file(path: Path) -> bool:
    """Return True if a file should be skipped."""
    name_lower = path.name.lower()
    if any(name_lower.startswith(p) for p in IGNORED_PREFIXES):
        return True
    if path.suffix.lower() in IGNORED_EXTENSIONS:
        return True
    return False


def should_ignore_android_path(fpath: str) -> bool:
    """
    Return True if an Android file path should be excluded.
    Allows /Android/media (and its subdirectories) on mobile internal storage & SD card for backup,
    while excluding /Android/data, /Android/obb, system junk, and trash folders.
    """
    norm = fpath.replace("\\", "/")
    norm_lower = norm.lower()
    # If path is inside Android directory:
    if "/android/" in norm_lower or norm_lower.endswith("/android"):
        # Allow the Android root, Android/media root, and anything inside Android/media
        if "/android/media" in norm_lower or norm_lower.endswith("/android") or norm_lower.endswith("/android/"):
            return any(excl.lower() in norm_lower for excl in ("/.thumbnails", "/LOST.DIR", "/.trash", "/.FileManagerRecycler", "/.Trash"))
        # Exclude everything else under /Android (such as /Android/data, /Android/obb, /Android/sandbox)
        return True
    return any(excl.lower() in norm_lower for excl in ANDROID_EXCLUDED_PATHS)


