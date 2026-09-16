"""
Unit tests for the security module (Roadmap Item 12).

These are security invariants, not feature tests. The properties that must
hold no matter what:
- Plaintext secrets never appear in the on-disk vault.
- A wrong password never yields a partial read; the store stays locked.
- A confirmation word is single-use, expiring, and never replayable.
- Voice verification with no backend REFUSES — it never accidentally passes.
- The password gate stores no password, and locks out brute force.
"""

import json
import os

import pytest

import security_module
from security_module import (
    ConfirmationWord,
    PasswordGate,
    SecureStore,
    VoicePrintVerifier,
)

# PBKDF2 at production strength makes the suite crawl; these tests exercise
# logic, not work factor, so they use a low count. One test below explicitly
# asserts the production default is still high.
FAST_ITERATIONS = 1_000


@pytest.fixture
def store(tmp_path):
    return SecureStore(path=str(tmp_path / "vault.enc"), iterations=FAST_ITERATIONS)


@pytest.fixture
def gate(tmp_path):
    return PasswordGate(path=str(tmp_path / "gate.json"), iterations=FAST_ITERATIONS)


class TestSecureStore:
    def test_initialize_creates_store(self, store):
        assert store.initialize("correct horse battery") is True
        assert store.exists() is True
        assert store.unlocked is True

    def test_initialize_refuses_to_clobber(self, store):
        store.initialize("first password")
        assert store.initialize("second password") is False

    def test_roundtrip_after_relock(self, store):
        store.initialize("master password")
        store.set("api_key", "super-secret-value")
        store.lock()
        assert store.unlocked is False

        assert store.unlock("master password") is True
        assert store.get("api_key") == "super-secret-value"

    def test_wrong_password_fails_and_stays_locked(self, store):
        store.initialize("right password")
        store.set("api_key", "secret")
        store.lock()

        assert store.unlock("wrong password") is False
        assert store.unlocked is False
        assert store.get("api_key") is None

    def test_plaintext_never_on_disk(self, store):
        store.initialize("master password")
        store.set("api_key", "PLAINTEXT-CANARY-VALUE")

        raw = open(store.path, "rb").read()
        assert b"PLAINTEXT-CANARY-VALUE" not in raw
        assert b"api_key" not in raw

    def test_locked_store_denies_writes(self, store):
        store.initialize("password here")
        store.lock()
        assert store.set("k", "v") is False

    def test_locked_store_denies_reads(self, store):
        store.initialize("password here")
        store.set("k", "v")
        store.lock()
        assert store.get("k") is None
        assert store.keys() == []

    def test_delete_removes_key(self, store):
        store.initialize("password here")
        store.set("k", "v")
        assert store.delete("k") is True
        assert store.get("k") is None

    def test_unlock_nonexistent_store_fails(self, store):
        assert store.unlock("anything") is False

    def test_corrupt_store_fails_closed(self, store):
        store.initialize("password here")
        store.lock()
        with open(store.path, "wb") as handle:
            handle.write(b"this is not a valid encrypted payload")
        assert store.unlock("password here") is False
        assert store.unlocked is False

    def test_store_file_permissions_are_owner_only(self, store):
        store.initialize("password here")
        mode = os.stat(store.path).st_mode & 0o777
        assert mode == 0o600


class TestPasswordGate:
    def test_password_is_not_stored(self, gate):
        gate.set_password("my-secret-password")
        contents = open(gate.path).read()
        assert "my-secret-password" not in contents

    def test_correct_password_accepted(self, gate):
        gate.set_password("my-secret-password")
        assert gate.verify("my-secret-password").allowed is True

    def test_wrong_password_rejected(self, gate):
        gate.set_password("my-secret-password")
        assert gate.verify("not-the-password").allowed is False

    def test_short_password_refused(self, gate):
        assert gate.set_password("short") is False
        assert gate.is_configured() is False

    def test_unconfigured_gate_denies(self, gate):
        assert gate.verify("anything").allowed is False

    def test_lockout_after_repeated_failures(self, gate):
        gate.set_password("my-secret-password")
        for _ in range(security_module.MAX_GATE_ATTEMPTS):
            gate.verify("wrong")
        # Even the correct password is refused while locked out.
        result = gate.verify("my-secret-password")
        assert result.allowed is False
        assert "locked out" in result.reason.lower()

    def test_successful_verify_resets_attempt_counter(self, gate):
        gate.set_password("my-secret-password")
        gate.verify("wrong")
        gate.verify("wrong")
        gate.verify("my-secret-password")
        result = gate.verify("wrong")
        assert result.attempts_remaining == security_module.MAX_GATE_ATTEMPTS - 1

    def test_gate_file_permissions_are_owner_only(self, gate):
        gate.set_password("my-secret-password")
        mode = os.stat(gate.path).st_mode & 0o777
        assert mode == 0o600

    def test_unreadable_config_fails_closed(self, gate):
        gate.set_password("my-secret-password")
        with open(gate.path, "w") as handle:
            handle.write("not json")
        assert gate.verify("my-secret-password").allowed is False


class TestConfirmationWord:
    def test_issued_word_verifies(self):
        cw = ConfirmationWord()
        word = cw.issue()
        assert cw.verify(word) is True

    def test_word_is_single_use(self):
        cw = ConfirmationWord()
        word = cw.issue()
        assert cw.verify(word) is True
        # Replaying the same word must fail.
        assert cw.verify(word) is False

    def test_failed_attempt_also_consumes_the_word(self):
        """A wrong guess must not leave the word available for another try."""
        cw = ConfirmationWord()
        word = cw.issue()
        assert cw.verify("wrongword") is False
        assert cw.verify(word) is False

    def test_verify_without_issue_fails(self):
        assert ConfirmationWord().verify("anything") is False

    def test_expired_word_rejected(self):
        cw = ConfirmationWord(ttl_seconds=0)
        word = cw.issue()
        import time
        time.sleep(0.01)
        assert cw.verify(word) is False

    def test_case_and_whitespace_tolerated(self):
        cw = ConfirmationWord()
        word = cw.issue()
        assert cw.verify(f"  {word.upper()}  ") is True

    def test_rotation_produces_varied_words(self):
        cw = ConfirmationWord()
        seen = set()
        for _ in range(40):
            seen.add(cw.issue())
        # With a 26-word list, 40 draws should not collapse to one value.
        assert len(seen) > 1

    def test_has_active_word_reflects_state(self):
        cw = ConfirmationWord()
        assert cw.has_active_word is False
        word = cw.issue()
        assert cw.has_active_word is True
        cw.verify(word)
        assert cw.has_active_word is False

    def test_empty_candidate_rejected(self):
        cw = ConfirmationWord()
        cw.issue()
        assert cw.verify("") is False


class TestVoicePrintVerifier:
    def test_backend_reported_unavailable(self):
        """No speaker-embedding library is installed in this environment."""
        assert VoicePrintVerifier().available() is False

    def test_verification_without_backend_refuses(self):
        result = VoicePrintVerifier().verify(audio_samples=[0.1, 0.2])
        assert result.verified is False
        assert result.status == "unavailable"

    def test_enrollment_without_backend_refuses(self):
        result = VoicePrintVerifier().enroll(audio_samples=[0.1, 0.2])
        assert result.verified is False
        assert result.status == "unavailable"

    def test_locked_store_blocks_verification(self, store, monkeypatch):
        monkeypatch.setattr(VoicePrintVerifier, "available", lambda self: True)
        verifier = VoicePrintVerifier(store=store)
        result = verifier.verify(audio_samples=[0.1])
        assert result.verified is False
        assert result.status == "unavailable"

    def test_not_enrolled_reported(self, store, monkeypatch):
        monkeypatch.setattr(VoicePrintVerifier, "available", lambda self: True)
        store.initialize("master password")
        verifier = VoicePrintVerifier(store=store)
        result = verifier.verify(audio_samples=[0.1])
        assert result.status == "not_enrolled"

    def test_embedding_failure_fails_closed(self, store, monkeypatch):
        monkeypatch.setattr(VoicePrintVerifier, "available", lambda self: True)
        store.initialize("master password")
        store.set("voice_print", [1.0, 0.0, 0.0])
        verifier = VoicePrintVerifier(store=store)
        result = verifier.verify(audio_samples=[0.1])
        assert result.verified is False

    def test_matching_embedding_verifies(self, store, monkeypatch):
        monkeypatch.setattr(VoicePrintVerifier, "available", lambda self: True)
        monkeypatch.setattr(VoicePrintVerifier, "_embed", lambda self, a: [1.0, 0.0, 0.0])
        store.initialize("master password")
        store.set("voice_print", [1.0, 0.0, 0.0])
        result = VoicePrintVerifier(store=store).verify(audio_samples=[0.1])
        assert result.verified is True

    def test_mismatched_embedding_rejected(self, store, monkeypatch):
        monkeypatch.setattr(VoicePrintVerifier, "available", lambda self: True)
        monkeypatch.setattr(VoicePrintVerifier, "_embed", lambda self, a: [0.0, 1.0, 0.0])
        store.initialize("master password")
        store.set("voice_print", [1.0, 0.0, 0.0])
        result = VoicePrintVerifier(store=store).verify(audio_samples=[0.1])
        assert result.verified is False
        assert result.status == "rejected"

    def test_voice_print_stored_encrypted(self, store, monkeypatch):
        monkeypatch.setattr(VoicePrintVerifier, "available", lambda self: True)
        monkeypatch.setattr(VoicePrintVerifier, "_embed", lambda self, a: [0.4242424242, 0.1, 0.2])
        store.initialize("master password")
        VoicePrintVerifier(store=store).enroll(audio_samples=[0.1])
        raw = open(store.path, "rb").read()
        assert b"0.4242424242" not in raw
        assert b"voice_print" not in raw


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert security_module._cosine_similarity([1.0, 2.0], [1.0, 2.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert security_module._cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_mismatched_lengths_return_zero(self):
        assert security_module._cosine_similarity([1.0], [1.0, 2.0]) == 0.0

    def test_empty_vectors_return_zero(self):
        assert security_module._cosine_similarity([], []) == 0.0

    def test_zero_vector_returns_zero(self):
        assert security_module._cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


class TestProductionHardening:
    def test_default_iteration_count_is_high(self):
        """The fast fixtures must not have weakened the shipped default."""
        assert security_module.PBKDF2_ITERATIONS >= 200_000

    def test_derived_keys_differ_per_salt(self):
        key_a = security_module._derive_key("same password", b"a" * 16, FAST_ITERATIONS)
        key_b = security_module._derive_key("same password", b"b" * 16, FAST_ITERATIONS)
        assert key_a != key_b
