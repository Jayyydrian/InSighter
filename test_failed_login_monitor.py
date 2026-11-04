import json
import unittest
from unittest.mock import patch

from failed_login_monitor import (
    FailedLoginMonitor,
    _account_name_matches,
    _audit_failure_enabled,
)


AUDIT_POLICY = (
    "Machine Name,Policy Target,Subcategory,Subcategory GUID,Inclusion Setting\n"
    "HOST,System,Logon,{0CCE9215-69AE-11D9-BED3-505054503030},Failure\n"
)


class FakeCommandRunner:
    def __init__(self):
        self.audit_policy = AUDIT_POLICY
        self.latest_record_id = "100"
        self.query_result = {
            "latest_record_id": 110,
            "events": [
                {"record_id": 102, "target_user": "intern"},
                {"record_id": 105, "target_user": "another-user"},
                {"record_id": 108, "target_user": "INTERN"},
            ],
        }
        self.query_returncode = 0
        self.commands = []

    def __call__(self, command, timeout=10):
        self.commands.append(command)
        if command[0].endswith("auditpol.exe"):
            return _result(self.audit_policy, 0)
        if "FilterXPath" in command[-1]:
            return _result(json.dumps(self.query_result), self.query_returncode)
        return _result(self.latest_record_id, 0)


def _result(stdout="", returncode=0):
    class Result:
        pass

    result = Result()
    result.stdout = stdout
    result.stderr = ""
    result.returncode = returncode
    return result


class FailedLoginMonitorTests(unittest.TestCase):
    def setUp(self):
        self.runner = FakeCommandRunner()
        self.clock_value = 10.0
        self.monitor = FailedLoginMonitor(
            enabled=True,
            target_user="INTERN",
            command_runner=self.runner,
            monotonic=lambda: self.clock_value,
            windows=True,
        )
        self.auditpol_path = patch.object(
            self.monitor, "_auditpol_path", return_value="auditpol.exe"
        )
        self.auditpol_path.start()
        self.addCleanup(self.auditpol_path.stop)

    def test_counts_only_matching_target_account_and_advances_record_cursor(self):
        self.assertEqual(self.monitor.start(), "available")
        self.assertEqual(self.monitor.last_record_id, 100)

        self.clock_value = 40.0
        result = self.monitor.observe()

        self.assertEqual(result.count, 2)
        self.assertEqual(result.status, "available")
        self.assertEqual(result.observation_window_seconds, 30)
        self.assertEqual(self.monitor.last_record_id, 110)
        self.assertNotIn("another-user", repr(result))

    def test_successful_query_with_no_matches_is_zero_not_null(self):
        self.monitor.start()
        self.runner.query_result = {
            "latest_record_id": 115,
            "events": [
                {"record_id": 112, "target_user": "other-user"},
            ],
        }

        result = self.monitor.observe()

        self.assertEqual((result.count, result.status), (0, "available"))

    def test_disabled_and_non_windows_states_do_not_read_logs(self):
        disabled = FailedLoginMonitor(
            enabled=False,
            target_user="intern",
            command_runner=self.runner,
            windows=True,
        )
        self.assertEqual(disabled.observe().status, "disabled")
        self.assertEqual(self.runner.commands, [])

        unsupported = FailedLoginMonitor(
            enabled=True,
            target_user="intern",
            command_runner=self.runner,
            windows=False,
        )
        result = unsupported.observe()
        self.assertEqual((result.count, result.status), (None, "unavailable"))
        self.assertEqual(self.runner.commands, [])

    def test_failure_audit_policy_disabled_is_not_configured(self):
        self.runner.audit_policy = (
            AUDIT_POLICY.rsplit(",", 1)[0] + ",No Auditing\n"
        )

        result = self.monitor.start()

        self.assertEqual(result, "not_configured")
        self.assertEqual(len(self.runner.commands), 1)

    def test_unrecognized_audit_policy_fails_closed(self):
        self.assertIsNone(_audit_failure_enabled("machine,policy\n"))
        self.runner.audit_policy = (
            AUDIT_POLICY.rsplit(",", 1)[0] + ",Success\n"
        )

        self.assertEqual(self.monitor.start(), "not_configured")

    def test_security_log_access_failure_is_unavailable(self):
        self.runner.latest_record_id = ""
        self.assertEqual(self.monitor.start(), "unavailable")

    def test_query_error_keeps_count_null_and_retries_from_same_cursor(self):
        self.monitor.start()
        self.runner.query_returncode = 1

        failed = self.monitor.observe()

        self.assertEqual((failed.count, failed.status), (None, "error"))
        self.assertEqual(self.monitor.last_record_id, 100)

        self.runner.query_returncode = 0
        recovered = self.monitor.observe()
        self.assertEqual((recovered.count, recovered.status), (2, "available"))

    def test_log_clear_resets_cursor_without_fabricating_zero(self):
        self.monitor.start()
        self.runner.query_result = {"latest_record_id": 12, "events": []}

        result = self.monitor.observe()

        self.assertEqual((result.count, result.status), (None, "error"))
        self.assertEqual(self.monitor.last_record_id, 12)

    def test_account_match_preserves_process_account_attribution(self):
        self.assertTrue(_account_name_matches("intern", "DOMAIN\\intern"))
        self.assertTrue(_account_name_matches("user", "user@example.test"))
        self.assertFalse(_account_name_matches("other", "DOMAIN\\intern"))


if __name__ == "__main__":
    unittest.main()
