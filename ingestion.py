"""REST and Active Directory ingestion for normalized activity events."""

from datetime import datetime, timedelta, timezone
import os

import requests

try:
    from ldap3 import AUTO_BIND_NO_TLS, Connection, Server
except ImportError:
    AUTO_BIND_NO_TLS = Connection = Server = None

from database import connect
from privacy import sanitize_event
from sector_config import DEFAULT_SECTOR, get_active_sector, get_sector_config, role_allowed

# Same fixed demo usernames generate_logs() seeds, in the same order, so
# legacy rows can be backfilled with a role from whatever sector is active.
_DEMO_USER_ORDER = ["alice", "bob", "charlie", "diana", "eve"]


def _default_role_for_user(user, sector):
    """Best-effort role for a legacy row, using generate_logs()'s round-robin
    convention against the *active sector's* role taxonomy (instead of a
    fixed, sector-agnostic job title)."""
    roles = get_sector_config(sector)["roles"]
    if user not in _DEMO_USER_ORDER or not roles:
        return None
    return roles[_DEMO_USER_ORDER.index(user) % len(roles)]


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
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE logs ADD COLUMN {name} {definition}")
                if name == "ingested_at":
                    conn.execute(
                        "UPDATE logs SET ingested_at = CURRENT_TIMESTAMP WHERE ingested_at IS NULL"
                    )
                if name == "data_category":
                    sector = get_active_sector(conn) or "sme_startup"
                    category = get_sector_config(sector)["data_categories"][0]
                    conn.execute(
                        "UPDATE logs SET data_category = ? WHERE data_category IS NULL OR data_category = ''",
                        (category,),
                    )
        sector = get_active_sector(conn) or DEFAULT_SECTOR
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
    ensure_logs_schema()
    conn = connect()
    inserted = 0
    try:
        for event in events:
            row = sanitize_event(event, source=source)
            sector = get_active_sector(conn)
            if sector is not None and not role_allowed(row["role"], sector):
                raise ValueError(
                    f"Role '{row['role']}' is not allowed for active sector '{sector}'."
                )
            data_category = str(event.get("data_category", "")).strip().lower()
            if not data_category:
                data_category = get_sector_config(sector)["data_categories"][0]
            conn.execute(
                """INSERT INTO logs
                (user, login_hour, files_accessed, data_transferred_mb,
                 failed_logins, off_hours_access, role, source, payload_encrypted,
                 ingested_at, data_category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)""",
                (
                    row["user"], row["login_hour"], row["files_accessed"],
                    row["data_transferred_mb"], row["failed_logins"],
                    row["off_hours_access"], row["role"], row["source"],
                    row["payload_encrypted"], data_category,
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
