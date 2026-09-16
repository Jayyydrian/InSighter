"""
InSighter Authentication & Access Control module.

Implements the Authentication Tier described in Chapter 3:
  - BCrypt-hashed credentials (no plaintext passwords stored)
  - Session-based login (server-rendered dashboard; JWT is reserved for the
    edge/API-facing tiers per the Chapter 3 architecture)
  - RBAC with three roles for this POC:
        admin       -> full access: per-user risk matrix, alerts, drill-down
      compliance  -> flagged records with pseudonymized identities
        management  -> summary-level access only (aggregate stats, no
                        individual user activity details)
"""

import bcrypt
import hashlib
import math
import time
from functools import wraps
from flask import session, redirect, url_for, request, render_template
from database import connect
from sector_config import DEFAULT_SECTOR, SECTORS

MAX_LOGIN_ATTEMPTS = 3
LOGIN_COOLDOWN_SECONDS = 60
DUMMY_PASSWORD_HASH = bcrypt.hashpw(
    b"insighter-invalid-account-password",
    bcrypt.gensalt(),
).decode()


def init_users_table():
    """Create the users table and seed default accounts if empty."""
    conn = connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL CHECK(role IN ('admin', 'management', 'compliance')),
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
    ).fetchone()[0]
    if "compliance" not in schema:
        conn.execute("""
            CREATE TABLE users_new (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL CHECK(role IN ('admin', 'management', 'compliance')),
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            INSERT INTO users_new (id, username, password_hash, role, created_at)
            SELECT id, username, password_hash, role, created_at FROM users
        """)
        conn.execute("DROP TABLE users")
        conn.execute("ALTER TABLE users_new RENAME TO users")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deployment_config (
            id                      INTEGER PRIMARY KEY CHECK (id = 1),
            sector                  TEXT DEFAULT 'sme_startup',
            threshold_high          REAL DEFAULT 45,
            threshold_medium        REAL DEFAULT 30,
            log_targets             TEXT DEFAULT 'active_directory,google_workspace,microsoft_365',
            sync_interval_minutes   INTEGER DEFAULT 15,
            drone_operator_username TEXT DEFAULT 'drone_operator',
            updated_at              TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    deployment_columns = {row[1] for row in conn.execute("PRAGMA table_info(deployment_config)")}
    if "sector" not in deployment_columns:
        conn.execute("ALTER TABLE deployment_config ADD COLUMN sector TEXT DEFAULT 'sme_startup'")
    legacy = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='uav_config'"
    ).fetchone()
    if legacy:
        conn.execute("""
            INSERT OR IGNORE INTO deployment_config
            (id, sector, threshold_high, threshold_medium, log_targets,
             sync_interval_minutes, drone_operator_username, updated_at)
            SELECT id, ?, threshold_high, threshold_medium, log_targets,
                   sync_interval_minutes, drone_operator_username, updated_at
            FROM uav_config
        """, (DEFAULT_SECTOR,))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT,
            action     TEXT NOT NULL,
            detail     TEXT DEFAULT '',
            timestamp  TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS login_security (
            username_key    TEXT PRIMARY KEY,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            locked_until    REAL NOT NULL DEFAULT 0,
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    row = conn.execute("SELECT COUNT(*) FROM deployment_config").fetchone()[0]
    if row == 0:
        conn.execute("INSERT INTO deployment_config (id, sector) VALUES (1, ?)", (DEFAULT_SECTOR,))
    conn.commit()

    seed_users = [
        ("admin", "admin123", "admin"),
        ("manager", "manager123", "management"),
        ("hr_officer", "hr_officer123", "compliance"),
    ]
    for username, plain_pw, role in seed_users:
        pw_hash = bcrypt.hashpw(plain_pw.encode(), bcrypt.gensalt()).decode()
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?,?,?)",
            (username, pw_hash, role),
        )
    conn.commit()
    conn.close()


def log_action(username, action, detail=""):
    """
    Record an entry in the audit trail. Implements Chapter 3's
    'Privacy-Compliant Audit Mode' -- controlled visibility into who did
    what, for Data Privacy Act (R.A. 10173) accountability.
    """
    conn = connect()
    conn.execute(
        "INSERT INTO audit_log (username, action, detail) VALUES (?,?,?)",
        (username, action, detail),
    )
    conn.commit()
    conn.close()


def verify_login(username, password):
    """Return the user's role if credentials are valid, else None."""
    role, _ = authenticate_login(username, password)
    return role


def _login_username_key(username):
    return hashlib.sha256(username.casefold().encode("utf-8")).hexdigest()


def authenticate_login(username, password):
    """Return ``(role, retry_after)`` while enforcing the login cooldown policy."""
    username_key = _login_username_key(username)
    now = time.time()
    conn = connect()
    security = conn.execute(
        "SELECT failed_attempts, locked_until FROM login_security WHERE username_key = ?",
        (username_key,),
    ).fetchone()
    if security and security[1] > now:
        retry_after = max(1, math.ceil(security[1] - now))
        conn.close()
        return None, retry_after

    row = conn.execute(
        "SELECT password_hash, role FROM users WHERE username = ?", (username,)
    ).fetchone()
    pw_hash = row[0] if row else DUMMY_PASSWORD_HASH
    valid = bcrypt.checkpw(password.encode(), pw_hash.encode()) if row else False
    if valid:
        conn.execute("DELETE FROM login_security WHERE username_key = ?", (username_key,))
        conn.commit()
        conn.close()
        return row[1], None

    failed_attempts = (security[0] if security else 0) + 1
    locked_until = now + LOGIN_COOLDOWN_SECONDS if failed_attempts >= MAX_LOGIN_ATTEMPTS else 0
    conn.execute(
        """INSERT INTO login_security (username_key, failed_attempts, locked_until)
           VALUES (?, ?, ?)
           ON CONFLICT(username_key) DO UPDATE SET
             failed_attempts = excluded.failed_attempts,
             locked_until = excluded.locked_until,
             updated_at = CURRENT_TIMESTAMP""",
        (username_key, failed_attempts, locked_until),
    )
    conn.commit()
    conn.close()
    return None, LOGIN_COOLDOWN_SECONDS if locked_until else None


def login_required(view_func):
    """Redirect to /login if there is no active session."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped


def admin_required(view_func):
    """Block access unless the session role is 'admin'."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login", next=request.path))
        if session.get("role") != "admin":
            return render_template("forbidden.html"), 403
        return view_func(*args, **kwargs)
    return wrapped


def compliance_required(view_func):
    """Block access unless the session role is the pseudonymized compliance tier."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login", next=request.path))
        if session.get("role") != "compliance":
            return render_template("forbidden.html"), 403
        return view_func(*args, **kwargs)
    return wrapped
