# Tasks

- [x] 1.1 `is_secret_file`, mirror filtering, read-only overlay, `extra_env` filtering (D1–D4). Tests: each spec scenario, nested secret file in the mirror, example files kept, non-secret `extra_env` kept.
- [x] 1.2a Bubblewrap repairs (D5). Tests: probe binds, thread-based process limit, extra_env and HOME reach the bwrap child, no key leaks.
- [x] 1.2 Full suite, 0 failures; report the count before and after. Update ROADMAP B3, including the rlimit known limit.
