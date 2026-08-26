"""
Command verifier for dynamically generated system-inspection and coding-agent commands.

This is an extra layer before the normal tier gate. It statically checks
commands, safely runs recognized read-only commands and pipelines, and sends write or
destructive commands to the normal confirmation gate without executing them.
"""

import re
import shlex
import subprocess

from zedek_logger import get_logger
from tier_gate import FORCE_TIER_2_PATTERNS, FORCE_TIER_3_PATTERNS

log = get_logger("command_verifier")

READ_ONLY_COMMAND_PREFIXES = [
    "ls", "cat", "grep", "egrep", "fgrep", "find", "du", "df", "ps", "echo", "pwd", "wc", "head", "tail",
    "dpkg", "dpkg-query", "apt-cache", "pacman", "rpm", "uname", "lscpu", "lsblk", "lspci", "lshw", "lsusb",
    "uptime", "free", "ip", "hostname", "which", "whereis", "arch", "whoami", "id", "nvidia-smi",
    "awk", "cut", "sed", "sort", "uniq", "tr", "date", "timedatectl", "env", "printenv", "sensors",
]


def static_check(command: str) -> dict:
    """Return whether a generated command passes static risk checks."""
    lowered = command.lower()

    for pattern in FORCE_TIER_3_PATTERNS:
        if pattern in lowered:
            log.info("static_check_blocked", extra={"command": command, "pattern": pattern, "tier": 3})
            return {"safe": False, "tier": 3, "reason": f"Contains high-risk pattern: '{pattern}'"}

    for pattern in FORCE_TIER_2_PATTERNS:
        if pattern in lowered:
            log.info("static_check_flagged", extra={"command": command, "pattern": pattern, "tier": 2})
            return {"safe": True, "tier": 2, "reason": f"Contains risky pattern: '{pattern}' — needs confirmation"}

    log.info("static_check_passed", extra={"command": command})
    return {"safe": True, "tier": 0, "reason": "No risky patterns detected"}


def is_safe_readonly_pipeline(command: str) -> bool:
    """Return whether a command string (including piped commands) is strictly read-only and safe."""
    cmd = command.strip()
    if not cmd:
        return False

    lowered = cmd.lower()
    # Check for disallowed redirection and subshell execution operators
    for token in [">", ">>", "<", "|&", ";", "&&", "||", "$(", "`", "\n", "\r"]:
        if token in lowered:
            return False

    # Check for forbidden keywords as isolated words
    for bad in ["sudo", "su", "pkexec", "eval", "exec", "tee", "rm", "mkfs", "dd", "chmod", "chown", "reboot", "shutdown", "poweroff"]:
        if re.search(rf"\b{bad}\b", lowered):
            return False

    # Split by pipe '|' and ensure every command in the pipeline starts with a read-only verb
    stages = [s.strip() for s in cmd.split("|")]
    if not stages:
        return False

    for stage in stages:
        if not stage:
            return False
        tokens = stage.split()
        first_token = tokens[0].lower()
        if "=" in first_token and len(tokens) > 1:
            first_token = tokens[1].lower()
        if first_token not in READ_ONLY_COMMAND_PREFIXES:
            return False

    return True


def is_read_only(command: str) -> bool:
    """Return whether a command is a verified safe read-only operation or pipeline."""
    return is_safe_readonly_pipeline(command)


def dry_run_readonly(command: str, timeout: int = 10) -> dict:
    """Run a recognized read-only command or safe pipeline."""
    log.info("dry_run_readonly_started", extra={"command": command})
    if not is_safe_readonly_pipeline(command):
        log.info("dry_run_readonly_rejected", extra={"command": command, "reason": "Not a safe read-only pipeline"})
        return {"success": False, "error": "Command rejected: contains disallowed operators or non-read-only commands"}

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        log.info("dry_run_readonly_complete", extra={"command": command, "returncode": result.returncode})
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        log.info("dry_run_readonly_timeout", extra={"command": command})
        return {"success": False, "error": "Command timed out"}
    except Exception as e:
        log.info("dry_run_readonly_error", extra={"command": command, "error": str(e)})
        return {"success": False, "error": str(e)}


def dry_run_destructive(command: str) -> dict:
    """Report that destructive dry-run isolation is not implemented yet."""
    log.info("dry_run_destructive_not_implemented", extra={"command": command})
    return {
        "success": False,
        "error": "Destructive dry-run isolation not yet implemented — falls through to tier gate directly.",
    }


def verify_command(command: str) -> dict:
    """Run static checks and a read-only execution when applicable."""
    check = static_check(command)
    if not check["safe"]:
        return {"proceed": False, "tier": check["tier"], "reason": check["reason"], "dry_run": None}

    if is_read_only(command):
        dry_run = dry_run_readonly(command)
        return {"proceed": dry_run["success"], "tier": check["tier"], "reason": check["reason"], "dry_run": dry_run}

    return {
        "proceed": True,
        "tier": max(check["tier"], 1),
        "reason": "Write command — no dry-run available, requires tier gate confirmation",
        "dry_run": None,
    }


if __name__ == "__main__":
    print("=== Command verifier self-test ===\n")
    for command in ["dpkg -l | grep -c '^ii'", "uname -r", "find . -name '*.py'", "rm -rf /some/path", "echo hello > file.txt"]:
        print(f"'{command}' -> {verify_command(command)}\n")

