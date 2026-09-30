"""
The confirmation channel (ROADMAP D5).

Every request for the user's approval goes through ask(), and every notice
through notify(). The terminal is the default channel; a UI, a voice turn or
a scheduled job swaps in its own with use_channel() instead of the callers
reading input() themselves.

Rules that do not depend on the channel:
  - Only "y" or "yes" approves. Anything else, including silence, refuses.
  - A refusal may carry the user's reason ("no, use the other file"), which
    the caller can hand back to an agent so it can propose something else.
  - A channel that cannot ask answers with a refusal. Nothing here can
    approve on the user's behalf.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Protocol

from zedek_logger import get_logger

log = get_logger("confirmation")

_YES = {"y", "yes"}
_LEADING_NO_RE = re.compile(r"^(?:no|n)\b[\s,.;:!-]*", re.IGNORECASE)
MAX_REASON_CHARS = 300


@dataclass(frozen=True)
class Answer:
    """The user's reply to a confirmation request. Truthy only when approved."""

    approved: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.approved


def parse_reply(raw: str | None) -> Answer:
    """Turn what the user typed or said into an Answer."""
    text = " ".join((raw or "").split())
    if text.lower() in _YES:
        return Answer(True)
    return Answer(False, _LEADING_NO_RE.sub("", text)[:MAX_REASON_CHARS].strip())


class Channel(Protocol):
    def ask(self, message: str) -> Answer: ...

    def notify(self, message: str) -> None: ...


class TerminalChannel:
    """Print the request and read one line from the terminal."""

    def ask(self, message: str) -> Answer:
        print(message)
        try:
            return parse_reply(input("> "))
        except (EOFError, KeyboardInterrupt):
            log.info("confirmation_unanswered", extra={"channel": "terminal"})
            return Answer(False)

    def notify(self, message: str) -> None:
        print(message)


class DenyChannel:
    """For runs with nobody to ask (scheduled jobs): every request is refused."""

    def ask(self, message: str) -> Answer:
        log.info("confirmation_denied_no_channel", extra={"request": message[:200]})
        return Answer(False)

    def notify(self, message: str) -> None:
        log.info("confirmation_notice", extra={"notice": message[:200]})


_channel: ContextVar[Channel] = ContextVar("zedek_confirmation_channel", default=TerminalChannel())


def get_channel() -> Channel:
    return _channel.get()


@contextmanager
def use_channel(channel: Channel) -> Iterator[Channel]:
    """Make `channel` the active one for the duration of a task."""
    token = _channel.set(channel)
    try:
        yield channel
    finally:
        _channel.reset(token)


def ask(message: str) -> Answer:
    """Ask the user to approve something. A channel that fails refuses."""
    try:
        answer = get_channel().ask(message)
    except Exception as error:
        log.info("confirmation_channel_failed", extra={"error": str(error)[:200]})
        return Answer(False)
    if not isinstance(answer, Answer):
        answer = Answer(answer is True)
    return answer


def notify(message: str) -> None:
    try:
        get_channel().notify(message)
    except Exception as error:
        log.info("confirmation_channel_failed", extra={"error": str(error)[:200]})
