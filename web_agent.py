"""Web / browser specialist for the Zedek assistant (Roadmap Item 10).

Drives the Playwright MCP tools toward a stated goal using a bounded
observe → act loop: decide one action, run it through the tier gate,
observe the result, decide the next action, stop when the goal is met or
the step budget runs out.

Important property of the underlying tools: they are STATELESS. Each
browser tool call takes an explicit `url` and operates on a freshly loaded
page — there is no persistent browser session carried between steps. The
loop is therefore a sequence of self-contained operations, not a session
walk, and every action must name the URL it applies to.

Safety boundaries:
  - Only the five browser_* tools are callable; the allowlist is explicit.
  - Only http:// and https:// URLs are permitted (the MCP server applies
    its own SSRF blocklist on top of this).
  - Every action passes through the tier gate. Browser tools default to
    Tier 2, so each one requires explicit confirmation. Confirmation is
    supplied by an injected callback — the agent NEVER self-approves, and
    the default callback denies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

import llm_provider
import mcp_client
from tier_gate import gate
from zedek_logger import get_logger

log = get_logger("web_agent")

NAVIGATE_TOOL = "mcp_playwright_tools_browser_navigate"
CLICK_TOOL = "mcp_playwright_tools_browser_click"
TYPE_TOOL = "mcp_playwright_tools_browser_type"
GET_TEXT_TOOL = "mcp_playwright_tools_browser_get_text"
SCREENSHOT_TOOL = "mcp_playwright_tools_browser_screenshot"

BROWSER_TOOL_ALLOWLIST = {
    NAVIGATE_TOOL,
    CLICK_TOOL,
    TYPE_TOOL,
    GET_TEXT_TOOL,
    SCREENSHOT_TOOL,
}

# Short names the planner is asked to emit, mapped to qualified MCP names.
TOOL_ALIASES = {
    "navigate": NAVIGATE_TOOL,
    "click": CLICK_TOOL,
    "type": TYPE_TOOL,
    "get_text": GET_TEXT_TOOL,
    "screenshot": SCREENSHOT_TOOL,
}

MAX_STEPS = 4
MAX_OBSERVATION_CHARS = 2500


def deny_all(_message: str) -> bool:
    """Default confirmation callback: deny. Never auto-approve a Tier 2 action."""
    return False


def is_safe_url(url: str) -> bool:
    """Only absolute http(s) URLs are allowed through this agent."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _truncate(text: str, limit: int = MAX_OBSERVATION_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[truncated]"


@dataclass
class Step:
    """One attempted browser action and what came back."""

    index: int
    tool: str
    args: dict[str, Any]
    status: str  # "ok" | "denied" | "blocked" | "error" | "rejected"
    observation: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tool": self.tool,
            "args": self.args,
            "status": self.status,
            "reason": self.reason,
            "observation": self.observation[:200],
        }


@dataclass
class BrowseReport:
    """Structured result of a browsing run."""

    goal: str
    summary: str
    steps: list[Step] = field(default_factory=list)
    completed: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
            "completed": self.completed,
            "notes": self.notes,
        }


class WebAgent:
    """Goal-directed browsing over the stateless Playwright MCP tools."""

    def __init__(self, max_steps: int = MAX_STEPS) -> None:
        self.max_steps = max_steps

    # ── Deciding the next action ─────────────────────────────────────────

    def decide_next_action(
        self,
        goal: str,
        history: list[Step],
        start_url: str | None = None,
    ) -> dict[str, Any]:
        """Ask the planner for exactly one next action.

        Returns {"tool": <alias>, "args": {...}, "done": bool, "reason": str}.
        On any failure returns a done=True decision so the loop terminates
        rather than spinning.
        """
        history_block = "\n".join(
            f"Step {s.index}: {s.tool} {json.dumps(s.args)} -> [{s.status}] {s.observation[:400]}"
            for s in history
        ) or "(nothing done yet)"

        prompt = f"""You are driving a web browser toward a goal, one action at a time.

Goal: {goal}
Suggested starting URL: {start_url or "(none given — infer one from the goal)"}

Actions already taken and what they returned:
{history_block}

Available tools (ALL are stateless — every call reloads the page, so every
action must include the full url it applies to):
- navigate      args: {{"url": "https://..."}}                       — load a page, returns its title/status
- get_text      args: {{"url": "https://...", "selector": "body"}}    — read text from a page
- click         args: {{"url": "https://...", "selector": "css"}}     — load page then click an element
- type          args: {{"url": "https://...", "selector": "css", "text": "..."}} — load page then type
- screenshot    args: {{"url": "https://..."}}                        — capture the page

Rules:
- Choose exactly ONE next action that makes progress toward the goal.
- Only http:// or https:// URLs.
- If the goal is already satisfied by what you have observed, set "done": true.
- If the goal cannot be achieved with these tools, set "done": true and explain in "reason".
- Do not repeat an action that already failed.

Return ONLY valid JSON:
{{"tool": "navigate", "args": {{"url": "https://example.com"}}, "done": false, "reason": "why this action"}}"""

        try:
            result = llm_provider.generate_chat(
                [{"role": "user", "content": prompt}], json_mode=True, task="planning",
            )
            decision = json.loads(result["answer"])
            if not isinstance(decision, dict):
                raise ValueError("decision is not an object")
            return {
                "tool": decision.get("tool", ""),
                "args": decision.get("args", {}) if isinstance(decision.get("args"), dict) else {},
                "done": bool(decision.get("done", False)),
                "reason": str(decision.get("reason", "")),
            }
        except Exception as err:
            log.info("web_action_planning_failed", extra={"error": str(err)})
            return {"tool": "", "args": {}, "done": True, "reason": f"Could not plan a next action: {err}"}

    # ── Executing one action ─────────────────────────────────────────────

    def execute_action(
        self,
        alias: str,
        args: dict[str, Any],
        goal: str,
        index: int,
        confirm_fn: Callable[[str], bool],
    ) -> Step:
        """Validate, gate, confirm, and run one browser action."""
        qualified_name = TOOL_ALIASES.get(alias)
        if qualified_name is None or qualified_name not in BROWSER_TOOL_ALLOWLIST:
            log.info("web_tool_not_allowlisted", extra={"alias": alias})
            return Step(index=index, tool=alias, args=args, status="rejected",
                        reason=f"'{alias}' is not an allowed browser action.")

        url = args.get("url", "")
        if not is_safe_url(url):
            log.info("web_unsafe_url_rejected", extra={"url": str(url)[:200]})
            return Step(index=index, tool=alias, args=args, status="rejected",
                        reason=f"Refused unsafe or non-http(s) URL: {url!r}")

        registry = mcp_client.get_tool_registry()
        tool_spec = registry.get(qualified_name)
        if tool_spec is None:
            return Step(index=index, tool=alias, args=args, status="error",
                        reason="Browser tools are not available (Playwright MCP server not running).")

        decision = gate(qualified_name, args, user_input=goal,
                        tool_description=tool_spec.description)

        if decision["action"] == "blocked":
            log.info("web_action_blocked", extra={"tool": qualified_name})
            return Step(index=index, tool=alias, args=args, status="blocked",
                        reason=decision.get("message", "Blocked by the tier gate."))

        if decision["action"] == "confirm":
            approved = confirm_fn(decision.get("message", f"Run {alias} on {url}?"))
            if not approved:
                log.info("web_action_confirmation_denied", extra={"tool": qualified_name})
                return Step(index=index, tool=alias, args=args, status="denied",
                            reason="You declined this browser action.")
            log.info("web_action_confirmation_granted", extra={"tool": qualified_name})

        result = mcp_client.call_mcp_tool(qualified_name, args)
        if result.get("error"):
            log.info("web_action_error", extra={"tool": qualified_name, "error": str(result["error"])})
            return Step(index=index, tool=alias, args=args, status="error",
                        reason=str(result["error"]))

        observation = _truncate(str(result.get("result", "")))
        log.info("web_action_executed", extra={"tool": qualified_name, "chars": len(observation)})
        return Step(index=index, tool=alias, args=args, status="ok", observation=observation)

    # ── Full loop ────────────────────────────────────────────────────────

    def browse(
        self,
        goal: str,
        start_url: str | None = None,
        confirm_fn: Callable[[str], bool] | None = None,
    ) -> BrowseReport:
        """Run the bounded observe → act loop toward `goal`."""
        goal = (goal or "").strip()
        if not goal:
            return BrowseReport(goal=goal, summary="I need an actual browsing goal.", completed=False)

        confirm = confirm_fn or deny_all
        steps: list[Step] = []
        notes: list[str] = []

        log.info("web_browse_started", extra={"goal": goal[:200], "start_url": start_url or ""})

        for index in range(1, self.max_steps + 1):
            decision = self.decide_next_action(goal, steps, start_url=start_url)

            if decision["done"]:
                if decision.get("reason"):
                    notes.append(decision["reason"])
                break

            step = self.execute_action(
                decision["tool"], decision["args"], goal, index, confirm,
            )
            steps.append(step)

            # A denied or blocked action means the user (or the gate) said no —
            # stop rather than trying to route around the refusal.
            if step.status in ("denied", "blocked"):
                notes.append(step.reason)
                break
        else:
            notes.append(f"Reached the {self.max_steps}-step limit.")

        summary = self.summarize(goal, steps)
        completed = any(s.status == "ok" for s in steps)

        log.info("web_browse_finished", extra={
            "goal": goal[:200], "steps": len(steps), "completed": completed,
        })
        return BrowseReport(goal=goal, summary=summary, steps=steps,
                            completed=completed, notes=notes)

    def summarize(self, goal: str, steps: list[Step]) -> str:
        """Turn the observations into a plain answer to the original goal."""
        successful = [s for s in steps if s.status == "ok" and s.observation]
        if not successful:
            return (
                "I wasn't able to complete that in the browser — no action produced "
                "a usable result. Nothing was submitted or changed."
            )

        observations = "\n\n".join(
            f"[Step {s.index}: {s.tool} on {s.args.get('url', '')}]\n{s.observation}"
            for s in successful
        )

        prompt = f"""Answer the user's browsing goal using ONLY what the browser actually returned.

Goal: {goal}

Browser observations:
{observations}

Rules:
- Use only the observations above. Do not add outside knowledge.
- If the observations do not fully satisfy the goal, say what is missing.
- Be concise and direct."""

        try:
            result = llm_provider.generate_chat(
                [{"role": "user", "content": prompt}], task="research",
            )
            answer = (result.get("answer") or "").strip()
            if answer:
                return answer
        except Exception as err:
            log.info("web_summary_failed", extra={"error": str(err)})

        return "Here is what the browser returned:\n\n" + observations[:2000]


def format_browse_report(report: BrowseReport) -> str:
    """Render a browsing report for the terminal."""
    lines = [report.summary]
    if report.steps:
        lines.append("\nActions taken:")
        for step in report.steps:
            marker = {"ok": "✅", "denied": "🚫", "blocked": "⛔", "error": "❌", "rejected": "⚠️"}.get(step.status, "•")
            target = step.args.get("url", "")
            detail = f" — {step.reason}" if step.reason else ""
            lines.append(f"  {marker} {step.index}. {step.tool} {target}{detail}")
    if report.notes:
        lines.append("\n" + "\n".join(f"Note: {n}" for n in report.notes))
    return "\n".join(lines)
