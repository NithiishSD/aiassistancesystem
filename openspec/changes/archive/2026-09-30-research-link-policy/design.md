# Design

- `allowed_links(question, sources)` → normalized set; `defang_unsourced_links(text, allowed)` → (text, removed hosts). Both pure functions.
- Normalization: `urlsplit`, lowercase host without `www.`, path without trailing `/`, query kept; trailing `.,;:!?)]}>'"*_\`` is not part of a link and is re-appended after the marker.
- Links inside a user-requested page are *not* allowed: the page itself is the untrusted input this defends against.
