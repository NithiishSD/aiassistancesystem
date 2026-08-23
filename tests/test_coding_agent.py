from coding_agent import CodingSpecialist, Verifier


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
