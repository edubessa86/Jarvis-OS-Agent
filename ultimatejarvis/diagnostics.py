"""Lightweight hardware and runtime diagnostics; no heavyweight ML imports."""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any


def _memory_bytes() -> int | None:
    """Return total physical RAM where a standard-library probe is available."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", wintypes.DWORD),
                    ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
        except (AttributeError, OSError, TypeError):
            return None
    if hasattr(os, "sysconf"):
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            if pages > 0 and page_size > 0:
                return int(pages * page_size)
        except (ValueError, OSError):
            pass
    return None


def collect_diagnostics(workspace: str | Path | None = None) -> dict[str, Any]:
    """Return basic system facts without probing network, GPU, or user files."""
    ram = _memory_bytes()
    result: dict[str, Any] = {
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "logical_cpu_count": os.cpu_count(),
        "total_ram_bytes": ram,
        "total_ram_gib": round(ram / (1024 ** 3), 2) if ram is not None else None,
        "gpu": "not probed",
        "model_runtime": "not probed",
    }
    if workspace is not None:
        path = Path(workspace).expanduser()
        if path.exists() and path.is_dir():
            usage = shutil.disk_usage(path)
            result["workspace_disk_free_bytes"] = usage.free
    return result
