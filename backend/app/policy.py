"""Per-job ElevenLabs consent and local audit records.

This is an operation guard for the trusted local API, not user authentication.
Records are append-only by this application, not tamper-proof against the owner.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone

from . import config


class PolicyError(ValueError):
    def __init__(self, message: str, status: int = 403):
        super().__init__(message)
        self.status = status


def story_approval(script: str, voice_id: str, *, cloud_approved: bool,
                   voice_consent: bool, max_characters: int) -> dict:
    """Validate before scheduling or performing any paid request.

    The cap counts input script characters conservatively (including markup).
    It is not a dollar estimate: provider billing depends on account and model.
    """
    if not cloud_approved:
        raise PolicyError("Approve sending this script to ElevenLabs and using paid character quota.")
    if not voice_consent:
        raise PolicyError("Confirm that you have permission to use the selected voice.")
    if not 1 <= max_characters <= 50000:
        raise PolicyError("max_characters must be between 1 and 50000", 422)
    if len(script) > max_characters:
        raise PolicyError(f"Script has {len(script)} characters; job limit is {max_characters}.", 422)
    if not script.strip():
        raise PolicyError("script is empty", 422)
    # A voice identifier is an identifier, never an arbitrary URL path.
    if not voice_id or len(voice_id) > 128 or not all(c.isascii() and (c.isalnum() or c in "_-") for c in voice_id):
        raise PolicyError("voice_id must contain only letters, numbers, underscores or hyphens", 422)
    return {
        "action": "elevenlabs.tts", "provider": "elevenlabs",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "script_sha256": hashlib.sha256(script.encode("utf-8")).hexdigest(),
        "characters": len(script), "max_characters": max_characters,
        "voice_id": voice_id, "model": config.ELEVENLABS_MODEL,
        "cloud_approved": True, "voice_consent": True,
        "consent_type": "caller_attestation", "ai_generated_audio": True,
    }


def record_story_event(plan_id: str, outcome: str, approval: dict) -> None:
    """Append one redacted, locked JSONL record. Failure blocks paid execution."""
    directory = config.DATA_DIR / "audit"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "operations.jsonl"
    record = {
        "schema_version": 1, "timestamp": datetime.now(timezone.utc).isoformat(),
        "plan_id": plan_id, "outcome": outcome, "approval": approval,
    }
    payload = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())

