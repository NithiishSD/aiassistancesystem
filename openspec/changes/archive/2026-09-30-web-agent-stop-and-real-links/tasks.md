# Tasks

## 1. Stop on goal satisfaction

- [x] 1.1 In `WebAgent.browse`, end the loop after an ok step whose extraction has `goal_satisfied=True`; verify with tests in `tests/test_web_quarantine.py` that the planner is called once and one step is taken, and that an unsatisfied step continues

## 2. Real links only

- [x] 2.1 `mcp_playwright_server`: navigate and get_text (body) append a marked list of the page's absolute http(s) links, de-duplicated and capped; verify by a unit test of the formatting helper
- [x] 2.2 `web_agent`: split the link list from the tool result before truncation and keep it on the step; the extractor prompt lists the user-domain links and asks for `next_url` from that list or null; verify by tests that links are parsed, that off-domain links are not shown, and that the list is absent from the planner prompt
- [x] 2.3 `_admit` drops a `next_url` that is not among the page's links and logs `not_a_page_link`; verify tests for invented link, real link, and no link list

## 3. Integrate

- [x] 3.1 Live check against a local test page served on 127.0.0.1: verify the link list is returned and that a one-page goal finishes in one step
- [x] 3.2 Tick the two B1 follow-ups in `ROADMAP.md`; run `./zedek-env/bin/python -m pytest -q` with 0 failures and report the count before and after
