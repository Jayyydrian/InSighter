import atexit
from contextlib import contextmanager
import os
import tempfile

import database

TEST_SECRET = "test-only-not-a-secret"
_TEST_SECRET_NAMES = ("INSIGHTER_SECRET_KEY", "INSIGHTER_PII_SECRET")
_original_secrets = {
    name: os.environ.get(name)
    for name in _TEST_SECRET_NAMES
}


def _restore_secrets(originals):
    for name, value in originals.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _restore_import_environment():
    _restore_secrets(_original_secrets)


@contextmanager
def test_environment():
    originals = {name: os.environ.get(name) for name in _TEST_SECRET_NAMES}
    try:
        for name in _TEST_SECRET_NAMES:
            os.environ[name] = TEST_SECRET
        yield
    finally:
        _restore_secrets(originals)


for _name in _TEST_SECRET_NAMES:
    os.environ[_name] = TEST_SECRET

atexit.register(_restore_import_environment)


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
