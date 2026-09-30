# Proposal

Follow-up to **ROADMAP B3** (`sandbox-secret-scrub`).

## Why

When bubblewrap is missing or cannot create namespaces, `SandboxRunner` silently runs code under the rlimit fallback. That fallback has no filesystem isolation (it can read `.env` and every file the user owns) and ignores `allow_network=False`. The previous change found the probe had been broken the whole time, and nothing noticed, because the downgrade was silent. A future AppArmor or package change could repeat that. Project rule: security code fails closed.

## What Changes

- Without working bubblewrap, a sandbox run returns `status="unavailable"` (backend `"none"`) with a message naming the fix (install bubblewrap, or opt in), instead of running the code.
- Opt-in to the unisolated fallback: `ZEDEK_SANDBOX_ALLOW_UNISOLATED=1` (or `SandboxRunner(allow_unisolated=True)`). Every run under it logs `sandbox_unisolated_run`.
- The same applies when bubblewrap passes the probe but then fails to launch (`OSError`).
- The runner logs its isolation backend once at construction (`sandbox_backend`).

Callers already handle `unavailable`: the coding agent reports the result as `unverified`, never `passed`; the evaluator records the status.

## Capabilities

### Modified Capabilities
- `sandbox-secrets`: unisolated execution requires an explicit opt-in.

## Impact

- **Code:** `sandbox_runner.py`.
- **Behavior:** none on this machine (bubblewrap works). On a machine without it, generated code stops running until bubblewrap is installed or the opt-in is set.

## Non-goals

- A second isolation backend (Landlock, containers). Revisit only if Zedek must run where bubblewrap cannot.
