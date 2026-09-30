# Tasks

- [x] 1.1 `_get_checked` (per-hop check, 5-redirect cap) in `fetch_url` and `search_arxiv`; `_ip_blocked` with `is_global`, mapped-address unwrap, fail-closed parsing.
- [x] 1.2 Tests: non-global addresses, redirect to metadata and to a privately-resolving host (never requested), relative redirects followed, loop capped.
- [x] 1.3 Full suite, 0 failures (1113 → 1126).
