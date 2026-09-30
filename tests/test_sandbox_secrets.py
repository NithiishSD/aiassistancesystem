"""Secret material stays out of the sandbox (OpenSpec change: sandbox-secret-scrub, ROADMAP B3)."""

import os

import pytest

import sandbox_runner as sr
from sandbox_runner import SandboxMode, SandboxRunner

FAKE_KEY = "gsk_test_not_a_real_key_123"


@pytest.fixture
def runner():
    return SandboxRunner(timeout_seconds=10, max_output_bytes=8192)


@pytest.mark.parametrize("name,secret", [
    (".env", True), (".env.local", True), (".ENV", True), ("server.pem", True), ("tls.key", True),
    ("id_rsa", True), ("id_ed25519.pub", True), (".netrc", True), ("credentials.json", True),
    (".env.example", False), (".env.sample", False), ("env.py", False), ("keyboard.py", False),
    ("README.md", False),
])
def test_is_secret_file(name, secret):
    assert sr.is_secret_file(name) is secret


def test_parent_key_not_in_child_environment(runner, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", FAKE_KEY)
    res = runner.run_code("import os\nprint(sorted(os.environ.items()))")
    assert res.status == "passed", res.stderr
    assert "GROQ_API_KEY" not in res.stdout and FAKE_KEY not in res.stdout


def test_forwarded_key_dropped_other_env_kept(runner, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", FAKE_KEY)
    logged = []
    monkeypatch.setattr(sr.log, "info", lambda msg, *a, **k: logged.append((msg, k.get("extra"))))
    env = runner._build_clean_env(
        {"OPENAI_API_KEY": "x", "GITHUB_TOKEN": "y", "DB_PASSWORD": "z",
         "INNOCENT_NAME": FAKE_KEY, "MY_FLAG": "1"}, "/tmp/w")
    assert env["MY_FLAG"] == "1"
    for name in ("OPENAI_API_KEY", "GITHUB_TOKEN", "DB_PASSWORD", "INNOCENT_NAME"):
        assert name not in env
    dropped = {extra["name"] for msg, extra in logged if msg == "sandbox_env_secret_dropped"}
    assert dropped == {"OPENAI_API_KEY", "GITHUB_TOKEN", "DB_PASSWORD", "INNOCENT_NAME"}
    assert FAKE_KEY not in str(logged)


def _project(root):
    with open(os.path.join(root, ".env"), "w") as f:
        f.write(f"GROQ_API_KEY={FAKE_KEY}\n")
    with open(os.path.join(root, ".env.example"), "w") as f:
        f.write("GROQ_API_KEY=\n")
    os.makedirs(os.path.join(root, "config"))
    with open(os.path.join(root, "config", "server.pem"), "w") as f:
        f.write("PRIVATE\n")


def test_mirror_drops_secret_files(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    src.mkdir(), dest.mkdir()
    _project(str(src))
    (src / "app.py").write_text("x = 1\n")
    SandboxRunner()._mirror_project(str(src), str(dest))
    assert (dest / "app.py").exists() and (dest / ".env.example").exists()
    assert not (dest / ".env").exists() and not (dest / "config" / "server.pem").exists()
    assert (dest / "config").is_dir()


def test_test_suite_cannot_read_env(runner, tmp_path):
    _project(str(tmp_path))
    (tmp_path / "test_leak.py").write_text(
        "import os\n"
        "def test_leak():\n"
        "    assert not os.path.exists('.env')\n"
        "    assert os.path.exists('.env.example')\n")
    res = runner.run_test_suite(str(tmp_path), ["-m", "pytest", "-q", "-p", "no:cacheprovider", "test_leak.py"])
    assert res.status == "passed", res.stdout + res.stderr


def test_read_only_project_masks_env(runner, tmp_path):
    if not runner._has_bwrap:
        pytest.skip("bubblewrap unavailable: the rlimit fallback is documented as no secret boundary")
    _project(str(tmp_path))
    code = (f"print(repr(open({str(tmp_path / '.env')!r}).read()))\n"
            f"print(repr(open({str(tmp_path / 'config' / 'server.pem')!r}).read()))\n"
            f"print(open({str(tmp_path / '.env.example')!r}).read().strip())\n")
    res = runner.run_code(code, mode=SandboxMode.PROJECT_READ_ONLY, project_root=str(tmp_path))
    assert res.status == "passed", res.stderr
    assert FAKE_KEY not in res.stdout and "PRIVATE" not in res.stdout
    assert res.stdout.splitlines() == ["''", "''", "GROQ_API_KEY="]


# ── Bubblewrap was never used (found while testing B3) ───────────────────────

def test_probe_binds_what_runs_need(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return type("R", (), {"returncode": 0})()
    monkeypatch.setattr(sr.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(sr.subprocess, "run", fake_run)
    assert sr._is_bwrap_functional()
    joined = " ".join(seen["cmd"])
    assert "--ro-bind /lib /lib" in joined and "--ro-bind-try /lib64 /lib64" in joined


def test_process_limit_counts_existing_threads():
    # RLIMIT_NPROC counts all of the user's threads; a flat 64 fails the first clone.
    import threading
    assert sr._process_limit() > threading.active_count() + sr._SANDBOX_PROCESS_BUDGET - 1


def test_bwrap_child_gets_clean_env_and_extra_env(runner, monkeypatch):
    if not runner._has_bwrap:
        pytest.skip("bubblewrap unavailable")
    monkeypatch.setenv("GROQ_API_KEY", FAKE_KEY)
    res = runner.run_code("import os\nprint(os.environ.get('MY_FLAG'), os.environ['HOME'], 'GROQ_API_KEY' in os.environ)",
                          extra_env={"MY_FLAG": "on"})
    assert res.isolation_backend == "bubblewrap", res.stderr
    flag, home, leaked = res.stdout.split()
    assert flag == "on" and home.startswith("/tmp/zedek-sandbox-") and leaked == "False"
