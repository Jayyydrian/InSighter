import os
import tempfile

import database

os.environ.setdefault("INSIGHTER_SECRET_KEY", "test-only-signing-key-32-bytes-minimum")
os.environ.setdefault("INSIGHTER_PII_SECRET", "test-only-pii-key-32-bytes-minimum")


_database_directory = None


def isolate_database():
    global _database_directory
    if _database_directory is None:
        _database_directory = tempfile.TemporaryDirectory(prefix="insighter-tests-")

    database_path = os.path.join(_database_directory.name, "test.db")
    os.environ["INSIGHTER_DB_PATH"] = database_path
    database.DB_PATH = database_path
    return database_path


def temporary_database_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as database_file:
        return database_file.name


def create_test_user(username, password, role):
    from auth import hash_password

    normalized_username = username.strip().casefold()
    conn = database.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO users (username, password_hash, role, is_active) VALUES (?, ?, ?, 1)",
            (normalized_username, hash_password(password), role),
        )
        conn.commit()
        return conn.execute("SELECT id FROM users WHERE username = ?", (normalized_username,)).fetchone()[0]
    finally:
        conn.close()
