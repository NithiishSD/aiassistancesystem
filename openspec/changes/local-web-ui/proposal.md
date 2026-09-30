# Proposal

Implements ROADMAP D4 (packaging: a UI and a `systemd --user` unit).

## Why

Zedek only runs inside the terminal that started it. It stops when the shell closes and offers
nothing but a text prompt. The confirmation channel (D5) now makes a second front end possible
without touching the gated flows.

## What Changes

- New `web_ui.py`: a single-page browser UI served from this machine only. A message goes
  through `orchestrator.handle()` exactly as a typed one does; the answer streams into the page;
  anything needing approval appears in the page with Approve / Decline and an optional reason,
  through a `WebChannel` for `confirmation.py`. The REPL commands (`help`, `digest`, `inbox`) and
  unread scheduled results work there too.
- Access control in code: binds `127.0.0.1`; every API call needs a per-start token carried in
  the URL fragment (never sent to the server or logged); the `Host` header must be this server
  (DNS rebinding); a cross-site `Origin` is refused; strict response headers; the page writes
  text with `textContent` only.
- One turn at a time. A confirmation nobody answers within 3 minutes is a refusal.
- `deploy/zedek-ui.service`: a `systemd --user` unit with `Restart=on-failure`. The address with
  its token is written to `$XDG_RUNTIME_DIR/zedek-ui.url` (mode 600). Installing it is manual.

## Non-goals

- **Not Chainlit.** ROADMAP named it, but FastAPI and uvicorn are already installed for the MCP
  servers, and the page needs one screen. Chainlit would add a large dependency tree and its own
  server for the same result; the project rule is to prefer what is installed.
- No remote access, no accounts, no TLS: it is for the person at this machine.
- No persistence of the conversation in the browser; no file upload; no voice in the page.
- The terminal REPL is unchanged and stays the default.

## Capabilities

### New Capabilities
- `web-ui`: the local browser front end, who may reach it, and how approvals work in it.

### Modified Capabilities

## Impact

- Code: new `web_ui.py`, `deploy/zedek-ui.service`, tests. `requirements.txt` pins `fastapi` and
  `uvicorn` at the installed versions, since they are now used directly.
- Tier gate: unchanged. Every action still passes `gate()`; approval is asked in the page.
- Watchdog: none.
- Untrusted-input path: a new inbound surface, limited to the local machine and guarded as
  above. Model output and web content shown in the page are inserted as text, not markup.
