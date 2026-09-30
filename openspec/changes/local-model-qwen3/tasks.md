# Tasks

- [x] 1.1 Bump `ollama` to 0.6.3; `local_chat` with `think=False`; `ZEDEK_LOCAL_MODEL`; route `_local`, `_local_stream` and the orchestrator's direct calls through it (D2, D3). Tests: think=False on every path, env override, ROUTING_MODEL follows LOCAL_MODEL.
- [x] 1.2 `evals/local_model_eval.py` (D1): intent accuracy on escalated dev rows with cloud off, structured-output validity, median latency, per model.
- [x] 1.3 Run it for `llama3.1:8b` and `qwen3:8b`; apply the D1 gate to choose the default.
- [x] 1.4 Full suite and routing gate, 0 failures; report the count before (962) and after. Update ROADMAP D3 with the numbers.
