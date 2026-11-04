"""REST and Active Directory ingestion for normalized activity events."""

from datetime import datetime, timedelta, timezone
import json
import os

import requests

try:
    from ldap3 import AUTO_BIND_NO_TLS, Connection, Server
except ImportError:
    AUTO_BIND_NO_TLS = Connection = Server = None

from database import connect
from privacy import sanitize_event
from sector_config import (
    DEFAULT_SECTOR, data_category_allowed, get_active_sector, get_sector_config,
    get_sector_roster, role_allowed,
)


def _default_role_for_user(user, sector):
    """Best-effort role lookup for users in the generated sector roster."""
    return dict((username, role) for username, role in get_sector_roster(sector)).get(user)


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
            "data_category": "TEXT DEFAULT ''",
            "feature_status": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE logs ADD COLUMN {name} {definition}")
                if name == "ingested_at":
                    conn.execute(
                        "UPDATE logs SET ingested_at = CURRENT_TIMESTAMP WHERE ingested_at IS NULL"
                    )
                if name == "data_category":
                    sector = get_active_sector(conn) or DEFAULT_SECTOR
                    category = get_sector_config(sector)["data_categories"][0]
                    conn.execute(
                        "UPDATE logs SET data_category = ? WHERE data_category IS NULL OR data_category = ''",
                        (category,),
                    )
        sector = get_active_sector(conn) or DEFAULT_SECTOR
        legacy_sensor_status = json.dumps({
            "login_hour": "supported",
            "files_accessed": "unsupported",
            "data_transferred_mb": "machine_wide",
            "failed_logins": "unsupported",
            "off_hours_access": "supported",
        }, separators=(",", ":"))
        conn.execute(
            """UPDATE logs SET feature_status = ?
               WHERE source = 'endpoint_sensor' AND feature_status IS NULL""",
            (legacy_sensor_status,),
        )
        missing = conn.execute(
            """SELECT DISTINCT user FROM logs
               WHERE role IS NULL OR role = '' OR role = 'unknown'"""
        ).fetchall()
        for (user,) in missing:
            role = _default_role_for_user(user, sector)
            if role:
                conn.execute(
                    """UPDATE logs SET role = ? WHERE user = ?
                       AND (role IS NULL OR role = '' OR role = 'unknown')""",
                    (role, user),
                )
        conn.commit()
    finally:
        conn.close()


def store_events(events, source="api"):
    """Sanitize and append external events without replacing synthetic records."""
    if not isinstance(events, list):
        raise ValueError("Events must be provided as a list.")
    ensure_logs_schema()
    conn = connect()
    inserted = 0
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS ingested_event_ids (
                event_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            "DELETE FROM ingested_event_ids WHERE created_at < datetime('now', '-30 days')"
        )
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("Each event must be a JSON object.")
            normalized_event = event
            feature_status = None
            if source == "endpoint_sensor":
                normalized_event = dict(event)
                normalized_event["user"] = event.get(
                    "user", event.get("username", event.get("email"))
                )
                telemetry_status = event.get("telemetry_status")
                network_status = (
                    telemetry_status.get("network")
                    if isinstance(telemetry_status, dict) else None
                )
                raw_transfer = event.get(
                    "data_transferred_mb", event.get("mb_transferred")
                )
                if "telemetry_status" not in event:
                    transfer_observed = raw_transfer is not None
                else:
                    transfer_observed = (
                        raw_transfer is not None
                        and isinstance(network_status, str)
                        and network_status in {"machine_wide_delta", "counter_reset"}
                    )
                normalized_event["data_transferred_mb"] = (
                    raw_transfer if transfer_observed else None
                )
                normalized_event["files_accessed"] = None
                normalized_event["failed_logins"] = None
                normalized_event["off_hours_access"] = event.get(
                    "off_hours_access", event.get("off_hours")
                )
                normalized_event["data_category"] = event.get(
                    "data_category", event.get("category", "")
                )
                feature_status = {
                    "login_hour": (
                        "supported"
                        if normalized_event.get("login_hour") is not None
                        else "unsupported"
                    ),
                    "files_accessed": "unsupported",
                    "data_transferred_mb": (
                        "machine_wide" if transfer_observed else "unsupported"
                    ),
                    "failed_logins": "unsupported",
                    "off_hours_access": (
                        "supported"
                        if normalized_event.get("off_hours_access") is not None
                        else "unsupported"
                    ),
                }
            row = sanitize_event(normalized_event, source=source)
            sector = get_active_sector(conn)
            if sector is not None and not role_allowed(row["role"], sector):
                raise ValueError(
                    f"Role '{row['role']}' is not allowed for active sector '{sector}'."
                )
            data_category = str(normalized_event.get("data_category", "")).strip().lower()
            if not data_category:
                data_category = get_sector_config(sector)["data_categories"][0]
            if source == "endpoint_sensor" and not data_category_allowed(data_category, sector):
                raise ValueError(
                    f"Data category '{data_category}' is not allowed for active sector '{sector}'."
                )
            event_id = event.get("event_id")
            if event_id is not None:
                if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
                    raise ValueError("Event ID must be a string between 1 and 128 characters.")
                inserted_id = conn.execute(
                    "INSERT OR IGNORE INTO ingested_event_ids (event_id) VALUES (?)",
                    (event_id,),
                )
                if inserted_id.rowcount == 0:
                    continue
            conn.execute(
                """INSERT INTO logs
                (user, login_hour, files_accessed, data_transferred_mb,
                 failed_logins, off_hours_access, role, source, payload_encrypted,
                 ingested_at, data_category, feature_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)""",
                (
                    row["user"], row["login_hour"], row["files_accessed"],
                    row["data_transferred_mb"], row["failed_logins"],
                    row["off_hours_access"], row["role"], row["source"],
                    row["payload_encrypted"], data_category,
                    json.dumps(feature_status, separators=(",", ":"))
                    if feature_status is not None else None,
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


def _active_directory_settings():
    """Read the minimum settings needed to bind to an AD/LDAP server."""
    server = os.environ.get("INSIGHTER_AD_SERVER", "").strip()
    username = os.environ.get("INSIGHTER_AD_USER", "").strip()
    password = os.environ.get("INSIGHTER_AD_PASSWORD", "")
    search_base = os.environ.get("INSIGHTER_AD_SEARCH_BASE", "").strip()
    if not all((server, username, password, search_base)):
        return None
    use_ssl = os.environ.get("INSIGHTER_AD_USE_SSL", "true").lower() in {"1", "true", "yes"}
    return {
        "server": server,
        "port": int(os.environ.get("INSIGHTER_AD_PORT", "636" if use_ssl else "389")),
        "use_ssl": use_ssl,
        "username": username,
        "password": password,
        "search_base": search_base,
        "search_filter": os.environ.get(
            "INSIGHTER_AD_SEARCH_FILTER",
            "(&(objectCategory=person)(objectClass=user)(sAMAccountName=*))",
        ),
    }


def _open_active_directory_connection(settings):
    if Server is None:
        raise RuntimeError("ldap3 is not installed; install dependencies before using Active Directory.")
    server = Server(
        settings["server"],
        port=settings["port"],
        use_ssl=settings["use_ssl"],
        connect_timeout=10,
    )
    return Connection(
        server,
        user=settings["username"],
        password=settings["password"],
        auto_bind=AUTO_BIND_NO_TLS,
        receive_timeout=20,
        raise_exceptions=True,
    )


def _windows_filetime(value):
    """Convert an AD FILETIME value to UTC, accepting ldap3 datetime values."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return None
    if raw <= 0:
        return None
    return datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=raw / 10)


def _attribute_value(attributes, name, default=None):
    value = attributes.get(name, default)
    if isinstance(value, (list, tuple)):
        return value[0] if value else default
    return value


def _active_directory_event(entry):
    """Map one AD user record to the normalized detector event shape."""
    attributes = entry.entry_attributes_as_dict
    last_logon = _windows_filetime(
        _attribute_value(attributes, "lastLogon")
        or _attribute_value(attributes, "lastLogonTimestamp")
    )
    return {
        "user": _attribute_value(attributes, "sAMAccountName", "unknown"),
        "role": _attribute_value(attributes, "title") or _attribute_value(attributes, "department") or "unknown",
        "login_hour": last_logon.hour if last_logon else 0,
        "files_accessed": 0,
        "data_transferred_mb": 0,
        "failed_logins": _attribute_value(attributes, "badPwdCount", 0) or 0,
        "off_hours_access": int(bool(last_logon and (last_logon.hour < 6 or last_logon.hour > 21))),
        "ad_last_logon": last_logon.isoformat() if last_logon else None,
        "data_category": "",
    }


def fetch_active_directory_events(connection=None):
    """Fetch AD user logon metadata, optionally using an injected ldap3 connection."""
    settings = _active_directory_settings()
    if settings is None:
        raise ValueError(
            "Active Directory requires INSIGHTER_AD_SERVER, INSIGHTER_AD_USER, "
            "INSIGHTER_AD_PASSWORD, and INSIGHTER_AD_SEARCH_BASE."
        )

    owned_connection = connection is None
    connection = connection or _open_active_directory_connection(settings)
    try:
        connection.search(
            search_base=settings["search_base"],
            search_filter=settings["search_filter"],
            attributes=[
                "sAMAccountName", "lastLogon", "lastLogonTimestamp",
                "badPwdCount", "title", "department",
            ],
        )
        return [_active_directory_event(entry) for entry in connection.entries]
    finally:
        if owned_connection:
            connection.unbind()


def ingest_from_active_directory(connection=None):
    """Fetch AD login metadata and send it through the common privacy path."""
    return store_events(
        fetch_active_directory_events(connection=connection),
        source="active_directory",
    )


def active_directory_status():
    """Return a UI-safe connection status without leaking bind details."""
    if _active_directory_settings() is None:
        return "not_connected", "Configure the AD server, bind credentials, and search base."
    try:
        connection = _open_active_directory_connection(_active_directory_settings())
        connection.unbind()
        return "connected", "LDAP bind succeeded; use Ingest Events to import AD logon metadata."
    except Exception:
        return "not_connected", "Active Directory connection failed; check server and credentials."


def ingest_from_configured_source():
    """Dispatch ingestion to the selected configured provider."""
    provider = os.environ.get("INSIGHTER_SOURCE_PROVIDER", "").strip().lower()
    if provider == "active_directory":
        return ingest_from_active_directory()
    return ingest_from_api()
