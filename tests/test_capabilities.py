"""One declarative capability registry (OpenSpec change: capability-registry, ROADMAP E1)."""

import inspect
import os
import shutil

import pytest
import yaml

import capabilities
import classifier
import classifier_tools
import tier_gate
from system_agent import AVAILABLE_FUNCTIONS


# ── Safety pin: any tier change must edit this test, so it gets reviewed ─────

def test_tier_table_is_pinned():
    assert tier_gate.FUNCTION_TIERS == {
        "search_files": 0, "disk_usage_by_folder": 0, "top_memory_processes": 0,
        "free_space_summary": 0, "directory_size": 0, "list_processes_detailed": 0,
        "open_application": 1, "coding_task": 0,
    }


def test_unknown_function_still_fails_safe():
    assert tier_gate.classify("not_a_capability", {}) == 3


# ── Every table comes from the registry ───────────────────────────────────────

def test_derived_tables_agree():
    names = set(capabilities.ROUTE_ORDER)
    assert list(classifier.INTENT_UTTERANCES) == list(capabilities.ROUTE_ORDER)
    assert classifier_tools.VALID_INTENT_NAMES == names | {capabilities.DEFAULT_INTENT}
    assert [t["name"] for t in classifier_tools.ROUTER_TOOLS] == list(capabilities.LLM_TOOL_ORDER)
    assert set(classifier.STRICT_INTENT_THRESHOLDS) <= names


def test_every_native_function_is_a_capability_with_a_tier():
    for name in AVAILABLE_FUNCTIONS:
        assert name in capabilities.CAPABILITIES, name
        assert capabilities.CAPABILITIES[name].tier is not None, f"{name} would be blocked at Tier 3"


def test_arg_types_name_real_parameters():
    for name, args in capabilities.int_args().items():
        params = inspect.signature(AVAILABLE_FUNCTIONS[name]).parameters
        assert set(args) <= set(params), name


def test_derived_tables_are_independent_copies():
    table = capabilities.intent_utterances()
    table["search_files"].append("mutated")
    assert "mutated" not in capabilities.intent_utterances()["search_files"]


# ── Loading fails closed ─────────────────────────────────────────────────────

@pytest.fixture
def registry(tmp_path):
    for f in os.listdir(capabilities.DIR):
        if f.endswith(".yaml"):
            shutil.copy(os.path.join(capabilities.DIR, f), tmp_path / f)
    return tmp_path


def _edit(path, **changes):
    doc = yaml.safe_load(path.read_text())
    doc.update(changes)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def test_copy_loads(registry):
    loaded, routes, _ = capabilities._load(str(registry))
    assert set(loaded) == set(capabilities.CAPABILITIES) and routes == capabilities.ROUTE_ORDER


@pytest.mark.parametrize("changes", [
    {"tier": 4}, {"tier": -1}, {"tier": True}, {"tier": "0"}, {"threshold": 1.5}, {"threshold": 0},
    {"surprise": 1}, {"name": "other"}, {"description": ""}, {"utterances": []},
    {"arg_types": {"top_n": "float"}},
])
def test_invalid_capability_refused(registry, changes):
    _edit(registry / "open_application.yaml", **changes)
    with pytest.raises(capabilities.CapabilityError):
        capabilities._load(str(registry))


def test_missing_file_refused(registry):
    (registry / "search_files.yaml").unlink()
    with pytest.raises(capabilities.CapabilityError, match="missing"):
        capabilities._load(str(registry))


def test_unlisted_file_refused(registry):
    shutil.copy(registry / "search_files.yaml", registry / "extra_tool.yaml")
    with pytest.raises(capabilities.CapabilityError, match="unlisted"):
        capabilities._load(str(registry))


def test_default_intent_cannot_be_a_route(registry):
    index = yaml.safe_load((registry / "index.yaml").read_text())
    index["routes"].append(capabilities.DEFAULT_INTENT)
    (registry / "index.yaml").write_text(yaml.safe_dump(index))
    with pytest.raises(capabilities.CapabilityError):
        capabilities._load(str(registry))
