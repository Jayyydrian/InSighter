"""PII protection helpers for identifiers and stored source records."""

import base64
import hashlib
import hmac
import json
import os

from cryptography.fernet import Fernet


def _secret():
    return os.environ.get(
        "INSIGHTER_PII_SECRET",
        os.environ.get("INSIGHTER_SECRET_KEY", "insighter-local-development-secret"),
    ).encode("utf-8")


def hash_identifier(value):
    """Return a stable, non-reversible identifier for a source user."""
    normalized = str(value or "unknown").strip().lower().encode("utf-8")
    return hmac.new(_secret(), normalized, hashlib.sha256).hexdigest()[:24]


def encrypt_payload(payload):
    """Encrypt non-identity event details for controlled recovery/audit use."""
    key = base64.urlsafe_b64encode(hashlib.sha256(_secret()).digest())
    token = Fernet(key).encrypt(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    )
    return token.decode("ascii")


def _remove_identifiers(event):
    return {
        key: value
        for key, value in event.items()
        if key.lower() not in {"user", "username", "email"}
    }


def sanitize_event(event, source="synthetic"):
    """Normalize an event and protect its original non-identity payload."""
    user = event.get("user", event.get("username", event.get("email")))
    if user is None:
        raise ValueError("Each event needs a user, username, or email field.")

    return {
        "user": str(user).strip(),
        "role": str(event.get("role", event.get("job_role", "unknown"))).strip().lower() or "unknown",
        "login_hour": int(event.get("login_hour", 0)),
        "files_accessed": int(event.get("files_accessed", 0)),
        "data_transferred_mb": float(event.get("data_transferred_mb", 0)),
        "failed_logins": int(event.get("failed_logins", 0)),
        "off_hours_access": int(bool(event.get("off_hours_access", 0))),
        "source": source,
        "payload_encrypted": encrypt_payload(_remove_identifiers(event)),
    }
