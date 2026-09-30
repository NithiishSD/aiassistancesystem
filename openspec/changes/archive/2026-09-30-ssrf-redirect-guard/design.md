# Design

Redirect detection by status code (301/302/303/307/308) with a string `Location`; relative locations resolved with `urljoin`. A blocked hop returns `"[blocked] …"` exactly like a blocked first URL, so callers need no new handling.
