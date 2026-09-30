# Tasks

## 1. The channel

- [x] 1.1 Create `confirmation.py` (`Answer`, `parse_reply`, `TerminalChannel`, `DenyChannel`, `ask`, `notify`, `use_channel`); verify with new `tests/test_confirmation.py` covering every `confirmation` spec scenario for approval, refusal, reason, deny-all, closed input and channel restore

## 2. Orchestrator call sites

- [x] 2.1 Route the Tier 2 system action, MCP tool, coding gate, apply-patch and browser confirmations and the two tier notices through the channel; verify `grep -n 'input("> ")' orchestrator.py` finds nothing and a test shows a fake channel receives a Tier 2 request and a refusal cancels it
- [x] 2.2 Send the coding plan as one message; on refusal with a reason, re-plan from request + reason and ask again (at most 2 revisions); verify tests for revised-then-approved, bare refusal, and the revision limit

## 3. Browser reject-with-feedback

- [x] 3.1 `execute_action` records the user's reason on a denied step; the planner's step history shows it; verify by test that the next planner prompt contains the reason
- [x] 3.2 `browse` continues after a denial with a reason, ends on a bare denial, and ends when the same action is proposed again; verify three tests, and that existing denial tests still pass

## 4. Integrate

- [x] 4.1 Update `ROADMAP.md` D5 and B6; run `./zedek-env/bin/python -m pytest -q` with 0 failures and report the count before and after
