import unittest
from pathlib import Path

from scripts.run_tests_ci import (
    _discover_tests,
    _test_mutations,
    check_test_floor,
    parse_counts,
    parse_expected_minimum,
)

ROOT = Path(__file__).resolve().parent


class RunTestsCiScriptTests(unittest.TestCase):
    def test_parse_counts_includes_skips_and_failures(self):
        counts = parse_counts(
            "Ran 12 tests in 0.1s\nFAILED (failures=1, errors=2, skipped=3)\n"
        )
        self.assertEqual(
            counts,
            {"tests": 12, "skipped": 3, "failures": 1, "errors": 2},
        )

    def test_floor_passes_at_minimum_including_skipped_tests(self):
        counts = parse_counts("Ran 12 tests in 0.1s\nOK (skipped=3)\n")
        self.assertIsNone(check_test_floor(counts["tests"], 12))

    def test_floor_fails_below_minimum(self):
        message = check_test_floor(8, 9)
        self.assertEqual(message, "Discovered 8 tests; expected at least 9")

    def test_expected_minimum_must_be_present_and_integer(self):
        self.assertEqual(parse_expected_minimum("12"), 12)
        with self.assertRaisesRegex(ValueError, "EXPECTED_MIN_TESTS is required"):
            parse_expected_minimum(None)
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            parse_expected_minimum("twelve")

    def test_preexisting_dirty_entry_with_same_hash_passes(self):
        before = {" M existing.py": "before-hash"}
        after = {" M existing.py": "before-hash"}
        self.assertEqual(_test_mutations(before, after), [])

    def test_new_status_entry_fails(self):
        before = {" M existing.py": "same-hash"}
        after = {
            " M existing.py": "same-hash",
            "?? added.py": "added-hash",
        }
        self.assertEqual(_test_mutations(before, after), ["added.py"])

    def test_changed_hash_for_preexisting_entry_fails(self):
        before = {" M existing.py": "before-hash"}
        after = {" M existing.py": "after-hash"}
        self.assertEqual(_test_mutations(before, after), ["existing.py"])

    def test_runtime_files_and_pycache_are_ignored(self):
        before = {}
        after = {
            " M database.db": "new-db-hash",
            "?? database.db-journal": "journal-hash",
            " M llm_usage_log.jsonl": "new-log-hash",
            "?? package/__pycache__/module.pyc": "bytecode-hash",
        }
        self.assertEqual(_test_mutations(before, after), [])

    def test_runner_discovers_same_tests_as_plain_unittest_discovery(self):
        runner_suite = _discover_tests(ROOT)
        plain_suite = unittest.TestLoader().discover(
            start_dir=str(ROOT),
            pattern="test*.py",
            top_level_dir=str(ROOT),
        )
        modules = set()

        def collect_modules(suite):
            for test in suite:
                if isinstance(test, unittest.TestSuite):
                    collect_modules(test)
                else:
                    modules.add(test.__class__.__module__)

        collect_modules(runner_suite)
        self.assertIn("test_ci_scripts", modules)
        self.assertEqual(runner_suite.countTestCases(), plain_suite.countTestCases())


if __name__ == "__main__":
    unittest.main()
