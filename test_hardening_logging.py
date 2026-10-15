import builtins
import logging
import unittest
from unittest.mock import Mock, patch


class HardeningLoggingTest(unittest.TestCase):
    def test_background_simulation_tick_failure_is_logged(self):
        import app

        with patch("app._run_simulation_tick", side_effect=RuntimeError("tick failed")), \
                patch("app.time.sleep", side_effect=RuntimeError("stop loop")):
            with self.assertLogs("app", level=logging.WARNING) as captured:
                with self.assertRaisesRegex(RuntimeError, "stop loop"):
                    app._background_simulation_loop(1)
        self.assertIn("Background simulation tick failed: tick failed", captured.output[0])

    def test_database_inspection_failure_is_logged(self):
        import database

        with patch.object(database, "SQLCIPHER_AVAILABLE", True), \
                patch.object(database, "DB_PATH", "encrypted.db"), \
                patch.object(database, "LEGACY_DB_PATH", "legacy.db"), \
                patch("database.os.path.exists", side_effect=[True, False]), \
                patch("database.connect", side_effect=OSError("cannot inspect db")), \
                patch("database.os.remove"):
            with self.assertLogs("database", level=logging.WARNING) as captured:
                database._migrate_legacy_database()
        self.assertIn("Could not inspect existing encrypted database", captured.output[0])

    def test_llm_usage_log_write_failure_is_logged(self):
        import llm_advisor

        with patch.object(builtins, "open", side_effect=OSError("log is read-only")):
            with self.assertLogs("llm_advisor", level=logging.WARNING) as captured:
                llm_advisor._log_usage({"id": 1}, success=True)
        self.assertIn("Failed to append LLM usage log entry", captured.output[0])

    def test_launcher_startup_log_write_failure_is_logged(self):
        from desktop_app import server_launcher

        with patch.object(builtins, "open", side_effect=OSError("log is read-only")):
            with self.assertLogs("desktop_app.server_launcher", level=logging.WARNING) as captured:
                server_launcher._log_startup_error("startup failed")
        self.assertIn("Could not write backend startup error log", captured.output[0])

    def test_launcher_termination_failure_is_logged(self):
        from desktop_app import server_launcher

        process = Mock()
        process.poll.return_value = None
        process.terminate.side_effect = OSError("process unavailable")
        with patch.object(server_launcher, "_server_process", process):
            with self.assertLogs("desktop_app.server_launcher", level=logging.WARNING) as captured:
                server_launcher._stop_backend_process()
        self.assertIn("Could not terminate backend process", captured.output[0])


if __name__ == "__main__":
    unittest.main()
