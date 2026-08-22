"""
Phase 3: System agent (Tier 0, read-only).

SANDBOXING MODEL:
The LLM never generates or executes raw shell strings. It can only call
one of the fixed functions below, with validated arguments. This is the
sandbox — not a subprocess wrapper, but a closed set of safe operations.

All operations are:
  - Read-only (no writes, no deletes)
  - Restricted to the user's home directory (no /etc, /root, system paths)
  - Logged via zedek_logger before returning results
"""

import os
import psutil
from pathlib import Path
from zedek_logger import get_logger

log = get_logger("system_agent")

# Hard boundary: nothing outside the home directory is ever touched.
HOME_DIR = str(Path.home())


def _validate_path(path: str) -> str:
    """
    Resolves a path and ensures it stays inside HOME_DIR.
    Raises ValueError if the path tries to escape (e.g. via ../../etc).
    """
    resolved = os.path.realpath(os.path.expanduser(path))
    if not resolved.startswith(HOME_DIR):
        raise ValueError(f"Path '{path}' is outside the allowed directory ({HOME_DIR}).")
    return resolved


def search_files(query: str, root_dir: str = "~") -> list[str]:
    """Search for files by name (substring match) under root_dir."""
    safe_root = _validate_path(root_dir)
    log.info("search_files_called", extra={"query": query, "root": safe_root})

    matches = []
    for dirpath, _, filenames in os.walk(safe_root):
        for fname in filenames:
            if query.lower() in fname.lower():
                matches.append(os.path.join(dirpath, fname))
        if len(matches) >= 50:  # cap results, avoid runaway output
            break

    log.info("search_files_result", extra={"query": query, "matches_found": len(matches)})
    return matches


def disk_usage_by_folder(root_dir: str = "~", top_n: int = 10) -> list[dict]:
    """Returns the top_n largest immediate subfolders under root_dir."""
    safe_root = _validate_path(root_dir)
    log.info("disk_usage_called", extra={"root": safe_root})

    sizes = []
    try:
        with os.scandir(safe_root) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    total = 0
                    for dirpath, _, filenames in os.walk(entry.path):
                        for f in filenames:
                            fp = os.path.join(dirpath, f)
                            try:
                                total += os.path.getsize(fp)
                            except (OSError, FileNotFoundError):
                                continue
                    sizes.append({"folder": entry.path, "size_gb": round(total / (1024**3), 2)})
    except PermissionError as e:
        log.info("disk_usage_permission_error", extra={"error": str(e)})

    sizes.sort(key=lambda x: x["size_gb"], reverse=True)
    result = sizes[:top_n]
    log.info("disk_usage_result", extra={"top_folders": result})
    return result


def top_memory_processes(top_n: int = 10) -> list[dict]:
    """Returns the top_n processes by memory usage (system-wide, not filesystem-restricted)."""
    log.info("memory_check_called", extra={})
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            mem_mb = p.info["memory_info"].rss / (1024**2)
            procs.append({"pid": p.info["pid"], "name": p.info["name"], "memory_mb": round(mem_mb, 1)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda x: x["memory_mb"], reverse=True)
    result = procs[:top_n]
    log.info("memory_check_result", extra={"top_processes": result})
    return result


def free_space_summary() -> dict:
    """Returns overall disk free/used space for the home partition."""
    log.info("free_space_called", extra={})
    total, used, free, _percent = psutil.disk_usage(HOME_DIR)
    result = {
        "total_gb": round(total / (1024**3), 1),
        "used_gb": round(used / (1024**3), 1),
        "free_gb": round(free / (1024**3), 1),
    }
    log.info("free_space_result", extra=result)
    return result


# The allowlist the orchestrator/LLM is permitted to call — nothing else.
AVAILABLE_FUNCTIONS = {
    "search_files": search_files,
    "disk_usage_by_folder": disk_usage_by_folder,
    "top_memory_processes": top_memory_processes,
    "free_space_summary": free_space_summary,
}


if __name__ == "__main__":
    print("=== System agent self-test ===")
    print("\nFree space:", free_space_summary())
    print("\nTop 5 memory processes:")
    for p in top_memory_processes(5):
        print(f"  {p['name']} (pid {p['pid']}): {p['memory_mb']} MB")
    print("\nTop 5 largest folders in home:")
    for f in disk_usage_by_folder(top_n=5):
        print(f"  {f['folder']}: {f['size_gb']} GB")
