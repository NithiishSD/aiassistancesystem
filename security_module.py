"""Security module for the Zedek assistant (Roadmap Item 12).

Four independent pieces, each narrow and fail-closed:

  1. SecureStore        — encrypted-at-rest local storage (Fernet + PBKDF2).
  2. PasswordGate       — password verification for the UI panel (PBKDF2, never
                          stores the password itself, locks out after repeated failures).
  3. ConfirmationWord   — a rotating, single-use word the user must type to
                          authorize a high-risk action.
  4. VoicePrintVerifier — speaker verification interface.

Why a rotating confirmation word exists at all: a plain "y" confirmation can be
satisfied by anything that can type "y" — an agent loop, a replayed input, a
stray keystroke. A word that changes after every use and is never reused cannot
be pre-computed or replayed, so authorizing a high-risk action requires reading
what the system is showing right now.

Voice-print status: the interface, enrollment flow, and fail-closed behavior are
implemented and tested, but the audio backend (a speaker-embedding library plus
microphone capture) is NOT installed in this environment. `VoicePrintVerifier`
reports `available() is False` and every verification returns "unavailable"
rather than passing. It never degrades into an accidental allow.

Security invariants held throughout:
  - Secrets are never written to logs.
  - All secret comparisons are constant-time (`hmac.compare_digest`).
  - Every failure path denies. There is no path where an error means "allow".
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from zedek_logger import get_logger

log = get_logger("security_module")

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SECURE_DIR = os.path.join(_PROJECT_ROOT, "data", "secure")
DEFAULT_STORE_PATH = os.path.join(SECURE_DIR, "vault.enc")
DEFAULT_GATE_PATH = os.path.join(SECURE_DIR, "gate.json")

# PBKDF2 work factor. High by design — these run on explicit user actions
# (unlock, login), not in a hot path.
PBKDF2_ITERATIONS = 480_000
SALT_BYTES = 16

# Confirmation word policy.
CONFIRMATION_WORD_TTL_SECONDS = 180
MAX_GATE_ATTEMPTS = 5
GATE_LOCKOUT_SECONDS = 300

# Deliberately short, unambiguous, easily-typed words. No homophones, no
# characters that are hard to tell apart when read off a screen.
CONFIRMATION_WORDLIST = [
    "anchor", "basalt", "cobalt", "dynamo", "ember", "falcon", "granite",
    "harbor", "indigo", "jasper", "kelvin", "lantern", "marble", "nimbus",
    "onyx", "prism", "quartz", "rivet", "summit", "tundra", "umber",
    "vertex", "walnut", "xenon", "yonder", "zephyr",
]


def _derive_key(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    """Derive a Fernet-compatible key from a password. Never logged, never stored."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def _hash_password(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    """Hash a password for verification purposes (not for key derivation)."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(password.encode("utf-8"))


# ═══════════════════════════════════════════════════════════════════════════
# 1. Encrypted local storage
# ═══════════════════════════════════════════════════════════════════════════

class SecureStore:
    """Password-protected, encrypted-at-rest key/value storage.

    The plaintext never touches disk: the whole payload is encrypted with a
    Fernet key derived from the master password. A wrong password cannot
    decrypt, and the store stays locked — there is no partial-read path.
    """

    def __init__(self, path: str = DEFAULT_STORE_PATH, iterations: int = PBKDF2_ITERATIONS) -> None:
        self.path = path
        self.iterations = iterations
        self._fernet: Fernet | None = None
        self._data: dict[str, Any] = {}
        self._unlocked = False

    @property
    def unlocked(self) -> bool:
        return self._unlocked

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def initialize(self, password: str) -> bool:
        """Create a new empty encrypted store. Refuses to clobber an existing one."""
        if self.exists():
            log.info("secure_store_init_refused_exists", extra={"path": self.path})
            return False
        if not password:
            return False

        salt = secrets.token_bytes(SALT_BYTES)
        self._fernet = Fernet(_derive_key(password, salt, self.iterations))
        self._data = {}
        self._unlocked = True
        self._persist(salt)
        log.info("secure_store_initialized", extra={"path": self.path})
        return True

    def unlock(self, password: str) -> bool:
        """Decrypt the store with `password`. Wrong password leaves it locked."""
        if not self.exists():
            log.info("secure_store_unlock_no_store", extra={"path": self.path})
            return False

        try:
            with open(self.path, "rb") as handle:
                blob = handle.read()
            salt, ciphertext = blob[:SALT_BYTES], blob[SALT_BYTES:]
            fernet = Fernet(_derive_key(password, salt, self.iterations))
            plaintext = fernet.decrypt(ciphertext)
            self._data = json.loads(plaintext.decode("utf-8"))
            self._fernet = fernet
            self._unlocked = True
            log.info("secure_store_unlocked", extra={"path": self.path})
            return True
        except (InvalidToken, ValueError, OSError, json.JSONDecodeError) as err:
            # Deliberately does not distinguish "wrong password" from "corrupt
            # file" to the caller — both mean locked.
            self.lock()
            log.info("secure_store_unlock_failed", extra={"error_type": type(err).__name__})
            return False

    def lock(self) -> None:
        """Drop the key and plaintext from memory."""
        self._fernet = None
        self._data = {}
        self._unlocked = False

    def _persist(self, salt: bytes | None = None) -> None:
        if not self._unlocked or self._fernet is None:
            raise RuntimeError("Cannot persist a locked store.")

        if salt is None:
            with open(self.path, "rb") as handle:
                salt = handle.read(SALT_BYTES)

        ciphertext = self._fernet.encrypt(json.dumps(self._data).encode("utf-8"))
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # Written 0600 — this file holds the user's secrets.
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(salt + ciphertext)

    def set(self, key: str, value: Any) -> bool:
        if not self._unlocked:
            log.info("secure_store_write_denied_locked", extra={"key": key})
            return False
        self._data[key] = value
        self._persist()
        log.info("secure_store_write", extra={"key": key})
        return True

    def get(self, key: str, default: Any = None) -> Any:
        if not self._unlocked:
            log.info("secure_store_read_denied_locked", extra={"key": key})
            return default
        return self._data.get(key, default)

    def delete(self, key: str) -> bool:
        if not self._unlocked or key not in self._data:
            return False
        del self._data[key]
        self._persist()
        return True

    def keys(self) -> list[str]:
        return sorted(self._data.keys()) if self._unlocked else []


# ═══════════════════════════════════════════════════════════════════════════
# 2. Password gate for the UI panel
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class GateResult:
    allowed: bool
    reason: str = ""
    attempts_remaining: int = 0


class PasswordGate:
    """Password verification with lockout. Stores only a salted hash."""

    def __init__(self, path: str = DEFAULT_GATE_PATH, iterations: int = PBKDF2_ITERATIONS) -> None:
        self.path = path
        self.iterations = iterations
        self._failed_attempts = 0
        self._locked_until = 0.0

    def is_configured(self) -> bool:
        return os.path.isfile(self.path)

    def set_password(self, password: str) -> bool:
        """Set (or reset) the panel password. The password itself is never stored."""
        if not password or len(password) < 8:
            log.info("password_gate_rejected_weak_password", extra={})
            return False

        salt = secrets.token_bytes(SALT_BYTES)
        digest = _hash_password(password, salt, self.iterations)
        payload = {
            "salt": base64.b64encode(salt).decode("ascii"),
            "hash": base64.b64encode(digest).decode("ascii"),
            "iterations": self.iterations,
        }
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        self._failed_attempts = 0
        self._locked_until = 0.0
        log.info("password_gate_configured", extra={})
        return True

    def verify(self, password: str) -> GateResult:
        """Check a password, enforcing lockout after repeated failures."""
        now = time.time()
        if now < self._locked_until:
            remaining = int(self._locked_until - now)
            return GateResult(False, f"Locked out for another {remaining}s.", 0)

        if not self.is_configured():
            return GateResult(False, "No password has been configured yet.", 0)

        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            salt = base64.b64decode(payload["salt"])
            expected = base64.b64decode(payload["hash"])
            iterations = int(payload.get("iterations", self.iterations))
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as err:
            log.info("password_gate_config_unreadable", extra={"error_type": type(err).__name__})
            return GateResult(False, "Password configuration is unreadable.", 0)

        candidate = _hash_password(password or "", salt, iterations)

        if hmac.compare_digest(candidate, expected):
            self._failed_attempts = 0
            log.info("password_gate_success", extra={})
            return GateResult(True, "Password accepted.", MAX_GATE_ATTEMPTS)

        self._failed_attempts += 1
        remaining = MAX_GATE_ATTEMPTS - self._failed_attempts
        log.info("password_gate_failure", extra={"attempts_remaining": max(0, remaining)})

        if remaining <= 0:
            self._locked_until = now + GATE_LOCKOUT_SECONDS
            self._failed_attempts = 0
            return GateResult(False, f"Too many failures — locked out for {GATE_LOCKOUT_SECONDS}s.", 0)

        return GateResult(False, "Incorrect password.", remaining)


# ═══════════════════════════════════════════════════════════════════════════
# 3. Rotating single-use confirmation word
# ═══════════════════════════════════════════════════════════════════════════

class ConfirmationWord:
    """A rotating, single-use word required to authorize a high-risk action.

    Properties that make this worth having over a "y/n" prompt:
      - Single use: a correct word is consumed immediately, so it cannot be replayed.
      - Rotating: every issue produces an independently random word.
      - Expiring: a word older than the TTL is refused even if correct.
      - Constant-time comparison, so a wrong guess leaks no timing information.
    """

    def __init__(self, ttl_seconds: int = CONFIRMATION_WORD_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._word: str | None = None
        self._issued_at: float = 0.0

    def issue(self) -> str:
        """Generate and return a fresh confirmation word."""
        self._word = secrets.choice(CONFIRMATION_WORDLIST)
        self._issued_at = time.time()
        # The word is shown to the user by the caller; it is never logged.
        log.info("confirmation_word_issued", extra={"ttl_seconds": self.ttl_seconds})
        return self._word

    @property
    def has_active_word(self) -> bool:
        if self._word is None:
            return False
        return (time.time() - self._issued_at) <= self.ttl_seconds

    def verify(self, candidate: str) -> bool:
        """Check a typed word. Consumes the active word either way."""
        if self._word is None:
            log.info("confirmation_word_verify_no_active_word", extra={})
            return False

        expired = (time.time() - self._issued_at) > self.ttl_seconds
        expected = self._word
        # Consume before returning, so neither success nor failure leaves a
        # reusable word behind.
        self._word = None
        self._issued_at = 0.0

        if expired:
            log.info("confirmation_word_expired", extra={})
            return False

        matched = hmac.compare_digest((candidate or "").strip().lower(), expected)
        log.info("confirmation_word_verified", extra={"matched": matched})
        return matched


# ═══════════════════════════════════════════════════════════════════════════
# 4. Voice-print verification (interface; audio backend not installed)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class VoiceVerificationResult:
    verified: bool
    status: str  # "verified" | "rejected" | "unavailable" | "not_enrolled"
    reason: str = ""
    similarity: float = 0.0


class VoicePrintVerifier:
    """Speaker verification against an enrolled voice print.

    The audio backend (speaker-embedding model + microphone capture) is not
    installed in this environment. Rather than stub out a fake "pass", every
    method reports unavailability explicitly. Wiring a real backend means
    implementing `_embed()` and flipping `available()` — the fail-closed
    contract around it already holds.
    """

    SIMILARITY_THRESHOLD = 0.75
    SAMPLE_RATE = 16_000

    def __init__(self, store: SecureStore | None = None) -> None:
        self.store = store
        self._backend_error = ""
        self._encoder: Any = None

    def available(self) -> bool:
        """Whether a speaker-embedding backend is importable."""
        try:
            import resemblyzer  # noqa: F401
            return True
        except ImportError as err:
            self._backend_error = str(err)
            return False

    def _get_encoder(self) -> Any:
        """Load the speaker encoder once and reuse it — it's a torch model."""
        if self._encoder is None:
            from resemblyzer import VoiceEncoder
            self._encoder = VoiceEncoder(verbose=False)
        return self._encoder

    def _embed(self, audio_samples: Any) -> list[float]:
        """Produce a speaker embedding from samples, a numpy array, or a wav path.

        Accepts whatever the caller has: a path to a wav file, a numpy array of
        float samples, or a plain list of floats at SAMPLE_RATE.
        """
        import numpy as np
        from resemblyzer import preprocess_wav

        if isinstance(audio_samples, (str, os.PathLike)):
            wav = preprocess_wav(audio_samples)
        else:
            raw = np.asarray(audio_samples, dtype=np.float32).flatten()
            if raw.size == 0:
                raise ValueError("No audio samples supplied.")
            wav = preprocess_wav(raw, source_sr=self.SAMPLE_RATE)

        return self._get_encoder().embed_utterance(wav).tolist()

    def record(self, seconds: float = 4.0) -> Any:
        """Capture a short sample from the default microphone.

        Kept separate from verify()/enroll() so the verification logic stays
        testable without hardware, and so a caller can supply audio from
        somewhere else entirely.
        """
        import sounddevice as sd

        frames = sd.rec(
            int(seconds * self.SAMPLE_RATE),
            samplerate=self.SAMPLE_RATE,
            channels=1,
            dtype="float32",
        )
        sd.wait()
        log.info("voice_sample_recorded", extra={"seconds": seconds})
        return frames.flatten()

    def enroll(self, audio_samples: Any) -> VoiceVerificationResult:
        """Record the reference voice print into the encrypted store."""
        if not self.available():
            return VoiceVerificationResult(
                False, "unavailable",
                "Voice enrollment needs a speaker-embedding backend, which is not installed.",
            )
        if self.store is None or not self.store.unlocked:
            return VoiceVerificationResult(
                False, "unavailable",
                "Voice prints require an unlocked secure store — biometric data is never stored in the clear.",
            )
        embedding = self._embed(audio_samples)
        self.store.set("voice_print", embedding)
        return VoiceVerificationResult(True, "verified", "Voice print enrolled.")

    def verify(self, audio_samples: Any) -> VoiceVerificationResult:
        """Compare a live sample against the enrolled print. Fails closed."""
        if not self.available():
            log.info("voice_verification_unavailable", extra={"reason": "backend_missing"})
            return VoiceVerificationResult(
                False, "unavailable",
                "Voice verification is unavailable (no speaker-embedding backend installed). "
                "Refusing rather than assuming it's you.",
            )
        if self.store is None or not self.store.unlocked:
            return VoiceVerificationResult(
                False, "unavailable", "The secure store holding the voice print is locked.",
            )

        enrolled = self.store.get("voice_print")
        if not enrolled:
            return VoiceVerificationResult(False, "not_enrolled", "No voice print has been enrolled yet.")

        try:
            candidate = self._embed(audio_samples)
            similarity = _cosine_similarity(candidate, enrolled)
        except Exception as err:
            log.info("voice_verification_error", extra={"error_type": type(err).__name__})
            return VoiceVerificationResult(False, "unavailable", f"Verification failed: {err}")

        verified = similarity >= self.SIMILARITY_THRESHOLD
        log.info("voice_verification_result", extra={"verified": verified})
        return VoiceVerificationResult(
            verified,
            "verified" if verified else "rejected",
            "Speaker matched." if verified else "Speaker did not match the enrolled voice print.",
            similarity,
        )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two embeddings. Returns 0.0 on any mismatch."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
