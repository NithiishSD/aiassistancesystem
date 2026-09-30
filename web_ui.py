"""
A local browser UI for Zedek (ROADMAP D4).

One page, served from this machine only. The orchestrator stays the product:
a message goes through orchestrator.handle() exactly as a typed one does, the
answer streams into the page, and anything that needs approval is asked in
the page through the confirmation channel (confirmation.py).

Safety properties, all enforced here rather than left to the browser:
  - Binds 127.0.0.1 only.
  - Every API call needs the session token, generated at start-up and handed
    over in the URL fragment (never sent to the server, never logged).
  - The Host header must be this server's own address, which defeats DNS
    rebinding; a cross-site Origin is refused.
    (OpenClaw's localhost gateway had neither check: CVE-2026-25253.)
  - One turn at a time. A confirmation nobody answers is refused.
  - The page writes all text with textContent, so an answer containing markup
    is shown, not run.

Built on FastAPI and uvicorn, which are already installed for the MCP servers.
Run: python web_ui.py [--port 8765]
"""

from __future__ import annotations

import hmac
import os
import secrets
import threading
import uuid
from typing import Any, Callable

import confirmation
from zedek_logger import get_logger

log = get_logger("web_ui")

DEFAULT_PORT = 8765
CONFIRM_TIMEOUT_SECONDS = 180
MAX_MESSAGE_CHARS = 4000
MAX_EVENTS = 2000
MAX_POLL_SECONDS = 25


class _PageStream:
    """llm_provider.StreamSink that forwards the reply to the page."""

    def __init__(self, session: "UiSession") -> None:
        self._session = session
        self.shown = ""

    def delta(self, text: str) -> None:
        self.shown += text
        self._session.publish("delta", text=text)

    def restart(self) -> None:
        self.shown = ""
        self._session.publish("restart")


class WebChannel:
    """Confirmation channel that asks in the page and waits for the answer."""

    def __init__(self, session: "UiSession", timeout: float = CONFIRM_TIMEOUT_SECONDS) -> None:
        self._session = session
        self._timeout = timeout

    def ask(self, message: str) -> confirmation.Answer:
        request_id = uuid.uuid4().hex
        waiter = {"event": threading.Event(), "reply": None}
        self._session.pending[request_id] = waiter
        self._session.publish("confirm", id=request_id, message=message)
        answered = waiter["event"].wait(self._timeout)
        self._session.pending.pop(request_id, None)
        if not answered:
            log.info("web_confirmation_timed_out", extra={})
            self._session.publish("confirm_closed", id=request_id, outcome="No answer in time, so it was not done.")
            return confirmation.Answer(False)
        return confirmation.parse_reply(waiter["reply"])

    def notify(self, message: str) -> None:
        self._session.publish("notice", text=message)


class UiSession:
    """The conversation state behind the page: an event log and one running turn."""

    def __init__(self, turn_runner: Callable[..., str] | None = None,
                 command_runner: Callable[[str], str | None] | None = None,
                 confirm_timeout: float = CONFIRM_TIMEOUT_SECONDS) -> None:
        self._turn_runner = turn_runner
        self._command_runner = command_runner
        self._confirm_timeout = confirm_timeout
        self._events: list[dict[str, Any]] = []
        self._first_index = 0
        self._changed = threading.Condition()
        self._busy = threading.Lock()
        self.pending: dict[str, dict] = {}

    # ── Event log ────────────────────────────────────────────────────────

    def publish(self, kind: str, **data: Any) -> None:
        with self._changed:
            self._events.append({"type": kind, **data})
            overflow = len(self._events) - MAX_EVENTS
            if overflow > 0:
                del self._events[:overflow]
                self._first_index += overflow
            self._changed.notify_all()

    def events_since(self, since: int, wait: float = 0.0) -> tuple[list[dict[str, Any]], int]:
        """Events after position `since`, waiting up to `wait` seconds for one."""
        with self._changed:
            end = self._first_index + len(self._events)
            if since >= end and wait > 0:
                self._changed.wait(min(wait, MAX_POLL_SECONDS))
                end = self._first_index + len(self._events)
            start = max(since, self._first_index) - self._first_index
            return list(self._events[start:]), end

    # ── Turns ────────────────────────────────────────────────────────────

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    def submit(self, text: str) -> bool:
        """Start a turn. False if one is already running."""
        if not self._busy.acquire(blocking=False):
            return False
        self.publish("user", text=text)
        threading.Thread(target=self._run_turn, args=(text,), name="zedek-ui-turn", daemon=True).start()
        return True

    def _run_turn(self, text: str) -> None:
        try:
            runner, commands = self._turn_runner, self._command_runner
            if runner is None or commands is None:
                import orchestrator
                runner = runner or orchestrator.handle
                commands = commands or orchestrator._repl_command
            reply = commands(text)
            if reply is None:
                sink = _PageStream(self)
                with confirmation.use_channel(WebChannel(self, self._confirm_timeout)):
                    reply = runner(text, stream=sink)
            self.publish("answer", text=reply or "")
        except Exception as error:  # the page must always get an end-of-turn event
            log.info("web_turn_failed", extra={"error_type": type(error).__name__, "error": str(error)[:200]})
            self.publish("answer", text=f"Something went wrong handling that: {error}")
        finally:
            self._busy.release()

    def answer_confirmation(self, request_id: str, reply: str) -> bool:
        waiter = self.pending.get(request_id)
        if waiter is None:
            return False
        waiter["reply"] = reply
        waiter["event"].set()
        self.publish("confirm_closed", id=request_id,
                     outcome="Approved." if confirmation.parse_reply(reply) else "Not approved.")
        return True


# ── HTTP ─────────────────────────────────────────────────────────────────────

def allowed_hosts(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


def request_allowed(host: str | None, origin: str | None, port: int) -> bool:
    """The Host must be this server, and a browser-sent Origin must be too."""
    if (host or "").lower() not in allowed_hosts(port):
        return False
    if origin is not None and origin.lower() not in {f"http://{h}" for h in allowed_hosts(port)}:
        return False
    return True


def create_app(session: UiSession, token: str, port: int = DEFAULT_PORT):
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse

    app = FastAPI(title="Zedek", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not request_allowed(request.headers.get("host"), request.headers.get("origin"), port):
            log.info("web_request_refused", extra={"reason": "host_or_origin"})
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("x-zedek-token", "")
            if not hmac.compare_digest(supplied.encode(), token.encode()):
                log.info("web_request_refused", extra={"reason": "token"})
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        return response

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE

    @app.get("/api/events")
    def events(since: int = 0, wait: float = 0.0) -> dict:
        found, position = session.events_since(max(0, since), max(0.0, wait))
        return {"events": found, "next": position, "busy": session.busy}

    @app.post("/api/message")
    def message(body: dict) -> Any:
        text = str(body.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "empty message"}, status_code=400)
        if len(text) > MAX_MESSAGE_CHARS:
            return JSONResponse({"error": f"message longer than {MAX_MESSAGE_CHARS} characters"}, status_code=413)
        if not session.submit(text):
            return JSONResponse({"error": "Zedek is still working on the previous message."}, status_code=409)
        return {"ok": True}

    @app.post("/api/confirm")
    def confirm(body: dict) -> Any:
        if not session.answer_confirmation(str(body.get("id") or ""), str(body.get("reply") or "")[:400]):
            return JSONResponse({"error": "That request is no longer waiting for an answer."}, status_code=404)
        return {"ok": True}

    return app


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zedek</title>
<style>
:root{--bg:#f6f6f3;--panel:#fff;--ink:#1c1c1a;--soft:#6b6b66;--line:#deded8;--me:#e7eefc;--warn:#fff4d6;--accent:#2f5fd0}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--panel:#20201e;--ink:#ecece8;--soft:#9c9c95;--line:#33332f;--me:#22304f;--warn:#3d3415;--accent:#8fb0ff}}
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif;display:flex;flex-direction:column}
header{padding:12px 16px;border-bottom:1px solid var(--line);font-weight:600}
header span{font-weight:400;color:var(--soft);font-size:14px;margin-left:8px}
#log{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;max-width:860px;width:100%;margin:0 auto}
.msg{padding:10px 14px;border-radius:12px;white-space:pre-wrap;overflow-wrap:anywhere;max-width:100%}
.user{background:var(--me);align-self:flex-end}
.zedek{background:var(--panel);border:1px solid var(--line);align-self:flex-start}
.notice{color:var(--soft);font-size:14px;align-self:center;text-align:center}
.confirm{background:var(--warn);border:1px solid var(--line);align-self:stretch}
.confirm .row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.confirm input{flex:1;min-width:160px}
form{display:flex;gap:8px;padding:12px 16px;border-top:1px solid var(--line);max-width:860px;width:100%;margin:0 auto}
input,button{font:inherit;color:inherit;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
#text{flex:1;min-width:0}
button{cursor:pointer}button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
button:disabled{opacity:.5;cursor:default}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
</style></head><body>
<header>Zedek<span id="status">connecting…</span></header>
<div id="log" aria-live="polite"></div>
<form id="form"><input id="text" autocomplete="off" placeholder="Ask or tell Zedek something" aria-label="Message" maxlength="4000">
<button class="primary" id="send">Send</button></form>
<script>
const token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
const log = document.getElementById('log'), statusEl = document.getElementById('status');
const form = document.getElementById('form'), text = document.getElementById('text'), send = document.getElementById('send');
let next = 0, live = null;
function add(cls, body){const d=document.createElement('div');d.className='msg '+cls;d.textContent=body;log.appendChild(d);log.scrollTop=log.scrollHeight;return d}
async function api(path, options){const r=await fetch(path,Object.assign({headers:{'X-Zedek-Token':token,'Content-Type':'application/json'}},options||{}));
  const data=await r.json().catch(()=>({}));if(!r.ok)throw new Error(data.error||('HTTP '+r.status));return data}
function setBusy(b){send.disabled=b;statusEl.textContent=b?'working…':'ready'}
function confirmBox(ev){const box=add('confirm',ev.message);box.dataset.id=ev.id;const row=document.createElement('div');row.className='row';
  const why=document.createElement('input');why.placeholder='Optional: what to change';why.setAttribute('aria-label','Reason for declining');
  const yes=document.createElement('button');yes.textContent='Approve';yes.className='primary';
  const no=document.createElement('button');no.textContent='Decline';
  const reply=r=>api('/api/confirm',{method:'POST',body:JSON.stringify({id:ev.id,reply:r})}).catch(e=>add('notice',e.message));
  yes.onclick=()=>reply('y');no.onclick=()=>reply('no '+why.value);
  row.append(why,no,yes);box.appendChild(row);no.focus()}
function handle(ev){
  if(ev.type==='user'){add('user',ev.text);live=null}
  else if(ev.type==='delta'){if(!live)live=add('zedek','');live.textContent+=ev.text;log.scrollTop=log.scrollHeight}
  else if(ev.type==='restart'){if(live){live.remove();live=null}add('notice','Connection dropped, retrying with another provider…')}
  else if(ev.type==='answer'){const same=live&&live.textContent.replace(/\\s+/g,' ').trim()===ev.text.replace(/\\s+/g,' ').trim();
    if(!same){if(live)live.remove();add('zedek',ev.text)}live=null}
  else if(ev.type==='notice'){add('notice',ev.text)}
  else if(ev.type==='confirm'){confirmBox(ev)}
  else if(ev.type==='confirm_closed'){const box=log.querySelector('.confirm[data-id="'+ev.id+'"]');
    if(box){const row=box.querySelector('.row');if(row)row.remove();const o=document.createElement('div');o.textContent=ev.outcome;box.appendChild(o)}}
}
async function poll(){for(;;){try{const d=await api('/api/events?since='+next+'&wait=25');d.events.forEach(handle);next=d.next;setBusy(d.busy)}
  catch(e){statusEl.textContent=e.message==='unauthorized'?'open the link Zedek printed (it carries the access token)':'disconnected, retrying…';
    await new Promise(r=>setTimeout(r,2000))}}}
form.onsubmit=async e=>{e.preventDefault();const t=text.value.trim();if(!t)return;
  try{await api('/api/message',{method:'POST',body:JSON.stringify({text:t})});text.value='';setBusy(true)}catch(err){add('notice',err.message)}};
poll();text.focus();
</script></body></html>
"""


def _write_url_file(url: str) -> str | None:
    """Leave the address where the owner can find it when run as a service."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir or not os.path.isdir(runtime_dir):
        return None
    path = os.path.join(runtime_dir, "zedek-ui.url")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(url + "\n")
    return path


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Zedek browser UI (this machine only)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    import orchestrator

    token = secrets.token_urlsafe(32)
    session = UiSession()
    notices = orchestrator._unread_notices()
    if notices:
        session.publish("notice", text=notices)

    url = f"http://127.0.0.1:{args.port}/#token={token}"
    url_file = _write_url_file(url)
    print(f"Zedek UI: {url}")
    if url_file:
        print(f"(also written to {url_file})")
    try:
        uvicorn.run(create_app(session, token, args.port), host="127.0.0.1", port=args.port,
                    log_level="warning", access_log=False)
    finally:
        orchestrator.summarize_and_flush_session()


if __name__ == "__main__":
    main()
