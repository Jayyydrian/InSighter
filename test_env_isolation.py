import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_support
from privacy import hash_identifier
from test_support import test_environment


class EnvironmentIsolationTests(unittest.TestCase):
    def test_app_imports_when_ambient_signing_key_is_unset_or_empty(self):
        project_root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix="insighter-env-import-") as directory:
            root = Path(directory)
            script = (
                "import os, socket, sys\n"
                "from pathlib import Path\n"
                "root = Path(sys.argv[1])\n"
                "os.environ['INSIGHTER_DB_PATH'] = str(root / 'test.db')\n"
                "sock = socket.socket(); sock.bind(('127.0.0.1', 0))\n"
                "os.environ['INSIGHTER_PORT'] = str(sock.getsockname()[1]); sock.close()\n"
                "os.environ['USERPROFILE'] = str(root / 'profile')\n"
                "os.environ['INSIGHTER_DISABLE_BACKGROUND_SIM'] = '1'\n"
                "os.environ['INSIGHTER_LLM_WARMUP'] = '0'\n"
                "os.environ['QT_QPA_PLATFORM'] = 'offscreen'\n"
                "import test_support\n"
                "assert os.environ['INSIGHTER_SECRET_KEY'] == test_support.TEST_SECRET\n"
                "assert os.environ['INSIGHTER_PII_SECRET'] == test_support.TEST_SECRET\n"
                "import app\n"
            )
            for signing_key in (None, ""):
                with self.subTest(signing_key="unset" if signing_key is None else "empty"):
                    environment = os.environ.copy()
                    environment.pop("INSIGHTER_SECRET_KEY", None)
                    environment.pop("INSIGHTER_PII_SECRET", None)
                    if signing_key is not None:
                        environment["INSIGHTER_SECRET_KEY"] = signing_key
                    process = subprocess.run(
                        [sys.executable, "-c", script, str(root)],
                        cwd=project_root,
                        env=environment,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(
                        process.returncode,
                        0,
                        msg=f"App import failed (stderr omitted to avoid env leakage): "
                        f"{bool(process.stderr)}",
                    )

    def test_pseudonym_is_stable_and_secret_specific(self):
        with patch.dict(
            os.environ,
            {
                "INSIGHTER_SECRET_KEY": "test-only-not-a-secret",
                "INSIGHTER_PII_SECRET": "test-only-not-a-secret",
            },
        ):
            self.assertEqual(hash_identifier("fixed-user"), "b33182f74ea7130744303310")

        with patch.dict(
            os.environ,
            {
                "INSIGHTER_SECRET_KEY": "test-only-not-a-secret",
                "INSIGHTER_PII_SECRET": "test-only-other-value",
            },
        ):
            self.assertNotEqual(hash_identifier("fixed-user"), "b33182f74ea7130744303310")

    def test_test_environment_restores_original_values(self):
        for original in (None, "", "test-only-other-value"):
            with self.subTest(original_state="unset" if original is None else "set"):
                with patch.dict(os.environ, {}, clear=False):
                    if original is None:
                        os.environ.pop("INSIGHTER_SECRET_KEY", None)
                    else:
                        os.environ["INSIGHTER_SECRET_KEY"] = original
                    os.environ.pop("INSIGHTER_PII_SECRET", None)

                    with test_environment():
                        self.assertEqual(
                            os.environ["INSIGHTER_SECRET_KEY"],
                            test_support.TEST_SECRET,
                        )
                        self.assertEqual(
                            os.environ["INSIGHTER_PII_SECRET"],
                            test_support.TEST_SECRET,
                        )

                    if original is None:
                        self.assertNotIn("INSIGHTER_SECRET_KEY", os.environ)
                    else:
                        self.assertEqual(os.environ["INSIGHTER_SECRET_KEY"], original)
                    self.assertNotIn("INSIGHTER_PII_SECRET", os.environ)


if __name__ == "__main__":
    unittest.main()
