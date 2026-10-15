import os
import tempfile

import database


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
