# InSighter System Test and Quality Assurance Report

## 1. Executive result

**Overall readiness: controlled demonstration / restricted pilot only; not production-ready.**

The full automated suite passed after implementation, and an isolated Windows PyInstaller executable started with both optional endpoint monitors enabled, handled inaccessible Windows audit permissions without terminating the sensor, and shut down using the stop file. Authentication, API contracts, SQLite persistence/deduplication, model missing-data behavior, and dashboard paths have automated coverage.

The most important unverified items are live Security Event ID 4625 collection, physical USB attach/remove behavior, a separate endpoint-to-Admin-PC network path, production HTTPS/WSGI deployment, and clean-machine packaging. Local Security-log collection was blocked by Windows permissions; this is reported as unavailable, not as a zero failed-login count.

## 2. Scope and architecture

The Windows sensor collects a host/process identity, local sampling hour/off-hours, machine-wide network and disk byte deltas, and optional removable-volume/file-operation events. An optional failed-login collector checks Windows Logon failure audit policy and counts new Security Event ID 4625 records matching the sensor process account. Events use the existing shared bearer-token `POST /api/ingest` route. `ingestion.py` normalizes and validates, applies the existing event-ID deduplication path, encrypts non-identity source payloads, and stores standard events in `logs`; USB/filesystem events are stored separately in `endpoint_activity_events`. The PyQt administrator client reads the existing APIs and the new admin-only endpoint-activity API.

No endpoint telemetry depends on the monitored user having an InSighter dashboard login. Dashboard authentication and its login throttle remain a separate control and event source.

## 3. Test environment and baseline

| Item | Evidence |
|---|---|
| Repository / branch | `Jayyydrian/InSighter`, `feature/usb-filesystem-monitoring` |
| Git HEAD before and after this task | `090326a` (`es_tc_update`); no commit was created |
| Initial worktree | Already dirty with the earlier USB/filesystem implementation and documentation changes. Those user/worktree changes were preserved. |
| Operating system | Windows 11, build `10.0.26200` |
| Python / packaging | Python 3.12.6; PyInstaller 6.22.3 |
| Baseline command | `EXPECTED_MIN_TESTS=322 python scripts/run_tests_ci.py` |
| Pre-change baseline | **322 passed, 0 failures, 0 errors, 0 skips** |
| Final command | `EXPECTED_MIN_TESTS=322 python scripts/run_tests_ci.py` |
| Final result | **340 passed, 0 failures, 0 errors, 0 skips** |
| Focused suite | `python -m unittest -v test_failed_login_monitor test_sensor_agent.SensorConfigurationTests test_sensor_agent.SensorTelemetryTests test_sensor_backend_integration.SensorBackendIntegrationTests test_auth_security.AuthSecurityTest` — **49 passed** |
| Static checks | `ruff check .`, changed-file `py_compile`, and `git diff --check` — all passed |
| CI test floor | `.github/workflows/ci.yml` raised to 340 after final discovery count |

An earlier historical baseline before the USB/filesystem changes was 308 tests passed. The baseline for this task is the tested 322-test worktree before optional failed-login code was changed.

## 4. Test-case results

| Scenario | Expected | Actual / evidence | Result |
|---|---|---|---|
| Administrator correct/incorrect credentials | Valid login works; invalid credentials fail | `test_auth_security.py` exercises credential verification and login routes with temporary SQLite accounts | **Verified locally** |
| Three failures and fourth attempt during cooldown | Third consecutive failure starts 60-second cooldown; another attempt is rejected | `MAX_LOGIN_ATTEMPTS = 3`, `LOGIN_COOLDOWN_SECONDS = 60`; throttle is stored under a hash of normalized username and remote IP; tests verify cooldown response | **Verified** |
| Login after cooldown and successful reset | Valid login after expiry succeeds; counter clears | Expiry test moves persisted `locked_until` into the past, then verifies valid login and throttle-row deletion; successful-login reset has a separate test | **Verified by simulated expiry**, not a 60-second wait |
| Session/logout/protected routes/CSRF | Login rotates session; logout revokes; unauthenticated/invalid mutations are rejected | Existing `test_auth_security.py`, `test_ingest_error_handling.py`, and web-contract tests pass | **Verified in test client** |
| Monitored user without dashboard account | Sensor can ingest using sensor credentials with no dashboard session | Sensor integration test verifies `/api/whoami` returns unauthenticated redirect, then a token-authenticated `/api/ingest` succeeds | **Verified in local Flask test client**; not deployed to a separate endpoint |
| Network and disk counters | Valid deltas and status; no false attribution | Counter math, unavailable/reset cases, and event formatting tested with injected/mocked counters | **Partially verified**; no live workload measurement, counters remain host-wide |
| USB connect/disconnect | Supported removable volumes generate events and persist | Drive classification and volume-difference tests use mocked drive types; backend validates and stores resulting event shape | **Partially verified**; no physical USB attach/remove |
| Filesystem operations | File create/modify/delete/rename counts, directories excluded, no paths retained | Real watchdog observer exercised against a temporary directory; privacy/path-rejection and table persistence covered in tests | **Verified on a temporary local directory**; not on physical USB media |
| Failed-login collector enabled / policy available | Count only matching target account; `0` is distinct from null | Mocked `auditpol`/PowerShell responses test cursor, matching, zero, reset, and errors; backend test persists `0` as supported | **Unit/integration verified with mocks only**; live event collection blocked |
| Failed-login collector inaccessible | Sensor and other monitors continue; count is null | Unit tests cover unavailable/error; packaged smoke enabled both optional monitors and logged audit-policy status `unavailable` without aborting | **Verified for unavailable startup path** |
| Failed-login disabled | Other sensor collection remains unchanged, count null | Default/omitted config tests and sensor event tests pass; checked-in config sets false | **Verified** |
| Model with missing data | No missing value becomes zero; incomplete row is excluded | Existing model calibration tests and backend integration assert `NULL`/unsupported values and unscoreable sensor rows; model feature list untouched | **Verified** |
| Retry and interruption | Transient failure uses bounded backoff and avoids duplicate storage | EventSender retry/queue and event-ID replay tests pass; packaged smoke used an unreachable loopback backend and exercised retry/logging | **Partially verified**; no prolonged outage/recovery replay or restart durability test |
| DB deduplication/persistence | Duplicate is acknowledged without second row | Flask integration test posts same ID twice and checks insert count/one row; activity events tested in separate table | **Verified for duplicate in one app process**; restart persistence not independently tested |
| Admin dashboard and activity page | Existing pages remain available; activity events appear to admin | Navigation/widget tests and admin API endpoint tests pass; `MainWindow` refreshes the active page on a 5-second timer | **Automated verification passed**; no visual target-PC walkthrough |
| PyInstaller/external config | Executable reads adjacent config and runs optional monitor code | Isolated one-file/no-console build in a temporary directory; external config enabled both optional monitors; stop-file exit succeeded | **Verified on build host**; clean PC without Python unavailable |
| Cross-computer Wi-Fi/hotspot | Sensor can reach Admin PC over deployed LAN | No physical hotspot, firewall, second computer, or production reverse proxy was available | **Not tested** |

## 5. Database and model findings

The inspected local SQLite database schema includes `logs` (including `feature_status`), `users`, `auth_sessions`, `login_throttle`, `audit_log`, `deployment_config`, `role_baselines`, and `baseline_setup_status`. That local file did not yet contain `endpoint_activity_events` or `ingested_event_ids`; the ingestion code creates them lazily with `CREATE TABLE IF NOT EXISTS` when used. Integration tests exercise additive table creation and duplicate behavior in isolated databases. No database rows were dropped or existing columns renamed for optional failed-login collection.

There is **no DDL change** for failed-login monitoring. It reuses existing `logs.failed_logins` and `logs.feature_status`. Only a count marked available and represented by a nonnegative integer is persisted as a measured value. Disabled/unavailable/not-configured/error values remain SQL `NULL`. The model still expects exactly five features and filters out any record missing a genuinely supported feature. Even when failed-logins is available, endpoint rows remain unscoreable because `files_accessed` is unsupported and network transfer is host-wide.

The current database code uses SQLCipher only when `sqlcipher3` is installed; otherwise it falls back to regular SQLite. A production encrypted-database configuration was not tested.

## 6. Authentication findings

`auth.py` implements 600,000-iteration salted PBKDF2-HMAC-SHA256 password hashes and constant-time comparisons. The login throttle key is a SHA-256 digest of normalized username and client IP. Failures are retained for a 15-minute window. The third failure locks for 60 seconds; further failed attempts after lock expiry increase the delay exponentially, capped at one hour. Correct login deletes the throttle row. This control is database-backed and survives normal backend process restarts, although an actual restart test was not performed. Login outcomes are written to `audit_log`; dashboard failures are not transformed into endpoint records or the endpoint `failed_logins` feature.

The throttle is per username/IP pair, not a global username lockout; networks/proxies that produce different `remote_addr` values may have separate keys. The system does not trust forwarded IP headers in this path. This task did not attempt security penetration testing or modify `auth.py`.

## 7. Optional failed-login collection

`failed_login_monitor.py` is opt-in (`failed_login_monitoring.enabled`, default false). It queries audit policy read-only and uses PowerShell `Get-WinEvent` for new Event ID 4625 records. The sensor does not configure audit policy or elevate itself. It compares `TargetUserName` only with the account running the sensor and transmits an aggregate count, not target account names or event XML. The bookmark is in memory only, initializes to the newest log record to avoid historical replay, and downtime is not backfilled.

Live verification was blocked on this host: `Get-WinEvent -LogName Security -MaxEvents 1` returned `UnauthorizedAccessException`; `auditpol.exe /get /subcategory:Logon /r` returned privilege-not-held error 1314. This means the environment cannot establish actual audit policy or read Security events. The collector correctly reported `unavailable` during the packaged optional-monitor smoke and continued the sensor. It would be inaccurate to claim that a real Windows failed logon was captured.

## 8. Security, privacy, and deployment findings

- Sensor bearer token is a shared credential of at least 32 characters, read from an environment variable and accepted only for sensor ingestion. It is not a per-device credential. Tests and package smoke verified that the token was absent from logs.
- The example transport is HTTP; the sensor supports HTTPS with certificate validation, but no production TLS terminator or WSGI server is delivered. HTTP can expose/replay the shared token.
- The Flask app defaults to loopback; binding to a LAN interface requires explicit configuration and firewall restriction. Do not expose the development server publicly.
- The sensor queues events only in bounded memory; restart/shutdown loses unsent events. Queue overflow drops oldest events.
- Standard non-identity event payloads are encrypted through the existing Fernet path. This does not guarantee encrypted SQLite storage. Endpoint username remains available in plaintext for scoring/display.
- USB/filesystem events retain drive root and operation counts, not path/name/content. This feature does not capture read/open activity or prove transfer/exfiltration.
- Failed-login event target identities/details are not sent or persisted by the optional collector; a service-account process only counts matching that account.
- Dashboard APIs for endpoint activity remain admin-only. Existing dashboard session and CSRF controls were not changed.

## 9. Issues during verification and remaining limitations

The first focused run of the new collector tests had four failures because the fake command runner treated both the bootstrap query and incremental query as the same command. The test fake was corrected to distinguish `FilterXPath`; the rerun passed all 49 focused tests. This was a test-harness issue, not a product-code failure.

An initial packaging attempt could not run because PyInstaller was absent from the active virtual environment. PyInstaller 6.22.3 was installed in that environment, without changing project runtime dependencies, and the isolated package build/smoke then passed. No generated package/build files were left in the repository by the final smoke.

Not verified: real Security-log event extraction, physical USB attach/remove, separate-machine deployment, real Wi-Fi/hotspot/firewall behavior, production HTTPS, clean-machine installation, long outage recovery, queue saturation, sustained load, and visual E2E on the Admin PC.

## 10. Overall assessment

| Subsystem | Verdict |
|---|---|
| Administrator authentication/cooldown | **Verified by automated tests; no auth implementation changes** |
| Endpoint token ingestion without dashboard session | **Verified locally; cross-computer deployment not tested** |
| Standard sensor network/disk sampling | **Partially verified with mocked counters** |
| USB detection | **Partially verified; physical hardware unavailable** |
| Filesystem counting/privacy | **Verified on temporary directory; physical USB use untested** |
| Optional failed-login collection | **Implemented, default disabled; live collection blocked by permissions/policy verification** |
| Backend/database/deduplication | **Verified by local integration tests; backend restart scenario not independently run** |
| Existing ML feature contract/missing-data behavior | **Verified; unchanged** |
| Admin dashboard | **Automated API/widget coverage; no visual target-device check** |
| Production readiness | **Not ready for unrestricted deployment** |

Before demonstration, configure an isolated test network, confirm Admin PC address/firewall/token handling, walk through the dashboard visually, and—only if authorized—test failed-logon collection on a Windows endpoint where audit failure policy and Security-log access are explicitly configured.
