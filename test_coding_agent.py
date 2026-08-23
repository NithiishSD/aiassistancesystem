"""
Test script for the current coding_agent.py — run this to see exactly what
works today (real sandbox execution) vs what's still a stub (plan_task,
patch — no LLM wired in yet).
"""

from coding_agent import CodingSpecialist, Verifier, SandboxedPythonRunner

specialist = CodingSpecialist()
verifier = Verifier()
runner = SandboxedPythonRunner()

print("=" * 60)
print("1. plan_task() — STUB, keyword-matching only, no real AI reasoning")
print("=" * 60)
plan1 = specialist.plan_task("fix the bug where free_space_summary crashes")
print(plan1)
print()
plan2 = specialist.plan_task("write a unit test for the prime checker")
print(plan2)
print("^ Notice steps[0]/steps[2] change based on keywords only — not real understanding.\n")

print("=" * 60)
print("2. patch() — STUB, literally just returns a template string")
print("=" * 60)
patch_result = specialist.patch("add a function that reverses a string", current_code="def foo(): pass")
print(patch_result)
print("^ This is NOT real generated code — just a placeholder message.\n")

print("=" * 60)
print("3. Verifier.verify_python() — REAL syntax checking")
print("=" * 60)
good_code = "def is_prime(n):\n    if n < 2: return False\n    return all(n % i for i in range(2, n))"
bad_code = "def broken(:\n    return 1 +"
print(f"Valid code check: {verifier.review_patch(good_code)}")
print(f"Broken code check: {verifier.review_patch(bad_code)}")
print()

print("=" * 60)
print("4. SandboxedPythonRunner.run() — REAL execution, real isolation")
print("=" * 60)

print("\n--- Test A: normal working code ---")
result = runner.run("print('hello from inside the sandbox')\nprint(2 + 2)")
print(result)

print("\n--- Test B: code that tries to access the network (should FAIL — proves isolation works) ---")
network_test = """
import socket
try:
    socket.create_connection(("8.8.8.8", 53), timeout=3)
    print("NETWORK ACCESS SUCCEEDED — this would be a sandbox failure")
except Exception as e:
    print(f"Network blocked as expected: {e}")
"""
result = runner.run(network_test)
print(result)

print("\n--- Test C: code that tries to read a file outside the sandbox (should FAIL) ---")
filesystem_test = """
try:
    with open('/etc/passwd') as f:
        print("FILE READ SUCCEEDED — check if this should be blocked")
        print(f.read()[:50])
except Exception as e:
    print(f"Blocked as expected: {e}")
"""
result = runner.run(filesystem_test)
print(result)

print("\n--- Test D: infinite loop (should TIME OUT, not hang forever) ---")
timeout_test = "while True:\n    pass"
result = runner.run(timeout_test)
print(result)

print("\n--- Test E: syntactically broken code (should be REJECTED before even trying to run) ---")
result = runner.run("def broken(:\n    return")
print(result)