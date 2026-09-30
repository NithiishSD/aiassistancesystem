# Tasks

## 1. Tool selection

- [x] 1.1 Whole-phrase shortcuts limited to the bundled server; no default tool when none is named; verify with new `tests/test_mcp_tool_selection.py` (shortcuts, keywords inside other words, model names no tool, nothing runs)

## 2. Providers

- [x] 2.1 `_note_headroom` after successful OpenAI-compatible replies (plain and streamed); verify with tests for each header case and a chain test that the provider is skipped without a request until the reset
- [x] 2.2 `last_answer_source()` and the `providers` REPL command; verify with tests

## 3. Evaluation and registry

- [x] 3.1 `evals/promote_misroutes.py`; verify with new `tests/test_promote_misroutes.py` (broken log lines, candidate rules, file format, dry run, `--skip`)
- [x] 3.2 Description and total-size limits in the capability loader; verify with tests

## 4. Integrate

- [x] 4.1 One discovery retry in the real-subprocess MCP tests
- [x] 4.2 Bring `ROADMAP.md` up to date; run `./zedek-env/bin/python -m pytest -q` with 0 failures and report the count before and after
