# Non-Regression Verification Record

## 1. Purpose and result

This record covers the optional Windows failed-login-monitor addition to the already-modified InSighter Endpoint Sensor / Telemetry Collector (ES/TC) branch. The working tree contained uncommitted USB/filesystem functionality before the failed-login work began. That functionality was treated as protected existing work: it was not reset, cleaned, overwritten, or replaced.

**Result:** the automated suite increased from 322 passing tests before failed-login changes to 340 passing tests afterward. No test failed in the final suite. No existing feature was intentionally removed. The model algorithms and feature definitions were not changed. Physical USB and live Windows Security-log collection remain environment-limited and are not represented as fully verified.

## 2. Source-control preservation

| Item | Recorded state |
|---|---|
| Repository | `Jayyydrian/InSighter` |
| Branch | `feature/usb-filesystem-monitoring` |
| HEAD at task start | `090326a` (`es_tc_update`) |
| Starting worktree | Already dirty, containing earlier USB/filesystem code, tests, and docs |
| Final worktree | Still uncommitted; all prior changes retained and new files added |
| Commit/reset/clean | None performed |

The currently uncommitted tree combines work from different stages. In particular, files such as `sensor_agent.py`, `ingestion.py`, and `test_sensor_backend_integration.py` contain both pre-existing sensor/USB work and the later failed-login additions. Do not restore these files wholesale to `HEAD` to remove one feature.

## 3. Test baseline and final comparison

The baseline was run before failed-login implementation, against the worktree that already included the 322-test USB/filesystem-era state.

| Check | Baseline before this task's implementation | Final |
|---|---:|---:|
| Command | `EXPECTED_MIN_TESTS=322 python scripts/run_tests_ci.py` | `EXPECTED_MIN_TESTS=322 python scripts/run_tests_ci.py` |
| Tests passed | **322** | **340** |
| Failures | **0** | **0** |
| Errors | **0** | **0** |
| Skips | **0** | **0** |
| Focused optional-monitor/auth/sensor/backend tests | N/A | **49 passed** |
| Repository Ruff check | Baseline check not separately recorded | Passed |
| Python syntax compilation of changed implementation/test files | Baseline check not separately recorded | Passed |
| `git diff --check` | N/A | Passed |

The test runner's CI minimum was raised from 322 to 340 after the final test count. The full suite was run with the threshold explicitly set to 322; the runner still discovered and executed all 340 tests. No pre-existing test failure was found at the task baseline.

At the start of the broader USB/filesystem work, an earlier baseline was 308 tests passed. It is historical context, not the baseline for this failed-login change.

## 4. Protected behavior review

| Existing functionality | Final result | Verification / notes |
|---|---|---|
| Sensor startup and configuration | **PASS** | Sensor/config tests; packaged Windows smoke starts with optional monitors enabled and unavailable failed-login collection |
| Network telemetry | **PASS / partially live-verified** | Existing tests remain passing; counter collection tests use injected/mocked counters and do not establish attribution or live network behavior |
| Disk telemetry | **PASS / partially live-verified** | Existing tests remain passing; counters are host-wide and tested with mocks |
| Administrator authentication | **PASS** | Authentication and session tests pass; no changes to `auth.py` |
| Sensor authentication and backend ingestion | **PASS** | Token success/rejection, no-dashboard-session token ingest, validation and response tests pass |
| SQLite persistence | **PASS** | Integration tests validate persistence in isolated SQLite databases |
| Event-ID deduplication | **PASS** | Replay test verifies no duplicate log row; event IDs still share existing path |
| Isolation Forest | **PASS; unchanged** | No algorithm or feature-definition edit; incomplete sensor observations remain excluded by existing eligibility rules |
| One-Class SVM | **PASS; unchanged** | No algorithm or feature-definition edit; existing model tests pass |
| Role-based baselines | **PASS; unchanged** | No baseline or role-model logic edit; existing model suite passes |
| Dashboard | **PASS in automated tests** | Existing dashboard tests pass; USB/file activity remains a separate added page. Visual target-PC walkthrough was not performed |
| Retry behavior | **PASS in tests** | Sender/replay tests pass; queue remains bounded and memory-only |
| Graceful shutdown | **PASS** | Shutdown tests and packaged stop-file smoke pass |
| USB detection and filesystem monitoring (pre-existing addition) | **No regression detected** | Mocked volume tests and real temporary-directory filesystem tests pass; physical USB was not available |
| Windows 4625 collector | **Added; live collection unverified** | Mocked unit/integration behavior passes. Security log/audit policy access was denied by the test host |

No model source file, authentication source file, database schema migration, existing API response contract, or existing dashboard view was replaced for failed-login monitoring.

## 5. Database/API compatibility

Failed-login monitoring adds no new database table, database column, or migration. It reuses `logs.failed_logins` and the existing `logs.feature_status` metadata. `ingestion.py` marks the measurement supported only when the sensor reports status `available` and a nonnegative integer (excluding booleans); `0` is valid. Disabled, unavailable, not-configured, error, and malformed values remain SQL `NULL` and unsupported. This is additive metadata on an existing ingestion event and preserves the existing response envelope.

The earlier USB/filesystem work uses additive `endpoint_activity_events` and `ingested_event_ids` creation paths and does not put those events into `logs` or model input. The test report records the local database inspection and additive table behavior. No existing table or row is intentionally dropped.

## 6. Worktree file inventory

### 6.1 Files added for optional failed-login collection / QA

- `failed_login_monitor.py` — read-only Windows audit-policy/Security Event ID 4625 counter.
- `test_failed_login_monitor.py` — mocked policy, event, status, cursor, and matching tests.
- `SYSTEM_FEATURE_INVENTORY.md` — subsystem inventory and evidence-based status.
- `SYSTEM_TEST_REPORT.md` — complete test and QA report.
- `FAILED_LOGIN_MONITORING_DESIGN.md` — this feature's operating and implementation reference.
- `NON_REGRESSION_VERIFICATION.md` — this preservation and regression record.

### 6.2 Files modified to integrate failed-login support or update related records

- `sensor_agent.py` — optional monitor configuration, collection, and nonfatal status integration.
- `ingestion.py` — strict available/nonnegative-integer handling and `NULL` semantics for failed-login feature status.
- `config.json` — checked-in `failed_login_monitoring.enabled: false`.
- `test_sensor_agent.py` — optional setting and collector behavior coverage.
- `test_sensor_backend_integration.py` — backend `0` versus missing/invalid persistence coverage.
- `test_auth_security.py` — cooldown-expiry behavior test; `auth.py` itself was not modified.
- `README.md`, `ENDPOINT_SENSOR.md`, and `summary_es_tc.md` — operational/technical documentation updates.
- `.github/workflows/ci.yml` — minimum discovered-test count changed from 322 to 340.

These are worktree-level classifications based on the recorded task history; some files also contain earlier USB/filesystem work and must not be rolled back wholesale.

### 6.3 Pre-existing USB/filesystem work preserved in the same worktree

Modified existing files: `app.py`, `build_sensor.py`, `desktop_app/api_client.py`, `desktop_app/main_window.py`, `requirements.txt`, `PRODUCTION_READINESS_REPORT.md`, and overlapping sensor/backend/test/documentation files noted above.

New USB/filesystem files: `usb_monitor.py`, `filesystem_monitor.py`, `desktop_app/endpoint_activity_tab.py`, and `test_endpoint_activity.py`.

The status output also shows the current per-file dirty state. This list is not a claim that every combined file belongs to only one stage.

## 7. Verification details and residual risks

- Full suite: 322 passed at baseline; 340 passed at final; zero failures/errors/skips.
- Focused suite: 49 passed.
- Ruff, syntax compilation, and whitespace/diff check: passed.
- Isolated PyInstaller 6.22.3 one-file/no-console build and smoke: passed with external config, optional monitors enabled, failed-login collector unable to access audit policy, and stop-file shutdown. Token was not present in logs.
- The Windows host denied `Get-WinEvent` Security-log access with `UnauthorizedAccessException` and denied `auditpol` with privilege-not-held error 1314. A real Event ID 4625 could not be collected.
- Physical USB attach/remove, endpoint-to-Admin-PC connectivity, production HTTPS/WSGI, clean-machine package install, visual dashboard walk-through, and long-duration outage recovery were not performed.

The collector's inability to access Windows audit data is an explicitly reported environment limitation, not a test pass for live event extraction. Inaccessible data stays null and does not stop standard telemetry.

## 8. Safe rollback guidance

No commit was created, and the branch's original HEAD does not isolate the failed-login addition from the already-present uncommitted USB/filesystem implementation. **Do not use `git reset`, `git clean`, or `git checkout HEAD -- <shared-file>` to roll back this feature.** Those operations can destroy prior user work.

If the optional failed-login feature must be removed while preserving the USB/filesystem work, make a selective reverse patch/reviewed edit limited to:

1. Remove `failed_login_monitor.py` and `test_failed_login_monitor.py`.
2. Remove only the `failed_login_monitoring` configuration and `FailedLoginMonitor` integration/status/payload blocks from `config.json` and `sensor_agent.py`.
3. Remove only failed-login availability/value normalization from `ingestion.py`; retain endpoint-activity branches and additive schema behavior.
4. Remove only failed-login-related test cases from `test_sensor_agent.py`, `test_sensor_backend_integration.py`, and `test_auth_security.py`; preserve all pre-existing tests in those files.
5. Revert only the failed-login text and test-floor delta in docs/CI, retaining USB/filesystem content and prior test expectations.
6. Run the full suite, `ruff check .`, syntax compilation, and `git diff --check`; compare against the 322-test USB/filesystem baseline.

Because modified files contain interleaved work, the operator should inspect the exact diff and use a reviewed patch that targets those hunks. Do not delete the whole `SYSTEM_FEATURE_INVENTORY.md` or `SYSTEM_TEST_REPORT.md` if they remain useful records of the broader work; edit or archive those reports according to project policy.

## 9. Final conclusion

Automated regression checks found no break in the protected functionality. The optional failed-login collector is isolated, default-off, and does not alter dashboard authentication or model algorithms. Live Windows event collection and physical/cross-machine deployment remain unverified. No existing feature was intentionally removed, no database migration was introduced for failed-login telemetry, and all prior uncommitted USB/filesystem work was preserved.
