"""Database connections with optional SQLCipher encryption and migration."""

import logging
import os
import shutil
import sqlite3 as plaintext_sqlite

logger = logging.getLogger(__name__)

try:
    from sqlcipher3 import dbapi2 as sqlite3
except ImportError:
    sqlite3 = plaintext_sqlite
    SQLCIPHER_AVAILABLE = False
else:
    SQLCIPHER_AVAILABLE = True

Row = sqlite3.Row
DB_PATH = os.environ.get("INSIGHTER_DB_PATH", "database.db.enc" if SQLCIPHER_AVAILABLE else "database.db")
LEGACY_DB_PATH = os.environ.get("INSIGHTER_LEGACY_DB_PATH", "database.db")


def _database_key():
    return os.environ.get(
        "INSIGHTER_DB_KEY",
        os.environ.get("INSIGHTER_SECRET_KEY", "insighter-local-development-db-key"),
    )


def _quote_key(value):
    return "'" + value.replace("'", "''") + "'"


def connect():
    """Open the configured database, authenticating SQLCipher when available."""
    conn = sqlite3.connect(DB_PATH)
    if SQLCIPHER_AVAILABLE:
        conn.execute(f"PRAGMA key = {_quote_key(_database_key())}")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    return conn


def _migrate_legacy_database():
    if not SQLCIPHER_AVAILABLE or DB_PATH == LEGACY_DB_PATH:
        return
    if os.path.exists(DB_PATH):
        try:
            existing = connect()
            has_tables = existing.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
            ).fetchone()
            existing.close()
            if has_tables or not os.path.exists(LEGACY_DB_PATH):
                return
        except Exception as exc:
            logger.warning("Could not inspect existing encrypted database at %s: %s", DB_PATH, exc)
        os.remove(DB_PATH)

    if not os.path.exists(LEGACY_DB_PATH):
        return

    source = plaintext_sqlite.connect(LEGACY_DB_PATH)
    target = connect()
    try:
        for statement in source.iterdump():
            if statement.startswith("BEGIN") or statement.startswith("COMMIT"):
                continue
            target.execute(statement)
        target.commit()
    finally:
        source.close()
        target.close()
    shutil.copy2(LEGACY_DB_PATH, f"{LEGACY_DB_PATH}.plaintext-backup")


def ensure_database():
    """Migrate an existing plaintext DB or create the configured database."""
    _migrate_legacy_database()
    conn = connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS role_baselines (
            sector TEXT,
            role TEXT,
            feature TEXT,
            value REAL,
            updated_by TEXT,
            updated_at TEXT,
            PRIMARY KEY (sector, role, feature)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS baseline_setup_status (
            sector TEXT PRIMARY KEY,
            completed INTEGER NOT NULL DEFAULT 0,
            completed_by TEXT,
            completed_at TEXT
        )
    """)
    conn.commit()
    conn.close()
