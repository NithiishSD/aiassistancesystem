# Design

### D1. Decision point
`_execute_in_sandbox`: bubblewrap result unless it is `unavailable`; then, if `self.allow_unisolated`, the rlimit path (logged); else an `unavailable` result with backend `"none"` and a stderr message. `allow_unisolated` defaults to `ZEDEK_SANDBOX_ALLOW_UNISOLATED` in (`1`, `true`, `yes`).

### D2. Visibility
`sandbox_backend {backend: bubblewrap|rlimit_process|none}` logged once in `__init__`.
