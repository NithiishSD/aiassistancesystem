# Design

### D1. One predicate
`is_secret_file(name)` matches a base name against the list in the proposal. Mirror and overlay both use it, so they cannot disagree.

### D2. Mirror
`_mirror_project` passes an ignore callable to `copytree` that drops heavy dirs (as today) plus secret files; top-level files are checked the same way.

### D3. Read-only overlay
`_secret_files(root)` walks the project (skipping the heavy dirs the mirror skips) and returns secret paths; each gets an empty regular temp file bound read-only over it after the project bind, so the later mount wins. (`/dev/null` fails: the tmpfs holding the project is `nodev`.)

### D4. extra_env
`_build_clean_env` drops a key if its upper-cased name ends in `_API_KEY`, `_KEY`, `_TOKEN` or `_SECRET`, contains `PASSWORD`, or if its value equals a non-empty value of such a variable in `os.environ`. Logged as `sandbox_env_secret_dropped {name}`.

### D5. Bubblewrap path repairs
- `_BWRAP_SYSTEM_BINDS` shared by `_is_bwrap_functional` and `_execute_bwrap`.
- `_process_limit()` (parent side): sum of `Threads:` over `/proc/*/status` for the user's uid, + 64; flat 64 if `/proc` is unreadable.
- Child environment = `_build_clean_env(...)` (+ project `PYTHONPATH` in read-only mode) via `--setenv`; bwrap itself gets only `PATH`.
