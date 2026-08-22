"""REST source ingestion for normalized activity events."""

import os

import requests

from database import connect
from privacy import sanitize_event


def ensure_logs_schema():
    """Add protection and source columns to older databases."""
    conn = connect()
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(logs)")}
        additions = {
            "role": "TEXT DEFAULT 'unknown'",
            "source": "TEXT DEFAULT 'synthetic'",
            "payload_encrypted": "TEXT DEFAULT ''",
            "ingested_at": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE logs ADD COLUMN {name} {definition}")
                if name == "ingested_at":
                    conn.execute(
                        "UPDATE logs SET ingested_at = CURRENT_TIMESTAMP WHERE ingested_at IS NULL"
                    )
        conn.execute(
            """UPDATE logs SET role = CASE user
                WHEN 'alice' THEN 'staff'
                WHEN 'bob' THEN 'staff'
                WHEN 'charlie' THEN 'developer'
                WHEN 'diana' THEN 'staff'
                WHEN 'eve' THEN 'finance analyst'
                ELSE role END
            WHERE role IS NULL OR role = '' OR role = 'unknown'"""
        )
        conn.commit()
    finally:
        conn.close()


def store_events(events, source="api"):
    """Sanitize and append external events without replacing synthetic records."""
    ensure_logs_schema()
    conn = connect()
    inserted = 0
    try:
        for event in events:
            row = sanitize_event(event, source=source)
            conn.execute(
                """INSERT INTO logs
                (user, login_hour, files_accessed, data_transferred_mb,
                 failed_logins, off_hours_access, role, source, payload_encrypted, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
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


def _extract_events(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("events", "records", "data", "logs"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    raise ValueError("The source API response must be a JSON object or list.")


def ingest_from_api():
    """Fetch and append events from the configured REST source."""
    url = os.environ.get("INSIGHTER_SOURCE_API_URL", "").strip()
    if not url:
        raise ValueError("INSIGHTER_SOURCE_API_URL is not configured.")

    headers = {"Accept": "application/json"}
    token = os.environ.get("INSIGHTER_SOURCE_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    return store_events(_extract_events(response.json()), source="api")
