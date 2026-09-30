# Design

`_is_bare_topic(text, term)`: tokenize on `[a-z0-9+#.'-]+` (so `c++` and `go-kart` stay one token), drop `_TOPIC_FILLER` words and the term; bare if nothing is left. Checked right after a term is detected, before the older correction/context checks. The filler list was built on the dev slice; the test slice was checked once afterwards.
