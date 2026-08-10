"""SQLCipher-backed database connections and one-time plaintext migration."""

import os
import shutil
import sqlite3 as plaintext_sqlite

from sqlcipher3 import dbapi2 as sqlite3

Row = sqlite3.Row

DB_PATH = os.environ.get("INSIGHTER_DB_PATH", "database.db.enc")
LEGACY_DB_PATH = os.environ.get("INSIGHTER_LEGACY_DB_PATH", "database.db")


def _database_key():
    return os.environ.get(
        "INSIGHTER_DB_KEY",
        os.environ.get("INSIGHTER_SECRET_KEY", "insighter-local-development-db-key"),
    )


def _quote_key(value):
    return "'" + value.replace("'", "''") + "'"


def connect():
    """Open the encrypted database and authenticate the SQLCipher key."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(f"PRAGMA key = {_quote_key(_database_key())}")
    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    return conn


def _migrate_legacy_database():
    if os.path.exists(DB_PATH):
        try:
            existing = connect()
            has_tables = existing.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
            ).fetchone()
            existing.close()
            if has_tables or not os.path.exists(LEGACY_DB_PATH):
                return
        except Exception:
            pass
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
    """Migrate an existing plaintext DB, or create the encrypted DB."""
    _migrate_legacy_database()
    conn = connect()
    conn.close()