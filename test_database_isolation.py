import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from test_support import assert_database_isolated, isolate_database


class DatabaseIsolationTests(unittest.TestCase):
    def test_import_without_configured_path_assigns_a_temporary_database(self):
        project_root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix="database-isolation-child-") as directory:
            root = Path(directory)
            profile = root / "profile"
            profile.mkdir()
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("INSIGHTER_")
            }
            environment.update(
                {
                    "INSIGHTER_PORT": "58139",
                    "USERPROFILE": str(profile),
                }
            )
            script = (
                "import os\n"
                "from pathlib import Path\n"
                "import database\n"
                "import test_support\n"
                "assert 'INSIGHTER_DB_PATH' in os.environ\n"
                "assert Path(database.DB_PATH).resolve() != "
                "(Path(test_support.__file__).resolve().parent / 'database.db').resolve()\n"
                "test_support.assert_database_isolated()\n"
            )
            process = subprocess.run(
                [sys.executable, "-c", script],
                cwd=project_root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)

    def test_test_support_uses_a_non_repository_database(self):
        database_path = Path(isolate_database()).resolve()
        repository_database = (Path(__file__).resolve().parent / "database.db").resolve()
        self.assertNotEqual(database_path, repository_database)
        assert_database_isolated()

    def test_guard_rejects_the_repository_database(self):
        repository_database = Path(__file__).resolve().parent / "database.db"
        with patch.object(database, "DB_PATH", str(repository_database)):
            with self.assertRaisesRegex(
                RuntimeError,
                "Tests must not use the repository database.db",
            ):
                assert_database_isolated()


if __name__ == "__main__":
    unittest.main()
