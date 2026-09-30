# Tasks

## 1. Session and channel

- [x] 1.1 Create `web_ui.py` with `UiSession` (bounded event log, one turn at a time, REPL commands) and `WebChannel` (ask in the page, timeout means refusal); verify with `tests/test_web_ui.py`: streamed then final answer, provider restart, busy refusal, failing turn ends, bounded log, approve, decline with reason, no answer, stale request, channel restored after the turn

## 2. HTTP and page

- [x] 2.1 `create_app` with the Host/Origin guard, token check on `/api/*`, and strict headers; verify tests for missing/wrong token, foreign Host with a valid token, cross-site Origin, own Origin, token absent from the page
- [x] 2.2 The page (long-poll events, send, approve/decline) writes text only with `textContent`; verify by test that the page source has no `innerHTML`
- [x] 2.3 `main()` binds 127.0.0.1, prints the address and writes it to `$XDG_RUNTIME_DIR/zedek-ui.url` with mode 600; add `deploy/zedek-ui.service`; pin `fastapi` and `uvicorn` in `requirements.txt`

## 3. Integrate

- [x] 3.1 Live check on scratch data in headless Chromium: the socket listens on 127.0.0.1 only, a question is answered in the page, a browser task shows a confirmation and declining it cancels, no console errors; look at the screenshot
- [x] 3.2 Update `ROADMAP.md` D4; run `./zedek-env/bin/python -m pytest -q` with 0 failures and report the count before and after
