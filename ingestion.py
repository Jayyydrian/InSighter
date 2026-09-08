"""REST source ingestion for normalized activity events."""

import os
from collections.abc import Mapping, Sequence
from typing import Any

import requests

from privacy import sanitize_event
from database import connect

DB_PATH = "database.db"


def ensure_logs_schema() -> None:
    """Add protection/source columns when opening a database from an older build."""
    conn = connect()
    try:
        _ensure_logs_columns(conn)
        _ensure_ingested_at_trigger(conn)
        conn.commit()
    finally:
        conn.close()


def _ensure_logs_columns(conn: Any) -> None:
    """Add missing log columns using SQLite-compatible ALTER TABLE defaults."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(logs)")}
    additions = {
        "role": "TEXT DEFAULT 'unknown'",
        "source": "TEXT DEFAULT 'synthetic'",
        "payload_encrypted": "TEXT DEFAULT ''",
        # SQLite prohibits CURRENT_TIMESTAMP in ALTER TABLE ADD COLUMN.
        "ingested_at": "TEXT DEFAULT NULL",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE logs ADD COLUMN {name} {definition}")

    # Backfill rows that predate this column; CURRENT_TIMESTAMP is valid in UPDATE.
    conn.execute(
        "UPDATE logs SET ingested_at = CURRENT_TIMESTAMP WHERE ingested_at IS NULL"
    )


def _ensure_ingested_at_trigger(conn: Any) -> None:
    """Give migrated tables the same timestamp behavior as newly created tables."""
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS logs_set_ingested_at
        AFTER INSERT ON logs
        FOR EACH ROW
        WHEN NEW.ingested_at IS NULL
        BEGIN
            UPDATE logs
            SET ingested_at = CURRENT_TIMESTAMP
            WHERE id = NEW.id;
        END
        """
    )


def validate_events(events: Sequence[Mapping[str, Any]]) -> None:
    """Validate normalized event inputs before any database rows are written."""
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        raise ValueError("The events value must be a list of event objects.")

    required_activity = (
        "login_hour",
        "files_accessed",
        "data_transferred_mb",
        "failed_logins",
        "off_hours_access",
    )
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            raise ValueError(f"Event {index} must be a JSON object.")
        if not any(event.get(field) not in (None, "") for field in ("user", "username", "email")):
            raise ValueError(f"Event {index} needs user, username, or email.")
        missing = [field for field in required_activity if field not in event]
        if missing:
            raise ValueError(f"Event {index} is missing required fields: {', '.join(missing)}")
        if not any(event.get(field) not in (None, "") for field in ("role", "job_role")):
            raise ValueError(f"Event {index} needs role or job_role.")


def store_events(events: Sequence[Mapping[str, Any]], source: str = "api") -> int:
    """Sanitize and append events without replacing synthetic records."""
    validate_events(events)
    conn = connect()
    _ensure_logs_columns(conn)
    inserted = 0
    try:
        for event in events:
            row = sanitize_event(event, source=source)
            conn.execute(
                """INSERT INTO logs
                (user, login_hour, files_accessed, data_transferred_mb,
                 failed_logins, off_hours_access, role, source, payload_encrypted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["user"], row["login_hour"], row["files_accessed"],
                    row["data_transferred_mb"], row["failed_logins"],
                    row["off_hours_access"], row["role"], row["source"],
                    row["payload_encrypted"],
                ),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted


def ingest_events(events: Sequence[Mapping[str, Any]], source: str = "api") -> int:
    """Validate and persist events through the shared privacy/database path."""
    return store_events(events, source=source)


def _extract_events(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("events", "records", "data", "logs"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    raise ValueError("The source API response must be a JSON object or list.")


def ingest_from_api() -> int:
    """Fetch events from INSIGHTER_SOURCE_API_URL using an optional bearer token."""
    url = os.environ.get("INSIGHTER_SOURCE_API_URL", "").strip()
    if not url:
        raise ValueError("INSIGHTER_SOURCE_API_URL is not configured.")

    headers = {"Accept": "application/json"}
    token = os.environ.get("INSIGHTER_SOURCE_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    events = _extract_events(response.json())
    return ingest_events(events, source="api")