"""Promoting corrected misroutes into the golden set (ROADMAP A4). Temp files only."""

import csv
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evals"))

import promote_misroutes as pm  # noqa: E402

GOLDEN = [{"utterance": "find my resume", "intent": "search_files", "split": "test"}]
CLASSES = ["search_files", "open_application", "general_question"]


def _entry(utterance, intended, routed_to="general_question"):
    return {"ts": "2026-09-30T10:00:00+00:00", "utterance": utterance, "routed_to": routed_to,
            "intended": intended, "via_llm": False}


def test_reads_the_log_and_skips_broken_lines(tmp_path):
    path = tmp_path / "misroutes.jsonl"
    path.write_text(json.dumps(_entry("open the editor", "open_application")) + "\nnot json\n[1]\n")
    assert [e["utterance"] for e in pm.read_misroutes(str(path))] == ["open the editor"]
    assert pm.read_misroutes(str(tmp_path / "missing.jsonl")) == []


def test_candidates_are_new_known_and_go_to_dev():
    entries = [
        _entry("Open  the Editor", "open_application"),
        _entry("open the editor", "open_application"),        # repeat
        _entry("find my resume", "search_files"),             # already golden (a TEST row stays untouched)
        _entry("launch it", "open_application"),              # already a router phrase
        _entry("do the thing", None),                         # the owner never said what was meant
        _entry("paint the wall", "not_an_intent"),
        _entry("", "search_files"),
    ]
    rows = pm.candidates(entries, GOLDEN, CLASSES, {"Launch it"})
    assert rows == [{"utterance": "open the editor", "intent": "open_application", "split": "dev",
                     "routed_to": "general_question"}]


def test_append_keeps_the_file_format(tmp_path):
    path = tmp_path / "golden.csv"
    path.write_bytes(b"utterance,intent,split\r\nfind my resume,search_files,test\r\n")
    pm.append_rows([{"utterance": "open the editor, please", "intent": "open_application", "split": "dev"}], str(path))
    assert path.read_bytes().endswith(b'"open the editor, please",open_application,dev\r\n')
    with open(path, newline="", encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 2


def test_listing_changes_nothing_and_apply_honours_skip(tmp_path, monkeypatch, capsys):
    log = tmp_path / "misroutes.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in [
        _entry("zzz open the qqq editor", "open_application"), _entry("zzz where is my qqq file", "search_files")]))
    golden = tmp_path / "golden.csv"
    golden.write_bytes(b"utterance,intent,split\r\nfind my resume,search_files,test\r\n")
    monkeypatch.setenv("ZEDEK_MISROUTES_PATH", str(log))
    monkeypatch.setattr(pm.rev, "GOLDEN_PATH", str(golden))
    monkeypatch.setattr(pm.rev, "load_golden", lambda: GOLDEN)
    before = golden.read_bytes()

    assert pm.main([]) == 0
    assert golden.read_bytes() == before and "public" in capsys.readouterr().out

    assert pm.main(["--apply", "--skip", "1"]) == 0
    added = golden.read_bytes()[len(before):]
    assert added == b"zzz where is my qqq file,search_files,dev\r\n"
    assert "--write-baseline" in capsys.readouterr().out
