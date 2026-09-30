# Proposal

Implements **ROADMAP B3** (keep API keys out of the sandbox).

## Why

The sandbox already starts children with a clean environment (`--clearenv` under bubblewrap, an explicit mapping in the rlimit fallback), so provider keys in the parent's environment do not reach sandboxed code. They still reach it through **files**:
- `run_test_suite` mirrors the whole project into its copy-on-write workspace, `.env` included.
- `PROJECT_READ_ONLY` bind-mounts the project root, `.env` included.
- `extra_env` passes any caller-supplied variable, so a key could be forwarded by mistake.

Generated or LLM-written code runs in these modes, so one `open(".env").read()` in a test would expose every key.

## What Changes

- `sandbox_runner.is_secret_file(name)`: `.env` and `.env.*` (except `.example`/`.sample`/`.template`), `*.pem`, `*.key`, `id_rsa*`, `id_ecdsa*`, `id_ed25519*`, `.netrc`, `.pypirc`, `.npmrc`, `credentials*.json`.
- The copy-on-write mirror skips secret files at every depth.
- Under bubblewrap, `PROJECT_READ_ONLY` overlays each secret file in the project with `/dev/null`.
- `extra_env` drops names that look like secrets (`*_API_KEY`, `*_KEY`, `*_TOKEN`, `*_SECRET`, `*PASSWORD*`, and any name currently holding a secret in the parent's environment); each drop is logged by name only.

### Found while testing: bubblewrap was never used
The availability probe ran `/usr/bin/true` without binding `/lib`, so the dynamic loader was missing, the probe always failed, and **every sandbox run silently used the weaker rlimit fallback**. Fixing the probe exposed three more bugs on the never-exercised bubblewrap path, all fixed here because without them the read-only mask never runs:
- `RLIMIT_NPROC=64` counts *all* of the user's threads (≈1,800 on a desktop session), so bubblewrap could not create its namespaces. The limit is now the user's current thread count + 64, which keeps the fork-bomb bound.
- `--clearenv` discarded the clean environment the runner builds, so `extra_env` never reached the child; it is now passed with `--setenv`.
- Read-only mode did not put the project on `PYTHONPATH` (the rlimit path did).
- Probe and runs share one list of system mounts (`/lib64` as `--ro-bind-try`).

## Capabilities

### New Capabilities
- `sandbox-secrets`: what secret material sandboxed code can and cannot reach.

## Impact

- **Code:** `sandbox_runner.py`.
- **Behavior:** sandboxed code now actually runs under bubblewrap namespaces on this machine (no network, private /tmp, read-only system), as the design always intended.
- **Tier gate:** unchanged. This narrows what already-approved sandbox runs can read.
- **Known limit (documented, unchanged):** the rlimit fallback, used only when bubblewrap is unavailable, has no filesystem isolation; code there can read any file the user can, by absolute path. It is not a secret boundary.

## Non-goals

- B4 (egress allowlist for network-enabled runs).
- Making the rlimit fallback a filesystem sandbox.
