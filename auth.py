"""
InSighter Authentication & Access Control module.

Implements the Authentication Tier described in Chapter 3:
    - PBKDF2-HMAC-SHA256 credentials with per-password random salts
  - Session-based login (server-rendered dashboard; JWT is reserved for the
    edge/API-facing tiers per the Chapter 3 architecture)
  - RBAC with three roles for this POC:
        admin       -> full access: per-user risk matrix, alerts, drill-down
      compliance  -> flagged records with pseudonymized identities
        management  -> summary-level access only (aggregate stats, no
                        individual user activity details)
"""

import hmac
import hashlib
import json
import logging
import math
import os
import secrets
import time
from functools import wraps
from flask import has_request_context, session, redirect, url_for, request, render_template
from flask.sessions import SessionInterface, SessionMixin
from werkzeug.datastructures import CallbackDict
from database import connect
from sector_config import DEFAULT_SECTOR, SECTORS

MAX_LOGIN_ATTEMPTS = 3
LOGIN_COOLDOWN_SECONDS = 60
PASSWORD_HASH_ITERATIONS = 600_000
SESSION_IDLE_SECONDS = 30 * 60
SESSION_ABSOLUTE_SECONDS = 8 * 60 * 60


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_HASH_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, encoded):
    try:
        scheme, iterations, salt_hex, digest_hex = encoded.split("$")
        rounds = int(iterations)
        if scheme != "pbkdf2_sha256" or not PASSWORD_HASH_ITERATIONS <= rounds <= 2_000_000:
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), rounds
        ).hex()
        return hmac.compare_digest(candidate, digest_hex)
    except (AttributeError, TypeError, ValueError):
        return False


DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


class DatabaseSession(CallbackDict, SessionMixin):
    def __init__(self, initial=None, sid=None, new=False):
        def on_update(_):
            self.modified = True

        super().__init__(initial, on_update)
        self.sid = sid
        self.new = new
        self.modified = False


class DatabaseSessionInterface(SessionInterface):
    session_class = DatabaseSession

    def open_session(self, app, req):
        sid = req.cookies.get(self.get_cookie_name(app))
        if not sid or len(sid) > 128:
            return self.session_class(sid=secrets.token_urlsafe(32), new=True)
        sid_hash = hashlib.sha256(sid.encode()).hexdigest()
        now = time.time()
        conn = connect()
        try:
            row = conn.execute(
                "SELECT data, created_at, last_seen FROM auth_sessions WHERE sid_hash = ?", (sid_hash,)
            ).fetchone()
            if not row or now - row[2] > SESSION_IDLE_SECONDS or now - row[1] > SESSION_ABSOLUTE_SECONDS:
                conn.execute("DELETE FROM auth_sessions WHERE sid_hash = ?", (sid_hash,))
                conn.commit()
                return self.session_class(sid=secrets.token_urlsafe(32), new=True)
            conn.execute("UPDATE auth_sessions SET last_seen = ? WHERE sid_hash = ?", (now, sid_hash))
            conn.commit()
            return self.session_class(json.loads(row[0]), sid=sid)
        finally:
            conn.close()

    def save_session(self, app, sess, response):
        cookie_name = self.get_cookie_name(app)
        sid_hash = hashlib.sha256(sess.sid.encode()).hexdigest()
        conn = connect()
        try:
            if not sess:
                conn.execute("DELETE FROM auth_sessions WHERE sid_hash = ?", (sid_hash,))
                conn.commit()
                response.delete_cookie(
                    cookie_name, path=self.get_cookie_path(app), domain=self.get_cookie_domain(app),
                    secure=app.config.get("SESSION_COOKIE_SECURE", True), httponly=True, samesite="Lax",
                )
                return
            now = time.time()
            conn.execute(
                """INSERT INTO auth_sessions (sid_hash, data, created_at, last_seen)
                   VALUES (?, ?, ?, ?) ON CONFLICT(sid_hash) DO UPDATE SET
                   data=excluded.data, last_seen=excluded.last_seen""",
                (sid_hash, json.dumps(dict(sess)), now, now),
            )
            conn.commit()
        finally:
            conn.close()
        response.set_cookie(
            cookie_name, sess.sid, expires=now + SESSION_ABSOLUTE_SECONDS,
            httponly=True, secure=app.config.get("SESSION_COOKIE_SECURE", True), samesite="Lax",
            path=self.get_cookie_path(app), domain=self.get_cookie_domain(app),
        )


def rotate_session():
    old_sid = session.sid
    conn = connect()
    try:
        conn.execute("DELETE FROM auth_sessions WHERE sid_hash = ?", (hashlib.sha256(old_sid.encode()).hexdigest(),))
        conn.commit()
    finally:
        conn.close()
    session.sid = secrets.token_urlsafe(32)
    session.modified = True


def _normalize_username(username):
    return (username or "").strip().casefold()


def _login_throttle_key(username, client_ip):
    return hashlib.sha256(f"{client_ip}\0{_normalize_username(username)}".encode("utf-8")).hexdigest()


def init_users_table():
    """Create the users table and seed default accounts if empty."""
    conn = connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL CHECK(role IN ('admin', 'management', 'compliance')),
            created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
            is_active     INTEGER NOT NULL DEFAULT 1
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
                created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                is_active     INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("""
            INSERT INTO users_new (id, username, password_hash, role, created_at, is_active)
            SELECT id, username, password_hash, role, created_at, 0 FROM users
        """)
        conn.execute("DROP TABLE users")
        conn.execute("ALTER TABLE users_new RENAME TO users")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deployment_config (
            id                      INTEGER PRIMARY KEY CHECK (id = 1),
            sector                  TEXT DEFAULT 'private_school',
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
        conn.execute("ALTER TABLE deployment_config ADD COLUMN sector TEXT DEFAULT 'private_school'")
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
    user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "is_active" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        conn.execute("UPDATE users SET is_active = 0")
        conn.execute("UPDATE users SET is_active = 0")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auth_sessions (
            sid_hash TEXT PRIMARY KEY, data TEXT NOT NULL, created_at REAL NOT NULL, last_seen REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS login_throttle (
            throttle_key TEXT PRIMARY KEY, failed_attempts INTEGER NOT NULL,
            window_started REAL NOT NULL, locked_until REAL NOT NULL
        )
    """)
    row = conn.execute("SELECT COUNT(*) FROM deployment_config").fetchone()[0]
    if row == 0:
        conn.execute("INSERT INTO deployment_config (id, sector) VALUES (1, ?)", (DEFAULT_SECTOR,))
    elif conn.execute("SELECT sector FROM deployment_config WHERE id = 1").fetchone()[0] not in SECTORS:
        previous_sector = conn.execute("SELECT sector FROM deployment_config WHERE id = 1").fetchone()[0]
        logging.getLogger(__name__).warning(
            "Unsupported deployment sector %r migrated to %s",
            previous_sector,
            DEFAULT_SECTOR,
        )
        conn.execute(
            "UPDATE deployment_config SET sector = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
            (DEFAULT_SECTOR,),
        )
        conn.execute(
            "INSERT INTO audit_log (username, action, detail) VALUES (?, ?, ?)",
            ("system", "SECTOR_MIGRATED", f"sector={previous_sector};new_sector={DEFAULT_SECTOR}"),
        )
    conn.commit()

    bootstrap_username = _normalize_username(os.environ.get("INSIGHTER_ADMIN_USERNAME"))
    bootstrap_password = os.environ.get("INSIGHTER_ADMIN_PASSWORD", "")
    if bool(bootstrap_username) != bool(bootstrap_password):
        conn.close()
        raise RuntimeError("Set both INSIGHTER_ADMIN_USERNAME and INSIGHTER_ADMIN_PASSWORD")
    if bootstrap_username and bootstrap_password:
        if len(bootstrap_password) < 14:
            conn.close()
            raise RuntimeError("INSIGHTER_ADMIN_PASSWORD must contain at least 14 characters")
        admin_exists = conn.execute("SELECT id FROM users WHERE role = 'admin' AND is_active = 1 LIMIT 1").fetchone()
        named_user = conn.execute("SELECT id FROM users WHERE username = ?", (bootstrap_username,)).fetchone()
        if not admin_exists and named_user:
            conn.execute(
                "UPDATE users SET password_hash = ?, role = 'admin', is_active = 1 WHERE id = ?",
                (hash_password(bootstrap_password), named_user[0]),
            )
        elif not admin_exists:
            conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
                (bootstrap_username, hash_password(bootstrap_password)),
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


def current_user():
    user_id = session.get("user_id")
    if user_id is None:
        return None
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id, username, role FROM users WHERE id = ? AND is_active = 1",
            (user_id,),
        ).fetchone()
        return {"id": row[0], "username": row[1], "role": row[2]} if row else None
    finally:
        conn.close()


def verify_login(username, password):
    """Return the user's role if credentials are valid, else None."""
    role, _ = authenticate_login(username, password)
    return role["role"] if role else None


def authenticate_login(username, password):
    """Return ``(user, retry_after)`` using IP-and-identifier throttling."""
    normalized_username = _normalize_username(username)
    client_ip = request.remote_addr if has_request_context() else "local"
    throttle_key = _login_throttle_key(normalized_username, client_ip or "unknown")
    now = time.time()
    conn = connect()
    try:
        throttle = conn.execute(
            "SELECT failed_attempts, window_started, locked_until FROM login_throttle WHERE throttle_key = ?",
            (throttle_key,),
        ).fetchone()
        row = conn.execute(
            "SELECT id, password_hash, role, is_active FROM users WHERE username = ?",
            (normalized_username,),
        ).fetchone()
        locked = bool(throttle and throttle[2] > now)
        encoded = row[1] if row and row[3] and not locked else DUMMY_PASSWORD_HASH
        valid = verify_password(password or "", encoded)
        if row and row[3] and not locked and valid:
            conn.execute("BEGIN IMMEDIATE")
            throttle = conn.execute(
                "SELECT locked_until FROM login_throttle WHERE throttle_key = ?", (throttle_key,)
            ).fetchone()
            if throttle and throttle[0] > time.time():
                conn.rollback()
                return None, max(1, math.ceil(throttle[0] - time.time()))
            conn.execute("DELETE FROM login_throttle WHERE throttle_key = ?", (throttle_key,))
            conn.commit()
            return {"id": row[0], "username": normalized_username, "role": row[2]}, None
        if locked:
            retry_after = max(1, math.ceil(throttle[2] - now))
            return None, retry_after
        conn.execute("BEGIN IMMEDIATE")
        throttle = conn.execute(
            "SELECT failed_attempts, window_started, locked_until FROM login_throttle WHERE throttle_key = ?",
            (throttle_key,),
        ).fetchone()
        if throttle and throttle[2] > time.time():
            retry_after = max(1, math.ceil(throttle[2] - time.time()))
            conn.rollback()
            return None, retry_after
        now = time.time()
        window_started = throttle[1] if throttle and now - throttle[1] < 900 else now
        failed_attempts = (throttle[0] if throttle and window_started == throttle[1] else 0) + 1
        delay = min(3600, LOGIN_COOLDOWN_SECONDS * (2 ** max(0, failed_attempts - MAX_LOGIN_ATTEMPTS)))
        locked_until = now + delay if failed_attempts >= MAX_LOGIN_ATTEMPTS else 0
        conn.execute(
            """INSERT INTO login_throttle (throttle_key, failed_attempts, window_started, locked_until)
               VALUES (?, ?, ?, ?) ON CONFLICT(throttle_key) DO UPDATE SET
               failed_attempts=excluded.failed_attempts, window_started=excluded.window_started,
               locked_until=excluded.locked_until""",
            (throttle_key, failed_attempts, window_started, locked_until),
        )
        conn.commit()
        return None, math.ceil(delay) if locked_until else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def login_required(view_func):
    """Redirect to /login if there is no active session."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            session.clear()
            return redirect(url_for("login", next=request.path))
        session["username"] = user["username"]
        session["role"] = user["role"]
        return view_func(*args, **kwargs)
    return wrapped


def admin_required(view_func):
    """Block access unless the session role is 'admin'."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            session.clear()
            return redirect(url_for("login", next=request.path))
        if user["role"] != "admin":
            return render_template("forbidden.html"), 403
        return view_func(*args, **kwargs)
    return wrapped


def compliance_required(view_func):
    """Block access unless the session role is the pseudonymized compliance tier."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            session.clear()
            return redirect(url_for("login", next=request.path))
        if user["role"] != "compliance":
            return render_template("forbidden.html"), 403
        return view_func(*args, **kwargs)
    return wrapped
