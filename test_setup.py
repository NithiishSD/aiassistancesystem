"""
Phase 1 sanity check: confirms Ollama is running and both local models
respond. Run this after setup.sh completes.

    python test_setup.py
"""

import sys
import ollama


def test_model(model_name: str, prompt: str) -> None:
    print(f"\n--- Testing {model_name} ---")
    try:
        response = ollama.chat(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response["message"]["content"]
        print(f"Prompt:   {prompt}")
        print(f"Response: {text[:200]}")
        print(f"[OK] {model_name} responded.")
    except Exception as e:
        print(f"[FAIL] {model_name} did not respond: {e}")
        sys.exit(1)


def check_gpu() -> None:
    print("\n--- Checking GPU / VRAM visibility ---")
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        total_gb = mem.total / (1024 ** 3)
        used_gb = mem.used / (1024 ** 3)
        print(f"[OK] GPU detected. VRAM: {used_gb:.2f}GB used / {total_gb:.2f}GB total")
    except Exception as e:
        print(f"[WARN] Could not read GPU stats (pynvml): {e}")
        print("This is fine for now — VRAM checks matter starting Phase 3 (orchestrator).")


if __name__ == "__main__":
    print("=== Jarvis Phase 1 Sanity Check ===")

    test_model("qwen2.5-coder:7b", "Write a one-line Python function that adds two numbers.")
    test_model("llama3.1:8b", "In one sentence, what is the capital of France?")

    check_gpu()

    print("\n=== Phase 1 complete. Both local models responded. ===")
    print("Next: Phase 2 — logging module.")
