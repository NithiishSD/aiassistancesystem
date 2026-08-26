import os
import pytest
from unittest.mock import patch, MagicMock

import system_agent
from orchestrator import _extract_application_name, route_request
from classifier import classify_intent


def test_extract_application_name_variations():
    cases = {
        "open appcenter": "appcenter",
        "its App center": "App center",
        "open app center": "app center",
        "Appcenter": "Appcenter",
        "AppCenter": "AppCenter",
        "App Center": "App Center",
        "open files app": "files",
        "open trash": "trash",
        "can you open Brave application": "Brave",
        "please launch visual studio code": "visual studio code",
        "open the calculator": "calculator",
        "start terminal": "terminal",
        "hey zedek, open google chrome": "google chrome",
        "no, its App Center": "App Center",
    }
    for user_input, expected in cases.items():
        assert _extract_application_name(user_input).lower() == expected.lower(), f"Failed for {user_input!r}"


def test_resolve_application_special_targets():
    # Trash
    res = system_agent._resolve_application("trash")
    assert res is not None
    cmd, name = res
    assert any("trash:///" in str(arg) for arg in cmd)
    assert name.lower() == "trash"

    # Files
    res = system_agent._resolve_application("files")
    assert res is not None
    cmd, name = res
    assert any(fm in cmd[0] for fm in ("nautilus", "nemo", "thunar", "dolphin", "xdg-open", "Files"))

    # App Center / appcenter
    res = system_agent._resolve_application("appcenter")
    assert res is not None
    cmd, name = res
    assert any(store in cmd[0] for store in ("snap-store", "ubuntu-app-center", "gnome-software", "plasma-discover", "App Center"))


def test_open_application_execution(monkeypatch):
    mock_popen = MagicMock()
    monkeypatch.setattr("subprocess.Popen", mock_popen)

    res = system_agent.open_application("appcenter")
    assert res["launched"] is True
    assert mock_popen.called
    assert res["app"]

    res_trash = system_agent.open_application("trash")
    assert res_trash["launched"] is True
    assert "trash" in res_trash["app"].lower()


def test_open_application_not_found():
    res = system_agent.open_application("non_existent_fake_app_12345_xyz")
    assert res["launched"] is False
    assert "not a recognized installed application" in res["reason"]


def test_classify_intent_open_app_queries():
    for query in ["open app center", "open files app", "open trash"]:
        result = classify_intent(query)
        assert result["function"] == "open_application"
