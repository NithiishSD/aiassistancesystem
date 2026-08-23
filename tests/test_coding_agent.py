from coding_agent import CodingSpecialist, SandboxedPythonRunner, Verifier


def test_coding_specialist_builds_a_plan():
    specialist = CodingSpecialist()
    plan = specialist.plan_task("Add a helper that uppercases text and validate it with a unit test")

    assert plan["goal"].lower()
    assert len(plan["steps"]) >= 3
    assert any("patch" in step.lower() for step in plan["steps"])
    assert any("test" in step.lower() for step in plan["steps"])


def test_verifier_accepts_valid_python_and_rejects_bad_syntax():
    verifier = Verifier()

    ok = verifier.verify_python("def normalize(text):\n    return text.strip().upper()\n")
    bad = verifier.verify_python("def broken(:\n    return 'oops'\n")

    assert ok is True
    assert bad is False


def test_sandboxed_runner_executes_valid_code_without_shell():
    result = SandboxedPythonRunner().run("print('sandbox-ok')")

    assert result["status"] in {"passed", "unavailable"}
    if result["status"] == "passed":
        assert result["stdout"].strip() == "sandbox-ok"


def test_sandboxed_runner_rejects_invalid_code():
    result = SandboxedPythonRunner().run("print(")

    assert result["status"] == "rejected"
