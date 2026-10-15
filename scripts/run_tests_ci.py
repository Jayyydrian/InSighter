#!/usr/bin/env python3
"""Run the project's unittest suite and fail on repo mutations caused by tests."""

from __future__ import annotations

import os
import io
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRACKED_ALLOWED_MUTATIONS = {"database.db", "llm_usage_log.jsonl"}


def _git_status(repo_root: Path) -> set[str]:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=False,
        text=True,
        capture_output=True,
    )
    return {line for line in completed.stdout.splitlines() if line}


def _status_path(status_line: str) -> str:
    path = status_line[3:].strip()
    if " -> " in path:
        path = path.rsplit(" -> ", 1)[1]
    if path.startswith('"'):
        import ast

        path = ast.literal_eval(path)
    return path


def _is_ignored_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return (
        normalized in TRACKED_ALLOWED_MUTATIONS
        or normalized.startswith("database.db")
        or normalized.startswith("llm_usage_log.jsonl")
        or "__pycache__/" in normalized
        or normalized.endswith("/__pycache__")
        or normalized.startswith("__pycache__")
    )


def _content_hash(repo_root: Path, path: str) -> str | None:
    completed = subprocess.run(
        ["git", "hash-object", "--", path],
        cwd=repo_root,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _status_snapshot(repo_root: Path) -> dict[str, str | None]:
    return {
        line: _content_hash(repo_root, _status_path(line))
        for line in _git_status(repo_root)
        if not _is_ignored_path(_status_path(line))
    }


def _test_mutations(
    before: dict[str, str | None],
    after: dict[str, str | None],
) -> list[str]:
    unexpected = []
    before_lines = set(before)
    for line, content_hash in after.items():
        path = _status_path(line)
        if _is_ignored_path(path):
            continue
        if line not in before_lines:
            unexpected.append(path)
        elif before[line] != content_hash:
            unexpected.append(path)
    return sorted(set(unexpected))


def parse_counts(output: str) -> dict[str, int]:
    run_match = re.search(r"Ran (\d+) tests?", output)
    if run_match is None:
        raise ValueError("unittest output did not contain a test count")
    skipped_match = re.search(r"skipped=(\d+)", output)
    failures_match = re.search(r"failures=(\d+)", output)
    errors_match = re.search(r"errors=(\d+)", output)
    return {
        "tests": int(run_match.group(1)),
        "skipped": int(skipped_match.group(1)) if skipped_match else 0,
        "failures": int(failures_match.group(1)) if failures_match else 0,
        "errors": int(errors_match.group(1)) if errors_match else 0,
    }


def parse_expected_minimum(value: str | None) -> int:
    if value is None:
        raise ValueError("EXPECTED_MIN_TESTS is required and must be an integer")
    try:
        minimum = int(value)
    except ValueError as exc:
        raise ValueError(
            f"EXPECTED_MIN_TESTS must be an integer; received {value!r}"
        ) from exc
    if minimum < 0:
        raise ValueError(f"EXPECTED_MIN_TESTS must be non-negative; received {minimum}")
    return minimum


def check_test_floor(test_count: int, expected_minimum: int) -> str | None:
    if test_count < expected_minimum:
        return f"Discovered {test_count} tests; expected at least {expected_minimum}"
    return None


def _discover_tests(repo_root: Path):
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(repo_root), pattern="test*.py", top_level_dir=str(repo_root))
    return suite


def run_tests(repo_root: Path, env: dict[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    try:
        expected_minimum = parse_expected_minimum(env.get("EXPECTED_MIN_TESTS"))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    baseline = _status_snapshot(repo_root)
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(
        _discover_tests(repo_root)
    )
    report = output.getvalue()
    print(report, end="")
    counts = parse_counts(report)
    if not result.wasSuccessful():
        return 1

    floor_error = check_test_floor(counts["tests"], expected_minimum)
    if floor_error:
        print(floor_error, file=sys.stderr)
        return 1

    dirty = _test_mutations(baseline, _status_snapshot(repo_root))
    if dirty:
        print("Tracked files were modified by tests:")
        for name in dirty:
            print(f" - {name}")
        return 1
    return 0


def main() -> int:
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("INSIGHTER_DISABLE_BACKGROUND_SIM", "1")
    env.setdefault("INSIGHTER_LLM_WARMUP", "0")
    env.setdefault("INSIGHTER_SECRET_KEY", "ci-test-key")
    os.environ.update(env)
    return run_tests(REPO_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
