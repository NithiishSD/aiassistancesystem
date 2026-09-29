"""Shared test fixtures.

Tests must never write the owner's real data files. The provider usage counter
(llm_provider, data/provider_usage.json) resolves its path from
ZEDEK_PROVIDER_USAGE_PATH at call time, so point it at a temp file for the
whole session.
"""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_provider_usage_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("provider_usage") / "provider_usage.json"
    previous = os.environ.get("ZEDEK_PROVIDER_USAGE_PATH")
    os.environ["ZEDEK_PROVIDER_USAGE_PATH"] = str(path)
    yield path
    if previous is None:
        os.environ.pop("ZEDEK_PROVIDER_USAGE_PATH", None)
    else:
        os.environ["ZEDEK_PROVIDER_USAGE_PATH"] = previous
