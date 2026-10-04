"""Cross-platform resident memory snapshots for the current Python process."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


def _current_process_memory_snapshot() -> dict[str, Any]:
    """Return current and peak RSS in bytes, or ``None`` when unavailable.

    Metrics describe this process only, never its child processes. Linux and
    WSL use procfs; Windows uses PROCESS_MEMORY_COUNTERS through the native API.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            get_process = ctypes.windll.kernel32.GetCurrentProcess
            get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
            if hasattr(get_process, "restype"):
                get_process.restype = wintypes.HANDLE
            if hasattr(get_memory, "argtypes"):
                get_memory.argtypes = [
                    wintypes.HANDLE,
                    ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                    wintypes.DWORD,
                ]
                get_memory.restype = wintypes.BOOL
            ok = get_memory(
                get_process(), ctypes.byref(counters), counters.cb
            )
            if not ok:
                raise OSError("GetProcessMemoryInfo failed")
            return {
                "current_rss_bytes": int(counters.WorkingSetSize),
                "peak_rss_bytes": int(counters.PeakWorkingSetSize),
                "source": "windows:GetProcessMemoryInfo",
                "available": True,
                "scope": "current_process",
                "peak_scope": "process_lifetime",
                "unit": "bytes",
                "error_reason": None,
            }
        except Exception as exc:
            return _unavailable(f"windows:{type(exc).__name__}: {exc}")

    try:
        values: dict[str, int] = {}
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            if line.startswith(("VmRSS:", "VmHWM:")):
                key, value, unit = line.split()[:3]
                if unit.lower() != "kb":
                    raise ValueError(f"unexpected procfs unit: {unit}")
                values[key[:-1]] = int(value) * 1024
        if "VmRSS" not in values:
            raise ValueError("VmRSS missing from /proc/self/status")
        return {
            "current_rss_bytes": values["VmRSS"],
            "peak_rss_bytes": values.get("VmHWM"),
            "source": "procfs:/proc/self/status",
            "available": True,
            "scope": "current_process",
            "peak_scope": "process_lifetime",
            "unit": "bytes",
            "error_reason": None if "VmHWM" in values else "VmHWM unavailable",
        }
    except Exception as exc:
        try:
            import psutil

            current = int(psutil.Process(os.getpid()).memory_info().rss)
            return {
                "current_rss_bytes": current,
                "peak_rss_bytes": None,
                "source": "psutil:Process.memory_info",
                "available": True,
                "scope": "current_process",
                "peak_scope": None,
                "unit": "bytes",
                "error_reason": "peak RSS is unavailable from this fallback",
            }
        except Exception as fallback_exc:
            return _unavailable(
                f"{type(exc).__name__}: {exc}; "
                f"fallback {type(fallback_exc).__name__}: {fallback_exc}"
            )


def process_memory_snapshot() -> dict[str, Any]:
    """Return parent and sampled process-tree RSS when psutil is available."""
    snapshot = _current_process_memory_snapshot()
    parent_current = snapshot.get("current_rss_bytes")
    child_current = 0
    child_peak = 0
    child_count = 0
    child_peaks_available = True
    error = None
    try:
        import psutil

        parent = psutil.Process(os.getpid())
        for child in parent.children(recursive=True):
            try:
                info = child.memory_info()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            child_current += int(info.rss)
            peak = getattr(info, "peak_wset", None)
            if peak is None:
                child_peaks_available = False
            else:
                child_peak += int(peak)
            child_count += 1
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    snapshot.update({
        "parent_current_rss_bytes": parent_current,
        "children_current_rss_bytes": child_current if child_count or error is None else None,
        "process_tree_current_rss_bytes": (
            parent_current + child_current if parent_current is not None and error is None else parent_current
        ),
        "process_tree_peak_rss_bytes": (
            snapshot.get("peak_rss_bytes") + child_peak
            if snapshot.get("peak_rss_bytes") is not None and error is None and child_peaks_available else None
        ),
        "child_process_count": child_count,
        "process_tree_error": error,
    })
    return snapshot


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "current_rss_bytes": None,
        "peak_rss_bytes": None,
        "source": None,
        "available": False,
        "scope": "current_process",
        "peak_scope": None,
        "unit": "bytes",
        "error_reason": reason,
    }
