import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from desktop_app import server_launcher

_ORIGINAL_VERIFY_DIRECTORY_VISIBLE = server_launcher._verify_directory_visible_outside_python


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = 0

    def wait(self, timeout=None):
        self.waited = True
        return self.returncode


class ServerLauncherTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="insighter-launcher-")
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.database_path = self.root / "throwaway.db"
        self.environment = patch.dict(
            os.environ,
            {
                "INSIGHTER_DB_PATH": str(self.database_path),
                "INSIGHTER_PORT": "5051",
                "INSIGHTER_DB_KEY": "explicit-test-db-key",
                "INSIGHTER_SECRET_KEY": "explicit-test-key",
                "LOCALAPPDATA": str(self.root / "localappdata"),
                "USERPROFILE": str(self.root / "userprofile"),
                "QT_QPA_PLATFORM": "offscreen",
                "INSIGHTER_DISABLE_BACKGROUND_SIM": "1",
                "INSIGHTER_LLM_WARMUP": "0",
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.original_globals = (
            server_launcher.PORT,
            server_launcher.BASE_URL,
            server_launcher.LOG_FILE,
            server_launcher.SERVER_LOG,
            server_launcher._server_process,
            server_launcher.last_error,
        )
        server_launcher.LOG_FILE = str(self.root / "backend_startup.log")
        server_launcher.SERVER_LOG = server_launcher.LOG_FILE
        server_launcher._server_process = None
        server_launcher.last_error = ""
        self.addCleanup(self._restore_globals)
        visibility_check = patch.object(
            server_launcher, "_verify_directory_visible_outside_python"
        )
        visibility_check.start()
        self.addCleanup(visibility_check.stop)

    def _restore_globals(self):
        (
            server_launcher.PORT,
            server_launcher.BASE_URL,
            server_launcher.LOG_FILE,
            server_launcher.SERVER_LOG,
            server_launcher._server_process,
            server_launcher.last_error,
        ) = self.original_globals

    def _prepare_successful_fake_start(self, process):
        popen = MagicMock(return_value=process)
        patches = (
            patch.object(server_launcher, "_preflight_checks", return_value=None),
            patch.object(server_launcher, "_port_is_free", return_value=True),
            patch.object(server_launcher, "_healthy_backend", side_effect=[False, True]),
            patch.object(server_launcher.subprocess, "Popen", new=popen),
        )
        return patches, popen

    def test_child_exit_shows_log_tail_and_actionable_hint(self):
        process = FakeProcess(returncode=1)

        def spawn(*_args, **_kwargs):
            Path(server_launcher.LOG_FILE).write_text(
                "Traceback (most recent call last):\n"
                "RuntimeError: Set INSIGHTER_SECRET_KEY to a stable, randomly generated secret\n",
                encoding="utf-8",
            )
            return process

        with (
            patch.object(server_launcher, "_preflight_checks", return_value=None),
            patch.object(server_launcher, "_port_is_free", return_value=True),
            patch.object(server_launcher, "_healthy_backend", return_value=False),
            patch.object(server_launcher.subprocess, "Popen", side_effect=spawn),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertFalse(server_launcher.ensure_backend_running(timeout=1))

        self.assertIn("Set INSIGHTER_SECRET_KEY", server_launcher.last_error)
        self.assertIn("Hint: set INSIGHTER_SECRET_KEY", server_launcher.last_error)
        self.assertIn(server_launcher.LOG_FILE, server_launcher.last_error)
        self.assertIn("last lines:", server_launcher.last_error)

    def test_missing_secret_is_generated_once_persisted_and_never_logged(self):
        secret_path = self.root / "userprofile" / ".insighter" / "secret_key"
        os.environ.pop("INSIGHTER_SECRET_KEY", None)
        process = FakeProcess()
        patches, popen = self._prepare_successful_fake_start(process)
        output = io.StringIO()
        with patch.object(server_launcher, "_restrict_local_secret_permissions") as restrict:
            with contextlib.ExitStack() as stack:
                for item in patches:
                    stack.enter_context(item)
                stack.enter_context(contextlib.redirect_stdout(output))
                self.assertTrue(server_launcher.ensure_backend_running(timeout=1))
            key = os.environ["INSIGHTER_SECRET_KEY"]
            self.assertTrue(secret_path.is_file())
            self.assertEqual(secret_path.read_text(encoding="utf-8"), key)
            self.assertGreaterEqual(len(key), 60)
            self.assertEqual(popen.call_args.kwargs["env"]["INSIGHTER_SECRET_KEY"], key)
            self.assertEqual(restrict.call_count, 2)
            self.assertIn(str(secret_path), output.getvalue())
            self.assertNotIn(key, output.getvalue())
            self.assertNotIn(key, Path(server_launcher.LOG_FILE).read_text(encoding="utf-8"))

            os.environ.pop("INSIGHTER_SECRET_KEY")
            self.assertFalse(server_launcher._ensure_local_secret())
            self.assertEqual(os.environ["INSIGHTER_SECRET_KEY"], key)
            self.assertEqual(restrict.call_count, 4)

    @unittest.skipUnless(os.name == "nt", "icacls is available only on Windows")
    def test_missing_secret_creates_directory_before_restricting_it(self):
        user_profile = self.root / "new-userprofile"
        secret_dir = user_profile / ".insighter"
        secret_file = secret_dir / "secret_key"
        os.environ["USERPROFILE"] = str(user_profile)
        os.environ.pop("INSIGHTER_SECRET_KEY", None)
        calls = []

        def fake_icacls(args, **kwargs):
            self.assertEqual(args[0], "icacls")
            self.assertTrue(kwargs["env"]["INSIGHTER_DB_PATH"])
            self.assertTrue(kwargs["env"]["INSIGHTER_PORT"])
            path = Path(args[1])
            calls.append((path, path.is_dir()))
            if not path.exists():
                return subprocess.CompletedProcess(
                    args, 1, stdout="", stderr=f"{path}: The system cannot find the file specified."
                )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch.object(server_launcher.subprocess, "run", side_effect=fake_icacls):
            self.assertTrue(server_launcher._ensure_local_secret())

        self.assertTrue(secret_dir.is_dir())
        self.assertTrue(secret_file.is_file())
        self.assertEqual(
            calls,
            [
                (secret_dir, True),
                (secret_file, False),
            ],
        )
        self.assertEqual(os.environ["INSIGHTER_SECRET_KEY"], secret_file.read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "nt", "icacls is available only on Windows")
    def test_existing_secret_directory_without_key_file_is_restricted_then_populated(self):
        user_profile = self.root / "existing-userprofile"
        secret_dir = user_profile / ".insighter"
        secret_dir.mkdir(parents=True)
        secret_file = secret_dir / "secret_key"
        os.environ["USERPROFILE"] = str(user_profile)
        os.environ.pop("INSIGHTER_SECRET_KEY", None)
        calls = []

        def fake_icacls(args, **kwargs):
            self.assertEqual(args[0], "icacls")
            self.assertTrue(kwargs["env"]["INSIGHTER_DB_PATH"])
            self.assertTrue(kwargs["env"]["INSIGHTER_PORT"])
            path = Path(args[1])
            calls.append((path, path.is_dir()))
            if not path.exists():
                return subprocess.CompletedProcess(
                    args, 1, stdout="", stderr=f"{path}: The system cannot find the file specified."
                )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch.object(server_launcher.subprocess, "run", side_effect=fake_icacls):
            self.assertTrue(server_launcher._ensure_local_secret())

        self.assertTrue(secret_file.is_file())
        self.assertEqual(
            calls,
            [
                (secret_dir, True),
                (secret_file, False),
            ],
        )

    @unittest.skipUnless(os.name == "nt", "external visibility check is Windows-only")
    def test_external_visibility_failure_names_python_and_prevents_key_use(self):
        secret_dir = self.root / "userprofile" / ".insighter"
        secret_file = secret_dir / "secret_key"
        os.environ.pop("INSIGHTER_SECRET_KEY", None)

        def invisible_directory(args, **kwargs):
            self.assertEqual(args[0].lower(), "cmd.exe")
            self.assertTrue(kwargs["env"]["INSIGHTER_DB_PATH"])
            self.assertTrue(kwargs["env"]["INSIGHTER_PORT"])
            self.assertTrue(Path(args[-1]).is_dir())
            return subprocess.CompletedProcess(
                args, 1, stdout="", stderr="directory is not externally visible"
            )

        with (
            patch.object(server_launcher.subprocess, "run", side_effect=invisible_directory),
            patch.object(
                server_launcher,
                "_verify_directory_visible_outside_python",
                side_effect=_ORIGINAL_VERIFY_DIRECTORY_VISIBLE,
            ),
            patch.object(server_launcher, "_restrict_local_secret_permissions") as restrict,
        ):
            with self.assertRaises(RuntimeError) as error:
                server_launcher._ensure_local_secret()

        self.assertIn(sys.executable, str(error.exception))
        self.assertIn("not visible outside Python", str(error.exception))
        self.assertFalse(secret_file.exists())
        self.assertNotIn("INSIGHTER_SECRET_KEY", os.environ)
        restrict.assert_not_called()

    def test_default_key_path_uses_userprofile_not_localappdata(self):
        user_profile = self.root / "profile"
        local_app_data = self.root / "store-virtualized-localappdata"
        os.environ["USERPROFILE"] = str(user_profile)
        os.environ["LOCALAPPDATA"] = str(local_app_data)
        self.assertEqual(
            server_launcher._local_secret_path(),
            user_profile / ".insighter" / "secret_key",
        )
        self.assertNotEqual(
            server_launcher._local_secret_path(),
            local_app_data / "InSighter" / "secret_key",
        )

    def test_user_secret_wins_without_userprofile(self):
        os.environ.pop("USERPROFILE", None)
        os.environ["INSIGHTER_SECRET_KEY"] = "explicit-user-signing-key"
        with (
            patch.object(server_launcher, "_verify_directory_visible_outside_python") as verify,
            patch.object(server_launcher, "_restrict_local_secret_permissions") as restrict,
        ):
            self.assertFalse(server_launcher._ensure_local_secret())
        self.assertEqual(os.environ["INSIGHTER_SECRET_KEY"], "explicit-user-signing-key")
        verify.assert_not_called()
        restrict.assert_not_called()

    def test_missing_userprofile_reports_plain_error(self):
        os.environ.pop("USERPROFILE", None)
        os.environ.pop("INSIGHTER_SECRET_KEY", None)
        with self.assertRaisesRegex(
            RuntimeError,
            "USERPROFILE must be set to create the local InSighter signing key",
        ):
            server_launcher._ensure_local_secret()

    def test_empty_userprofile_reports_plain_error(self):
        os.environ["USERPROFILE"] = " "
        os.environ.pop("INSIGHTER_SECRET_KEY", None)
        with self.assertRaisesRegex(
            RuntimeError,
            "USERPROFILE must be set to create the local InSighter signing key",
        ):
            server_launcher._ensure_local_secret()

    def test_sqlcipher_requires_a_separate_database_key_before_secret_generation(self):
        secret_path = self.root / "userprofile" / ".insighter" / "secret_key"
        os.environ.pop("INSIGHTER_DB_KEY", None)
        os.environ.pop("INSIGHTER_SECRET_KEY", None)
        process = FakeProcess()
        with (
            patch.object(server_launcher, "_sqlcipher_available", return_value=True),
            patch.object(server_launcher, "_preflight_checks", return_value=None),
            patch.object(server_launcher, "_port_is_free", return_value=True),
            patch.object(server_launcher, "_healthy_backend", side_effect=[False, True]),
            patch.object(server_launcher, "_restrict_local_secret_permissions"),
            patch.object(server_launcher.subprocess, "Popen", return_value=process) as spawn,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertFalse(server_launcher.ensure_backend_running(timeout=1))

        spawn.assert_not_called()
        self.assertFalse(secret_path.exists())
        self.assertNotIn("INSIGHTER_SECRET_KEY", os.environ)
        self.assertIn("INSIGHTER_DB_KEY must be set", server_launcher.last_error)
        self.assertIn("Hint: set INSIGHTER_DB_KEY", server_launcher.last_error)

    def test_database_key_allows_sqlcipher_startup_to_continue(self):
        os.environ.pop("INSIGHTER_SECRET_KEY", None)
        process = FakeProcess()
        patches, popen = self._prepare_successful_fake_start(process)
        with (
            patch.object(server_launcher, "_sqlcipher_available", return_value=True),
            patch.object(server_launcher, "_restrict_local_secret_permissions"),
        ):
            with contextlib.ExitStack() as stack:
                for item in patches:
                    stack.enter_context(item)
                self.assertTrue(server_launcher.ensure_backend_running(timeout=1))
        self.assertEqual(
            popen.call_args.kwargs["env"]["INSIGHTER_DB_KEY"], "explicit-test-db-key"
        )
        self.assertIn("INSIGHTER_SECRET_KEY", popen.call_args.kwargs["env"])

    def test_acl_failure_prevents_generated_secret_file_creation(self):
        secret_path = self.root / "userprofile" / ".insighter" / "secret_key"
        os.environ.pop("INSIGHTER_SECRET_KEY", None)
        with (
            patch.object(server_launcher, "_sqlcipher_available", return_value=False),
            patch.object(
                server_launcher,
                "_restrict_local_secret_permissions",
                side_effect=RuntimeError("simulated ACL denial"),
            ),
            patch.object(server_launcher, "_preflight_checks", return_value=None),
            patch.object(server_launcher, "_port_is_free", return_value=True),
            patch.object(server_launcher, "_healthy_backend", return_value=False),
            patch.object(server_launcher.subprocess, "Popen") as spawn,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertFalse(server_launcher.ensure_backend_running(timeout=1))

        self.assertIn("simulated ACL denial", server_launcher.last_error)
        self.assertFalse(secret_path.exists())
        self.assertNotIn("INSIGHTER_SECRET_KEY", os.environ)
        spawn.assert_not_called()

    def test_acl_failure_on_generated_key_removes_unsecured_file(self):
        secret_path = self.root / "userprofile" / ".insighter" / "secret_key"
        os.environ.pop("INSIGHTER_SECRET_KEY", None)
        with patch.object(
            server_launcher,
            "_restrict_local_secret_permissions",
            side_effect=[None, RuntimeError("simulated file ACL denial")],
        ):
            with self.assertRaisesRegex(RuntimeError, "refusing to use it"):
                server_launcher._ensure_local_secret()

        self.assertFalse(secret_path.exists())
        self.assertNotIn("INSIGHTER_SECRET_KEY", os.environ)

    def test_existing_user_secret_is_never_overwritten(self):
        with patch.object(server_launcher, "_restrict_local_secret_permissions") as restrict:
            os.environ.pop("INSIGHTER_SECRET_KEY", None)
            self.assertTrue(server_launcher._ensure_local_secret())
            secret_path = server_launcher._local_secret_path()
            generated_key = secret_path.read_text(encoding="utf-8")

            os.environ["INSIGHTER_SECRET_KEY"] = "user-managed-key"
            self.assertFalse(server_launcher._ensure_local_secret())
        self.assertEqual(os.environ["INSIGHTER_SECRET_KEY"], "user-managed-key")
        self.assertEqual(secret_path.read_text(encoding="utf-8"), generated_key)
        self.assertEqual(restrict.call_count, 2)

    def test_healthy_backend_on_requested_port_is_reused(self):
        with (
            patch.object(server_launcher, "_healthy_backend", return_value=True),
            patch.object(server_launcher.subprocess, "Popen") as spawn,
            patch.object(server_launcher, "_preflight_checks") as preflight,
        ):
            self.assertTrue(server_launcher.ensure_backend_running())
        spawn.assert_not_called()
        preflight.assert_not_called()
        self.assertEqual(server_launcher.BASE_URL, "http://127.0.0.1:5051")

    def test_foreign_listener_uses_next_free_port_without_stopping_it(self):
        process = FakeProcess()
        with (
            patch.object(server_launcher, "_healthy_backend", side_effect=[False, True]),
            patch.object(server_launcher, "_port_is_free", side_effect=[False, True]),
            patch.object(server_launcher, "_next_free_port", return_value=5052),
            patch.object(server_launcher, "_preflight_checks", return_value=None),
            patch.object(server_launcher.subprocess, "Popen", return_value=process) as spawn,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertTrue(server_launcher.ensure_backend_running(timeout=1))
        args, kwargs = spawn.call_args
        self.assertEqual(args[0][0], sys.executable)
        self.assertEqual(kwargs["env"]["INSIGHTER_PORT"], "5052")
        self.assertEqual(os.environ["INSIGHTER_PORT"], "5052")
        self.assertFalse(process.terminated)
        self.assertFalse(process.killed)

    def test_child_uses_same_interpreter_repo_cwd_and_explicit_database_path(self):
        process = FakeProcess()
        popen_patches, popen = self._prepare_successful_fake_start(process)
        with contextlib.ExitStack() as stack:
            for item in popen_patches:
                stack.enter_context(item)
            self.assertTrue(server_launcher.ensure_backend_running(timeout=1))
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [sys.executable, server_launcher.APP_PY_PATH])
        self.assertEqual(kwargs["cwd"], str(server_launcher.PROJECT_ROOT))
        child_env = kwargs["env"]
        self.assertTrue(child_env["INSIGHTER_DB_PATH"])
        self.assertTrue(child_env["INSIGHTER_PORT"])
        self.assertEqual(os.environ["INSIGHTER_DB_PATH"], child_env["INSIGHTER_DB_PATH"])
        self.assertEqual(os.environ["INSIGHTER_PORT"], child_env["INSIGHTER_PORT"])
        self.assertEqual(
            Path(child_env["INSIGHTER_DB_PATH"]).resolve(), self.database_path.resolve()
        )

    def test_unset_database_setting_is_passed_visibly_not_left_to_app_default(self):
        os.environ.pop("INSIGHTER_DB_PATH", None)
        process = FakeProcess()
        captured = io.StringIO()
        popen_patches, popen = self._prepare_successful_fake_start(process)
        with contextlib.ExitStack() as stack:
            for item in popen_patches:
                stack.enter_context(item)
            stack.enter_context(contextlib.redirect_stdout(captured))
            self.assertTrue(server_launcher.ensure_backend_running(timeout=1))
        kwargs = popen.call_args.kwargs
        explicit_path = str((server_launcher.PROJECT_ROOT / "database.db").resolve())
        self.assertEqual(kwargs["env"]["INSIGHTER_DB_PATH"], explicit_path)
        self.assertIn(explicit_path, captured.getvalue())

    def test_startup_log_is_overwritten_with_timestamp_for_each_start(self):
        log_path = Path(server_launcher.LOG_FILE)
        log_path.write_text("stale log content\n", encoding="utf-8")
        server_launcher._begin_startup_log()
        content = log_path.read_text(encoding="utf-8")
        self.assertNotIn("stale log content", content)
        self.assertIn("InSighter backend startup: ", content)
        server_launcher._begin_startup_log()
        self.assertEqual(len(log_path.read_text(encoding="utf-8").splitlines()), 1)

    def test_child_alive_but_never_ready_times_out_and_is_stopped(self):
        process = FakeProcess()
        with (
            patch.object(server_launcher, "_preflight_checks", return_value=None),
            patch.object(server_launcher, "_port_is_free", return_value=True),
            patch.object(server_launcher, "_healthy_backend", return_value=False),
            patch.object(server_launcher.subprocess, "Popen", return_value=process),
            patch.object(server_launcher.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertFalse(server_launcher.ensure_backend_running(timeout=0.01))
        self.assertIn("did not start on port", server_launcher.last_error)
        self.assertIn(server_launcher.LOG_FILE, server_launcher.last_error)
        self.assertTrue(process.terminated)
        self.assertTrue(process.waited)

    def test_preflight_failures_are_plain_language_and_prevent_spawn(self):
        database_path = self.root / "throwaway.db"
        old_version = (3, 10, 9, "final", 0)
        with patch.object(server_launcher.sys, "version_info", old_version):
            message = server_launcher._preflight_checks(database_path)
        self.assertIn("Python 3.11 or newer", message)

        with (
            patch.object(server_launcher.sys, "version_info", (3, 11, 0, "final", 0)),
            patch.object(server_launcher.importlib.util, "find_spec", return_value=None),
        ):
            message = server_launcher._preflight_checks(database_path)
        self.assertIn("Required Python packages are missing", message)

        with (
            patch.object(server_launcher.sys, "version_info", (3, 11, 0, "final", 0)),
            patch.object(server_launcher.importlib.util, "find_spec", return_value=object()),
            patch.object(
                server_launcher.tempfile,
                "NamedTemporaryFile",
                side_effect=PermissionError("read-only folder"),
            ),
        ):
            message = server_launcher._preflight_checks(database_path)
        self.assertIn("folder is not writable", message)

        blocked_parent = self.root / "not-a-directory"
        blocked_parent.write_text("not a directory", encoding="utf-8")
        with (
            patch.object(server_launcher.sys, "version_info", (3, 11, 0, "final", 0)),
            patch.object(server_launcher.importlib.util, "find_spec", return_value=object()),
        ):
            message = server_launcher._preflight_checks(blocked_parent / "database.db")
        self.assertIn("database location is not writable", message)

        with (
            patch.object(server_launcher, "_preflight_checks", return_value="Python version unsupported."),
            patch.object(server_launcher, "_healthy_backend", return_value=False),
            patch.object(server_launcher, "_port_is_free", return_value=True),
            patch.object(server_launcher.subprocess, "Popen") as spawn,
        ):
            self.assertFalse(server_launcher.ensure_backend_running())
        self.assertIn("Python version unsupported.", server_launcher.last_error)
        self.assertIn(server_launcher.LOG_FILE, server_launcher.last_error)
        spawn.assert_not_called()

    def test_port_probe_uses_a_socket_bind(self):
        socket_context = MagicMock()
        with patch.object(server_launcher.socket, "socket", return_value=socket_context) as create:
            self.assertTrue(server_launcher._port_is_free(51234))
        create.assert_called_once_with(server_launcher.socket.AF_INET, server_launcher.socket.SOCK_STREAM)
        socket_context.__enter__.return_value.bind.assert_called_once_with(
            ("127.0.0.1", 51234)
        )


if __name__ == "__main__":
    unittest.main()
