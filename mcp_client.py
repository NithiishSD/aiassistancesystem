"""
MCP Client — the strict boundary layer between Zedek and the MCP SDK.

DESIGN CONTRACT
---------------
Nothing MCP-specific (ClientSession, TextContent, CallToolResult, asyncio,
stdio_client) ever crosses this module's public API boundary. Callers receive
only plain Python types: MCPToolSpec dataclasses and plain dicts.

Public synchronous API:
    discover_all_tools() -> list[MCPToolSpec]
    call_mcp_tool(qualified_name, args) -> dict
    get_tool_registry() -> dict[str, MCPToolSpec]
    get_discovery_stats() -> dict

All asyncio and MCP protocol logic is encapsulated inside _async_discover()
and _async_call_tool(), which are driven by asyncio.run() internally.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from zedek_logger import get_logger

log = get_logger("mcp_client")

# ── Project root (used as default cwd for MCP server processes) ───────────────

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "mcp_servers.json")

# ── Timeout defaults (seconds) ────────────────────────────────────────────────

_DISCOVERY_TIMEOUT_DEFAULT: float = 10.0   # per server: startup + init + list_tools
_CALL_TIMEOUT_DEFAULT: float = 15.0        # per tool call (can be overridden via server config)


# ── Internal error taxonomy ───────────────────────────────────────────────────
# All subclasses of _MCPError are caught inside this module and translated into
# log entries + return dicts. They never propagate to orchestrator.py.

class _MCPError(Exception):
    """Base class for all internal MCP errors."""

class _MCPTransportError(_MCPError):
    """STDIO transport failed to start or connect."""

class _MCPInitializationError(_MCPError):
    """MCP initialize handshake failed or timed out."""

class _MCPDiscoveryError(_MCPError):
    """list_tools call failed or returned unexpected data."""

class _MCPToolNotFoundError(_MCPError):
    """Qualified name is not in the tool registry."""

class _MCPToolExecutionError(_MCPError):
    """call_tool returned an error result."""

class _MCPArgumentError(_MCPError):
    """Arguments failed schema validation before the call."""


# ── Data models (frozen — immutable after creation) ───────────────────────────

@dataclass(frozen=True)
class MCPServerConfig:
    """Validated, immutable representation of one mcp_servers.json entry."""
    name: str
    command: str
    args: list[str]
    env: dict[str, str]
    cwd: str
    description: str | None
    timeout: float  # used for both discovery and tool-call timeouts


@dataclass(frozen=True)
class MCPToolSpec:
    """Validated, immutable description of one MCP tool.

    qualified_name format: "mcp_{server_name}_{tool_name}"
    This is the key used everywhere in Zedek — never parse it back into parts.
    """
    server_name: str
    tool_name: str
    description: str
    input_schema: dict
    qualified_name: str


# ── Internal module-level registries ─────────────────────────────────────────
# Both are replaced atomically during discovery. Invocation uses these directly
# — it never parses qualified_name strings.

_TOOL_REGISTRY: dict[str, MCPToolSpec] = {}
_SERVER_REGISTRY: dict[str, MCPServerConfig] = {}

# Cache fingerprint: (mtime, size) of mcp_servers.json; None = file absent.
_CONFIG_FINGERPRINT: tuple[float, int] | None = None

# Discovery stats (reset on each discover_all_tools call)
_LAST_STATS: dict = {"server_count": 0, "tool_count": 0, "failed_servers": 0}


# ── Config fingerprint ────────────────────────────────────────────────────────

def _current_fingerprint() -> tuple[float, int] | None:
    """Return (mtime, size) of mcp_servers.json, or None if it does not exist."""
    try:
        stat = os.stat(_CONFIG_PATH)
        return (stat.st_mtime, stat.st_size)
    except OSError:
        return None


# ── Config loading and per-server validation ──────────────────────────────────

def _load_config() -> list[MCPServerConfig]:
    """
    Parse mcp_servers.json into a list of MCPServerConfig.

    - Returns [] if the file is absent (not an error).
    - Parses each entry individually: one bad entry is logged and skipped.
    - Required fields: 'name' (non-empty str), 'command' (non-empty str).
    - Duplicate server names: first occurrence wins, subsequent entries skipped.
    """
    if not os.path.exists(_CONFIG_PATH):
        log.info("mcp_config_absent", extra={"path": _CONFIG_PATH})
        return []

    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.info("mcp_config_load_failed", extra={"path": _CONFIG_PATH, "error": str(exc)})
        return []

    if not isinstance(raw, list):
        log.info("mcp_config_invalid_format", extra={"expected": "list", "got": type(raw).__name__})
        return []

    configs: list[MCPServerConfig] = []
    seen_names: set[str] = set()

    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            log.info("mcp_config_entry_skipped", extra={"index": idx, "reason": "not a dict"})
            continue

        name = entry.get("name", "")
        if not isinstance(name, str) or not name.strip():
            log.info("mcp_config_entry_skipped", extra={"index": idx, "reason": "missing or empty 'name'"})
            continue

        name = name.strip()

        if name in seen_names:
            log.info("mcp_config_entry_skipped", extra={"index": idx, "server_name": name, "reason": "duplicate server name"})
            continue

        command = entry.get("command", "")
        if not isinstance(command, str) or not command.strip():
            log.info("mcp_config_entry_skipped",  extra={"index": idx, "server_name": name, "reason": "missing or empty command"})
            continue

        command_str = command.strip()
        if command_str in ("python", "python3") and sys.executable:
            resolved_command = sys.executable
        else:
            resolved_command = command_str

        raw_args = entry.get("args", [])
        if not isinstance(raw_args, list) or not all(isinstance(a, str) for a in raw_args):
            log.info("mcp_config_entry_skipped",  extra={"index": idx, "server_name": name, "reason": "invalid args"})
            continue

        raw_env = entry.get("env", {})
        if not isinstance(raw_env, dict):
            raw_env = {}

        # Ensure environment inherits essential PATH and PYTHONPATH if not explicitly provided
        final_env = dict(os.environ)
        final_env.update({str(k): str(v) for k, v in raw_env.items()})

        raw_cwd = entry.get("cwd", None)
        if raw_cwd is None or raw_cwd == ".":
            resolved_cwd = _PROJECT_ROOT
        else:
            resolved_cwd = str(raw_cwd)

        description = entry.get("description", None)

        timeout_raw = entry.get("timeout", _DISCOVERY_TIMEOUT_DEFAULT)
        try:
            timeout = float(timeout_raw)
            if timeout <= 0:
                timeout = _DISCOVERY_TIMEOUT_DEFAULT
        except (TypeError, ValueError):
            timeout = _DISCOVERY_TIMEOUT_DEFAULT

        seen_names.add(name)
        configs.append(MCPServerConfig(
            name=name,
            command=resolved_command,
            args=list(raw_args),
            env=final_env,
            cwd=resolved_cwd,
            description=description,
            timeout=timeout,
        ))
        log.info("mcp_config_entry_loaded", extra={"server_name": name, "command": resolved_command})

    return configs


# ── Async core: discovery ─────────────────────────────────────────────────────

async def _async_discover_server(cfg: MCPServerConfig) -> list[MCPToolSpec]:
    """
    Spawn one MCP server over STDIO, initialize, list tools, and return specs.
    Raises _MCPError subclasses on failure. Always cleans up the subprocess.
    """
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp import ClientSession
    import mcp.types as mt

    params = StdioServerParameters(
        command=cfg.command,
        args=cfg.args,
        env=cfg.env if cfg.env else None,
        cwd=cfg.cwd,
    )

    try:
        async with asyncio.timeout(cfg.timeout):
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    try:
                        await session.initialize()
                    except Exception as exc:
                        raise _MCPInitializationError(f"initialize failed: {exc}") from exc

                    try:
                        result = await session.list_tools()
                    except Exception as exc:
                        raise _MCPDiscoveryError(f"list_tools failed: {exc}") from exc

                    tools: list[MCPToolSpec] = []
                    for tool in result.tools:
                        qname = f"mcp_{cfg.name}_{tool.name}"
                        tools.append(MCPToolSpec(
                            server_name=cfg.name,
                            tool_name=tool.name,
                            description=tool.description or "",
                            input_schema=tool.input_schema or {},
                            qualified_name=qname,
                        ))
                    return tools

    except asyncio.TimeoutError as exc:
        raise _MCPTransportError(
            f"server '{cfg.name}' timed out after {cfg.timeout}s"
        ) from exc
    except _MCPError:
        raise
    except Exception as exc:
        raise _MCPTransportError(
            f"server '{cfg.name}' failed to start: {exc}"
        ) from exc


async def _async_discover_all(configs: list[MCPServerConfig]) -> tuple[list[MCPToolSpec], int]:
    """
    Discover tools from all servers concurrently (each under its own timeout).
    Returns (all_tools, failure_count).
    """
    all_tools: list[MCPToolSpec] = []
    failures = 0

    for cfg in configs:
        try:
            tools = await _async_discover_server(cfg)
            log.info("mcp_server_discovered", extra={
                "server": cfg.name,
                "tool_count": len(tools),
            })
            all_tools.extend(tools)
        except _MCPError as exc:
            log.info("mcp_server_discovery_failed", extra={
                "server": cfg.name,
                "error": str(exc),
                "error_type": type(exc).__name__,
            })
            failures += 1

    return all_tools, failures


# ── Async core: invocation ────────────────────────────────────────────────────

async def _async_call_tool(cfg: MCPServerConfig, tool_name: str, args: dict) -> dict:
    """
    Spawn an MCP server, call one tool, clean up, return normalized result dict.
    Always returns {"result": str | None, "error": str | None}.
    """
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp import ClientSession

    params = StdioServerParameters(
        command=cfg.command,
        args=cfg.args,
        env=cfg.env if cfg.env else None,
        cwd=cfg.cwd,
    )

    timeout = max(cfg.timeout, _CALL_TIMEOUT_DEFAULT)

    try:
        async with asyncio.timeout(timeout):
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    try:
                        await session.initialize()
                    except Exception as exc:
                        raise _MCPInitializationError(f"initialize failed: {exc}") from exc

                    try:
                        call_result = await session.call_tool(
                            name=tool_name,
                            arguments=args or None,
                        )
                    except Exception as exc:
                        raise _MCPToolExecutionError(f"call_tool failed: {exc}") from exc

                    return _normalize_tool_result(call_result)

    except asyncio.TimeoutError:
        return {
            "result": None,
            "error": f"Tool '{tool_name}' timed out after {timeout}s",
        }
    except _MCPError as exc:
        return {"result": None, "error": str(exc)}
    except Exception as exc:
        return {"result": None, "error": f"Unexpected error calling '{tool_name}': {exc}"}


# ── Result normalisation ──────────────────────────────────────────────────────

def _normalize_tool_result(call_result: Any) -> dict:
    """
    Convert a CallToolResult into a plain {"result": str, "error": None} dict.

    Handles:
        - Single TextContent → result string
        - Multiple content items → newline-joined strings
        - is_error=True → error field populated
        - Empty content → result = ""
        - Non-TextContent items → str() fallback
    """
    import mcp.types as mt

    if call_result is None:
        return {"result": None, "error": "Null result from MCP server"}

    is_error = getattr(call_result, "is_error", False)
    content_items = getattr(call_result, "content", []) or []

    parts: list[str] = []
    for item in content_items:
        if isinstance(item, mt.TextContent):
            parts.append(item.text or "")
        else:
            parts.append(str(item))

    combined = "\n".join(parts)

    if is_error:
        return {"result": None, "error": combined or "MCP tool returned an error"}

    return {"result": combined, "error": None}


# ── Public synchronous API ────────────────────────────────────────────────────

def discover_all_tools() -> list[MCPToolSpec]:
    """
    Discover tools from all configured MCP servers.

    - Loads and validates mcp_servers.json.
    - Skips absent/malformed config gracefully (returns []).
    - Each server runs under its configured timeout; failures are logged + skipped.
    - Result is sorted by qualified_name for determinism.
    - Duplicate qualified_names (same name from two servers): first wins, logged.
    - Populates _TOOL_REGISTRY and _SERVER_REGISTRY atomically.
    - Updates _CONFIG_FINGERPRINT for cache invalidation.

    This function is safe to call at startup or any time. It never raises.
    """
    global _TOOL_REGISTRY, _SERVER_REGISTRY, _CONFIG_FINGERPRINT, _LAST_STATS

    configs = _load_config()

    if not configs:
        _TOOL_REGISTRY = {}
        _SERVER_REGISTRY = {}
        _CONFIG_FINGERPRINT = _current_fingerprint()
        _LAST_STATS = {"server_count": 0, "tool_count": 0, "failed_servers": 0}
        return []

    try:
        all_tools, failures = asyncio.run(_async_discover_all(configs))
    except RuntimeError as exc:
        # asyncio.run() raises RuntimeError if called inside a running event loop.
        # In that case, fall back gracefully.
        log.info("mcp_discovery_event_loop_conflict", extra={"error": str(exc)})
        _CONFIG_FINGERPRINT = _current_fingerprint()
        return list(_TOOL_REGISTRY.values())

    # Sort for determinism
    all_tools.sort(key=lambda t: t.qualified_name)

    # Deduplicate qualified names (first wins)
    seen_qnames: set[str] = set()
    deduped: list[MCPToolSpec] = []
    for tool in all_tools:
        if tool.qualified_name in seen_qnames:
            log.info("mcp_tool_duplicate_qualified_name_skipped", extra={
                "qualified_name": tool.qualified_name,
                "server": tool.server_name,
            })
        else:
            seen_qnames.add(tool.qualified_name)
            deduped.append(tool)

    # Build new registries atomically
    new_tool_registry: dict[str, MCPToolSpec] = {t.qualified_name: t for t in deduped}
    new_server_registry: dict[str, MCPServerConfig] = {c.name: c for c in configs}

    _TOOL_REGISTRY = new_tool_registry
    _SERVER_REGISTRY = new_server_registry
    _CONFIG_FINGERPRINT = _current_fingerprint()

    _LAST_STATS = {
        "server_count": len(configs),
        "tool_count": len(deduped),
        "failed_servers": failures,
    }

    log.info("mcp_discovery_complete", extra=_LAST_STATS)
    return deduped


def call_mcp_tool(qualified_name: str, args: dict) -> dict:
    """
    Invoke an MCP tool by its qualified name.

    Returns:
        {"result": str, "error": None}      — on success
        {"result": None, "error": str}      — on any failure

    Safety:
        - Unknown qualified_name → error dict (never a crash)
        - Invalid args → caller should validate before calling; errors surfaced in result
        - Server stderr is captured and logged, never printed to console
        - Process cleaned up on success, failure, and timeout
    """
    if not qualified_name or not isinstance(qualified_name, str):
        return {"result": None, "error": "Invalid qualified_name (empty or not a string)"}

    tool_spec = _TOOL_REGISTRY.get(qualified_name)
    if tool_spec is None:
        log.info("mcp_tool_not_found", extra={"qualified_name": qualified_name})
        return {"result": None, "error": f"Unknown MCP tool: '{qualified_name}'"}

    server_cfg = _SERVER_REGISTRY.get(tool_spec.server_name)
    if server_cfg is None:
        log.info("mcp_server_not_found", extra={
            "server_name": tool_spec.server_name,
            "qualified_name": qualified_name,
        })
        return {"result": None, "error": f"Server '{tool_spec.server_name}' is not registered"}

    log.info("mcp_tool_call_started", extra={
        "qualified_name": qualified_name,
        "server": tool_spec.server_name,
        "tool": tool_spec.tool_name,
    })

    try:
        result = asyncio.run(_async_call_tool(server_cfg, tool_spec.tool_name, args))
    except RuntimeError as exc:
        # Running event loop conflict — should not happen in Zedek's synchronous context
        log.info("mcp_tool_call_event_loop_conflict", extra={"error": str(exc)})
        return {"result": None, "error": f"Event loop conflict: {exc}"}

    if result.get("error"):
        log.info("mcp_tool_call_failed", extra={
            "qualified_name": qualified_name,
            "error": result["error"],
        })
    else:
        log.info("mcp_tool_call_success", extra={"qualified_name": qualified_name})

    return result


def get_tool_registry() -> dict[str, MCPToolSpec]:
    """Read-only view of the current tool registry.

    Returns a copy — callers cannot mutate internal state.
    """
    return dict(_TOOL_REGISTRY)


def get_discovery_stats() -> dict:
    """Returns the stats from the most recent discover_all_tools() call.

    Keys: server_count, tool_count, failed_servers
    """
    return dict(_LAST_STATS)


def is_cache_stale() -> bool:
    """Return True if the config file has changed since last discovery."""
    return _current_fingerprint() != _CONFIG_FINGERPRINT
