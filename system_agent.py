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


def directory_size(path: str = ".") -> dict:
    """Returns the total size of a specific directory (recursively), not a breakdown of subfolders."""
    safe_path = _validate_path(path)
    log.info("directory_size_called", extra={"path": safe_path})

    total = 0
    for dirpath, _, filenames in os.walk(safe_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except (OSError, FileNotFoundError):
                continue

    result = {"path": safe_path, "size_gb": round(total / (1024**3), 3)}
    log.info("directory_size_result", extra=result)
    return result


def list_directory_contents(path: str = "~", top_n: int = 30) -> list[dict]:
    """Lists files and folders directly inside a directory (like 'ls')."""
    safe_path = _validate_path(path)
    log.info("list_directory_called", extra={"path": safe_path})

    items = []
    try:
        with os.scandir(safe_path) as entries:
            for entry in entries:
                items.append({
                    "name": entry.name,
                    "type": "folder" if entry.is_dir(follow_symlinks=False) else "file",
                })
    except PermissionError as e:
        log.info("list_directory_permission_error", extra={"error": str(e)})

    result = items[:top_n]
    log.info("list_directory_result", extra={"count": len(result)})
    return result


def file_info(path: str) -> dict:
    """Returns metadata about a single file: size, last modified, type."""
    safe_path = _validate_path(path)
    log.info("file_info_called", extra={"path": safe_path})

    if not os.path.exists(safe_path):
        return {"path": safe_path, "exists": False}

    stat = os.stat(safe_path)
    result = {
        "path": safe_path,
        "exists": True,
        "size_mb": round(stat.st_size / (1024**2), 3),
        "last_modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "type": "folder" if os.path.isdir(safe_path) else "file",
    }
    log.info("file_info_result", extra=result)
    return result


def recently_modified_files(root_dir: str = "~", hours: int = 24, top_n: int = 15) -> list[dict]:
    """Finds files modified within the last N hours."""
    safe_root = _validate_path(root_dir)
    log.info("recently_modified_called", extra={"root": safe_root, "hours": hours})

    cutoff = time.time() - (hours * 3600)
    matches = []
    for dirpath, _, filenames in os.walk(safe_root):
        for fname in filenames:
            fp = os.path.join(dirpath, fname)
            try:
                mtime = os.path.getmtime(fp)
                if mtime >= cutoff:
                    matches.append({"path": fp, "modified": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")})
            except (OSError, FileNotFoundError):
                continue
        if len(matches) >= 200:
            break

    matches.sort(key=lambda x: x["modified"], reverse=True)
    result = matches[:top_n]
    log.info("recently_modified_result", extra={"count": len(result)})
    return result


def cpu_usage() -> dict:
    """Returns current CPU load percentage."""
    log.info("cpu_usage_called", extra={})
    percent = psutil.cpu_percent(interval=0.5)
    result = {"cpu_percent": percent}
    log.info("cpu_usage_result", extra=result)
    return result


def battery_status() -> dict:
    """Returns battery percentage and charging state, if available."""
    log.info("battery_status_called", extra={})
    battery = psutil.sensors_battery()
    if battery is None:
        result = {"available": False}
    else:
        result = {"available": True, "percent": battery.percent, "charging": battery.power_plugged}
    log.info("battery_status_result", extra=result)
    return result


def system_uptime() -> dict:
    """Returns how long the system has been running."""
    log.info("system_uptime_called", extra={})
    boot_time = psutil.boot_time()
    uptime_seconds = time.time() - boot_time
    hours = int(uptime_seconds // 3600)
    minutes = int((uptime_seconds % 3600) // 60)
    result = {"uptime_hours": hours, "uptime_minutes": minutes}
    log.info("system_uptime_result", extra=result)
    return result


def check_internet_connection() -> dict:
    """Checks whether the system currently has internet connectivity."""
    log.info("check_internet_called", extra={})
    import socket
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        result = {"connected": True}
    except OSError:
        result = {"connected": False}
    log.info("check_internet_result", extra=result)
    return result


def current_datetime() -> dict:
    """Returns the current system date and time."""
    now = datetime.now()
    result = {"date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M:%S"), "day": now.strftime("%A")}
    log.info("current_datetime_result", extra=result)
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
    "directory_size": directory_size,
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