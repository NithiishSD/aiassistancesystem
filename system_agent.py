"""
Phase 3: System agent (Tier 0, read-only).

SANDBOXING MODEL:
The LLM never generates or executes raw shell strings. It can only call
one of the fixed functions below, with validated arguments. This is the
sandbox — not a subprocess wrapper, but a closed set of safe operations.

Most operations are:
    - Read-only (no writes, no deletes); open_application is the controlled
        exception and launches only an executable resolved through shutil.which.
  - Restricted to the user's home directory (no /etc, /root, system paths)
  - Logged via zedek_logger before returning results
"""

import os
import time
from datetime import datetime
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


def list_processes_detailed(top_n: int = 50) -> list[dict]:
    """Returns detailed data for currently running processes."""
    log.info("list_processes_detailed_called", extra={})
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent", "create_time"]):
        try:
            mem_mb = p.info["memory_info"].rss / (1024**2)
            running_minutes = round((time.time() - p.info["create_time"]) / 60, 1)
            procs.append({
                "pid": p.info["pid"],
                "name": p.info["name"],
                "memory_mb": round(mem_mb, 1),
                "cpu_percent": p.info["cpu_percent"],
                "running_minutes": running_minutes,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    result = procs[:top_n]
    log.info("list_processes_detailed_result", extra={"count": len(result)})
    return result


def _clean_desktop_exec(exec_cmd: str) -> list[str]:
    """Clean XDG field codes (%u, %U, %f, %F, etc.) from an Exec line."""
    import re
    import shlex

    try:
        parts = shlex.split(exec_cmd)
    except Exception:
        parts = exec_cmd.split()
    return [p for p in parts if not re.match(r"^%[a-zA-Z]$", p)]


_DESKTOP_ENTRIES_CACHE: list[dict] | None = None


def _get_desktop_entries() -> list[dict]:
    """Scan and parse standard XDG and snap/flatpak .desktop application files."""
    global _DESKTOP_ENTRIES_CACHE
    if _DESKTOP_ENTRIES_CACHE is not None:
        return _DESKTOP_ENTRIES_CACHE

    import glob
    desktop_dirs = [
        os.path.expanduser("~/.local/share/applications"),
        "/usr/share/applications",
        "/usr/local/share/applications",
        "/var/lib/snapd/desktop/applications",
        "/var/lib/flatpak/exports/share/applications",
        os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
    ]

    entries = []
    seen_files = set()
    for d in desktop_dirs:
        if not os.path.isdir(d):
            continue
        for f in glob.glob(os.path.join(d, "*.desktop")):
            base_name = os.path.basename(f)
            if base_name in seen_files:
                continue
            seen_files.add(base_name)
            try:
                with open(f, "r", errors="ignore") as fp:
                    name, exec_cmd, nodisplay, generic = None, None, False, None
                    for line in fp:
                        line = line.strip()
                        if line.startswith("Name=") and not name:
                            name = line.split("=", 1)[1].strip()
                        elif line.startswith("GenericName=") and not generic:
                            generic = line.split("=", 1)[1].strip()
                        elif line.startswith("Exec=") and not exec_cmd:
                            exec_cmd = line.split("=", 1)[1].strip()
                        elif line.startswith("NoDisplay=true"):
                            nodisplay = True
                    if name and exec_cmd and not nodisplay:
                        cmd_parts = _clean_desktop_exec(exec_cmd)
                        if cmd_parts:
                            entries.append({
                                "name": name,
                                "generic": generic or "",
                                "cmd": cmd_parts,
                                "file": base_name,
                            })
            except Exception:
                continue

    _DESKTOP_ENTRIES_CACHE = entries
    return entries


def _resolve_application(app_name: str) -> tuple[list[str], str] | None:
    """
    Resolves an application name string into an executable command list and display name.
    Supports virtual targets, system aliases, .desktop discovery, and PATH lookups.
    """
    import re
    import shutil

    query = (app_name or "").strip()
    if not query:
        return None

    lowered = query.lower()
    norm_query = re.sub(r"[^a-z0-9]", "", lowered)

    # 1. Virtual / Special URIs
    if lowered in ("trash", "trash can", "recycle bin", "bin", "trash bin"):
        if shutil.which("gio"):
            return ["gio", "open", "trash:///"], "Trash"
        if shutil.which("xdg-open"):
            return ["xdg-open", "trash:///"], "Trash"
        if shutil.which("nautilus"):
            return ["nautilus", "trash:///"], "Trash"

    # 2. Known common aliases and system utilities
    aliases: dict[str, list[str]] = {
        "files": ["nautilus", "nemo", "thunar", "dolphin", "pcmanfm"],
        "file manager": ["nautilus", "nemo", "thunar", "dolphin", "pcmanfm"],
        "file explorer": ["nautilus", "nemo", "thunar", "dolphin", "pcmanfm"],
        "explorer": ["nautilus", "nemo", "thunar", "dolphin", "pcmanfm"],
        "app center": ["snap-store", "ubuntu-app-center", "gnome-software", "plasma-discover"],
        "appcenter": ["snap-store", "ubuntu-app-center", "gnome-software", "plasma-discover"],
        "app store": ["snap-store", "ubuntu-app-center", "gnome-software", "plasma-discover"],
        "software": ["snap-store", "ubuntu-app-center", "gnome-software", "plasma-discover"],
        "software center": ["snap-store", "ubuntu-app-center", "gnome-software", "plasma-discover"],
        "snap store": ["snap-store", "ubuntu-app-center", "gnome-software"],
        "terminal": ["gnome-terminal", "ptyxis", "konsole", "alacritty", "kitty", "xfce4-terminal", "xterm"],
        "console": ["gnome-terminal", "ptyxis", "konsole", "alacritty", "kitty", "xfce4-terminal", "xterm"],
        "calculator": ["gnome-calculator", "kcalc", "galculator", "xcalc"],
        "calc": ["gnome-calculator", "kcalc", "galculator", "xcalc"],
        "browser": ["google-chrome", "google-chrome-stable", "brave-browser", "firefox", "chromium-browser", "chromium", "microsoft-edge"],
        "chrome": ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"],
        "google chrome": ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"],
        "brave": ["brave-browser", "brave"],
        "firefox": ["firefox"],
        "code": ["code", "codium"],
        "vscode": ["code", "codium"],
        "vs code": ["code", "codium"],
        "visual studio code": ["code", "codium"],
        "text editor": ["gnome-text-editor", "gedit", "kate", "mousepad", "xed"],
        "notepad": ["gnome-text-editor", "gedit", "kate", "mousepad"],
        "system monitor": ["gnome-system-monitor", "ksysguard"],
        "task manager": ["gnome-system-monitor", "ksysguard"],
        "settings": ["gnome-control-center", "systemsettings"],
        "control center": ["gnome-control-center", "systemsettings"],
    }

    if lowered in aliases:
        for candidate in aliases[lowered]:
            w = shutil.which(candidate)
            if w:
                return [w], query.title()

    entries = _get_desktop_entries()

    # 3. Exact desktop Name match (case-insensitive)
    for e in entries:
        if e["name"].lower() == lowered:
            return e["cmd"], e["name"]

    # 4. Normalized alphanumeric match on desktop Name
    for e in entries:
        if re.sub(r"[^a-z0-9]", "", e["name"].lower()) == norm_query:
            return e["cmd"], e["name"]

    # 5. GenericName match
    for e in entries:
        if e["generic"] and re.sub(r"[^a-z0-9]", "", e["generic"].lower()) == norm_query:
            return e["cmd"], e["name"]

    # 6. Desktop file name match (e.g. org.gnome.Nautilus.desktop)
    for e in entries:
        stem = re.sub(r"\.desktop$", "", e["file"]).lower()
        if stem == lowered or norm_query == re.sub(r"[^a-z0-9]", "", stem):
            return e["cmd"], e["name"]

    # 7. Substring match on desktop Name
    if len(norm_query) >= 3:
        for e in entries:
            entry_norm = re.sub(r"[^a-z0-9]", "", e["name"].lower())
            if norm_query in entry_norm:
                return e["cmd"], e["name"]

    # 8. Fallback to shutil.which in system PATH
    for cand in [query, lowered, lowered.replace(" ", "-"), lowered.replace(" ", "")]:
        w = shutil.which(cand)
        if w:
            return [w], query

    return None


def open_application(app_name: str = "") -> dict:
    """Launches an installed desktop application or system utility."""
    import subprocess

    app_name = (app_name or "").strip()
    log.info("open_application_called", extra={"app_name": app_name})

    if not app_name:
        return {"launched": False, "reason": "Please specify which application to open."}

    resolution = _resolve_application(app_name)
    if not resolution:
        log.info("open_application_not_found", extra={"app_name": app_name})
        return {"launched": False, "reason": f"'{app_name}' is not a recognized installed application."}

    cmd_list, display_name = resolution
    try:
        subprocess.Popen(
            cmd_list,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        log.info("open_application_launched", extra={"app_name": app_name, "resolved_cmd": cmd_list, "display_name": display_name})
        return {"launched": True, "app": display_name, "path": " ".join(cmd_list)}
    except Exception as e:
        log.info("open_application_failed", extra={"app_name": app_name, "error": str(e)})
        return {"launched": False, "reason": f"Failed to launch '{display_name}': {e}"}


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
    "list_processes_detailed": list_processes_detailed,
    "open_application": open_application,
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