"""
core/storage.py — Universal storage device auto-discovery.

Detects ALL connected storage devices without any hardcoding:
  - Windows local drives (C:, D:, E:, USB sticks, etc.)
  - Android phones & SD cards via ADB (USB or WiFi)
  - macOS / Linux mount points

Returns a clean list of StorageDevice objects the UI can show directly.
"""

import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class StorageDevice:
    """Represents one detectable storage device."""
    label: str                    # Human-readable label shown in menu
    device_type: str              # "local", "usb", "android_internal", "android_sdcard", "network"
    path: Optional[str]           # Windows/macOS/Linux local path (None for Android)
    emoji: str                    # Display emoji
    total_gb: float = 0.0
    free_gb: float = 0.0
    used_gb: float = 0.0
    # Android-specific
    adb_device_id: Optional[str] = None
    adb_path: Optional[str] = None   # Linux path inside Android (e.g. /storage/emulated/0)
    device_model: Optional[str] = None
    # Selectable sub-folders (populated lazily for local drives)
    subfolders: List[str] = field(default_factory=list)

    @property
    def is_android(self) -> bool:
        return self.device_type in ("android_internal", "android_sdcard")

    @property
    def size_str(self) -> str:
        if self.total_gb > 0:
            return f"{self.used_gb:.1f} GB used / {self.total_gb:.1f} GB total"
        return "Size unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Local drive discovery
# ─────────────────────────────────────────────────────────────────────────────

def _discover_local_windows() -> List[StorageDevice]:
    """Use psutil (if available) or fallback ctypes to list Windows drives."""
    devices: List[StorageDevice] = []

    # Try psutil first (cleanest)
    try:
        import psutil
        for part in psutil.disk_partitions(all=False):
            path = part.mountpoint
            try:
                usage = psutil.disk_usage(path)
                total_gb = usage.total / 1e9
                used_gb = usage.used / 1e9
                free_gb = usage.free / 1e9
            except Exception:
                total_gb = used_gb = free_gb = 0.0

            drive_letter = path.rstrip("\\").rstrip("/")
            fstype = getattr(part, "fstype", "").lower()
            opts = getattr(part, "opts", "").lower()

            # Classify drive type
            if "removable" in opts or "cdrom" in opts:
                dtype = "usb"
                emoji = "💾"
                label = f"USB Drive ({drive_letter})"
            elif "network" in opts or fstype in ("smbfs", "nfs", "cifs"):
                dtype = "network"
                emoji = "🌐"
                label = f"Network Drive ({drive_letter})"
            else:
                dtype = "local"
                emoji = "💽"
                label = f"Local Disk ({drive_letter})"

            devices.append(StorageDevice(
                label=label,
                device_type=dtype,
                path=path,
                emoji=emoji,
                total_gb=round(total_gb, 1),
                used_gb=round(used_gb, 1),
                free_gb=round(free_gb, 1),
            ))
        return devices
    except ImportError:
        pass

    # Fallback: ctypes bitmask
    try:
        import ctypes
        drives_bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if drives_bitmask & (1 << i):
                drive = f"{chr(65 + i)}:\\"
                try:
                    total, used, free = _win_disk_usage(drive)
                except Exception:
                    total = used = free = 0.0
                devices.append(StorageDevice(
                    label=f"Local Disk ({drive.rstrip(chr(92))})",
                    device_type="local",
                    path=drive,
                    emoji="💽",
                    total_gb=round(total, 1),
                    used_gb=round(used, 1),
                    free_gb=round(free, 1),
                ))
    except Exception:
        # Last resort: check common drive letters
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            p = Path(f"{letter}:\\")
            if p.exists():
                try:
                    total, used, free = _win_disk_usage(str(p))
                except Exception:
                    total = used = free = 0.0
                devices.append(StorageDevice(
                    label=f"Local Disk ({letter}:)",
                    device_type="local",
                    path=str(p),
                    emoji="💽",
                    total_gb=round(total, 1),
                    used_gb=round(used, 1),
                    free_gb=round(free, 1),
                ))
    return devices


def _win_disk_usage(path: str):
    """Return (total_gb, used_gb, free_gb) for a Windows path."""
    import ctypes
    free_bytes = ctypes.c_ulonglong(0)
    total_bytes = ctypes.c_ulonglong(0)
    ctypes.windll.kernel32.GetDiskFreeSpaceExW(
        ctypes.c_wchar_p(path), None, ctypes.pointer(total_bytes), ctypes.pointer(free_bytes)
    )
    total = total_bytes.value / 1e9
    free = free_bytes.value / 1e9
    used = total - free
    return total, used, free


def _discover_local_unix() -> List[StorageDevice]:
    """Discover mounts on macOS / Linux."""
    devices: List[StorageDevice] = []
    try:
        import psutil
        for part in psutil.disk_partitions(all=False):
            if part.mountpoint in ("/dev", "/run", "/sys", "/proc", "/boot"):
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                total_gb = round(usage.total / 1e9, 1)
                used_gb = round(usage.used / 1e9, 1)
                free_gb = round(usage.free / 1e9, 1)
            except Exception:
                total_gb = used_gb = free_gb = 0.0

            label = f"Volume ({part.mountpoint})"
            emoji = "💽"
            dtype = "local"
            if "/media/" in part.mountpoint or "/mnt/" in part.mountpoint:
                label = f"USB / External ({part.mountpoint})"
                emoji = "💾"
                dtype = "usb"

            devices.append(StorageDevice(
                label=label,
                device_type=dtype,
                path=part.mountpoint,
                emoji=emoji,
                total_gb=total_gb,
                used_gb=used_gb,
                free_gb=free_gb,
            ))
    except ImportError:
        # Fallback parse /proc/mounts
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].startswith("/"):
                        mp = parts[1]
                        if any(mp.startswith(x) for x in ("/dev", "/run", "/sys", "/proc")):
                            continue
                        devices.append(StorageDevice(
                            label=f"Volume ({mp})",
                            device_type="local",
                            path=mp,
                            emoji="💽",
                        ))
        except Exception:
            pass
    return devices


# ─────────────────────────────────────────────────────────────────────────────
# Android / ADB discovery
# ─────────────────────────────────────────────────────────────────────────────

def _adb_available() -> bool:
    try:
        subprocess.run(["adb", "version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _discover_android() -> List[StorageDevice]:
    """Find Android phones via ADB and list their Internal Storage and SD Card.

    Parses `adb devices -l` output which uses SPACES (not tabs) as separator:
        3b518df7    device product:CPH2613IN model:CPH2613 device:OP5D3FL1 transport_id:1
    """
    devices: List[StorageDevice] = []
    if not _adb_available():
        return devices

    try:
        out = subprocess.check_output(
            ["adb", "devices", "-l"], timeout=10
        ).decode("utf-8", errors="ignore")
    except Exception:
        return devices

    for line in out.splitlines():
        line = line.strip()
        # Skip blank lines and the "List of devices attached" header
        if not line or line.startswith("List of"):
            continue

        # Split on whitespace — works whether separator is spaces or tabs
        parts = line.split()
        if len(parts) < 2:
            continue

        device_id = parts[0]
        status = parts[1]   # "device", "offline", "unauthorized", "no permissions"
        if status != "device":
            continue        # skip non-ready devices

        # Extract human-readable model from extended -l fields e.g. "model:CPH2613"
        model = "Android Device"
        for token in parts[2:]:
            if token.startswith("model:"):
                model = token.replace("model:", "").replace("_", " ").strip()
                break

        # Internal storage is always present on a connected, authorized device
        devices.append(StorageDevice(
            label=f"{model} — Internal Storage",
            device_type="android_internal",
            path=None,
            emoji="📱",
            adb_device_id=device_id,
            adb_path="/storage/emulated/0",
            device_model=model,
        ))

        # SD card is optional — check if one is mounted
        sd_path = _find_sdcard_path(device_id)
        if sd_path:
            devices.append(StorageDevice(
                label=f"{model} — SD Card",
                device_type="android_sdcard",
                path=None,
                emoji="💾",
                adb_device_id=device_id,
                adb_path=sd_path,
                device_model=model,
            ))

    return devices


def _find_sdcard_path(device_id: str) -> Optional[str]:
    """Return the ADB path to the SD card, or None if not present."""
    try:
        out = subprocess.check_output(
            ["adb", "-s", device_id, "shell", "ls", "/storage"],
            timeout=8,
        ).decode("utf-8", errors="ignore")
        for entry in out.split():
            entry = entry.strip()
            if entry and entry not in ("emulated", "self", "persist", "sdcard0", "self"):
                return f"/storage/{entry}"
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Sub-folder helpers for local drives
# ─────────────────────────────────────────────────────────────────────────────

# Well-known user folders shown in the folder selection sub-menu
USER_FOLDERS = [
    ("Desktop",    "🖥️"),
    ("Documents",  "📄"),
    ("Downloads",  "⬇️"),
    ("Pictures",   "🖼️"),
    ("Music",      "🎵"),
    ("Videos",     "🎬"),
]


def get_user_subfolders(device: StorageDevice) -> List[dict]:
    """
    Return a list of selectable sub-folder dicts for a local drive.
    Each dict: {name, path, emoji, file_count_approx}
    """
    result = []
    if not device.path:
        return result

    root = Path(device.path)
    # Common user home directories
    home = Path.home()
    home_drive = home.drive.upper() + "\\"

    # If this is the drive containing the user home, suggest standard folders
    if root.drive.upper() + "\\" == home_drive:
        result.append({
            "name": "All User Folders (Desktop + Documents + Downloads + Pictures + Music + Videos)",
            "path": str(home),
            "emoji": "👤",
            "quick_key": "a",
        })
        for folder_name, folder_emoji in USER_FOLDERS:
            p = home / folder_name
            if p.exists():
                result.append({
                    "name": f"{folder_name}",
                    "path": str(p),
                    "emoji": folder_emoji,
                    "quick_key": folder_name[0].lower(),
                })

    result.append({
        "name": f"Custom path on {device.label}...",
        "path": "__custom__",
        "emoji": "📁",
        "quick_key": "c",
    })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def discover_all() -> List[StorageDevice]:
    """
    Discover and return all connected storage devices.
    Local drives are listed first, then Android devices.
    """
    local_fn = _discover_local_windows if sys.platform == "win32" else _discover_local_unix
    local = local_fn()
    android = _discover_android()
    return local + android
