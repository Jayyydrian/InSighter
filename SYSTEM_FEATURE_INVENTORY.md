# InSighter System Feature Inventory

## Assessment scope

This inventory records the implementation present on branch `feature/usb-filesystem-monitoring` at Git HEAD `090326a` plus the uncommitted changes listed in [NON_REGRESSION_VERIFICATION.md](./NON_REGRESSION_VERIFICATION.md). It distinguishes automated tests from live, physical, and cross-machine verification. A feature is not labelled verified merely because a route, class, or table exists.

### Status definitions

- **VERIFIED**: the behavior was exercised by an automated or repeatable local test.
- **PARTIALLY VERIFIED**: some behavior is tested, but important real-world behavior or scope remains untested.
- **IMPLEMENTED, NOT VERIFIED**: implementation exists but its actual runtime behavior was not demonstrated.
- **INCOMPLETE**: implementation exists but only covers a constrained subset of the described function.
- **NOT IMPLEMENTED**: no functional implementation exists.
- **BLOCKED**: verification requires hardware, permissions, policy, or environment not available here.

## Architecture summary

```text
Authorized endpoint
  sensor_agent.py / insighter-sensor.exe
    - host/process identity and machine-wide psutil counters
    - opt-in failed_login_monitor.py (Windows Security Event ID 4625)
    - opt-in usb_monitor.py + filesystem_monitor.py
    - bounded in-memory sender queue and shared bearer token
                    |
                    v
Flask app.py: POST /api/ingest
  - existing endpoint authentication and CSRF boundary
  - ingestion.py validation, normalization, deduplication
  - privacy.py sanitization and encrypted source payload
  - SQLite logs / ingested_event_ids / endpoint_activity_events
                    |
                    v
PyQt6 administrator dashboard
  - existing Overview, Sessions, Profiles, Alerts, and system views
  - separate USB & File Activity page
```

Sensor ingestion does not require the monitored process account to have a dashboard account or session. The endpoint must be assigned a role/category accepted by the active sector, and the sensor must possess the configured shared ingestion token.

## Feature inventory

### Authentication, authorization, and audit

| Feature | Implementation | Dependencies / data | Status and evidence |
|---|---|---|---|
| Password storage and verification | `auth.py`: salted PBKDF2-HMAC-SHA256 with 600,000 iterations; constant-time digest comparison | Python `hashlib`, SQLite `users` | **VERIFIED** by `test_auth_security.py` password/hash tests. |
| Dashboard login and account normalization | `app.py` `/login`; `auth.authenticate_login()` normalizes username | Flask session, SQLite user table | **VERIFIED** by valid/invalid login and session tests. |
| Three-attempt login throttle | `auth.py`: keyed by normalized username and `request.remote_addr`; 15-minute attempt window | Persistent `login_throttle` table | **VERIFIED** by the three-failure/cooldown tests. Three failures trigger 60 seconds; subsequent failed attempts after expiry increase backoff exponentially, capped at one hour. |
| Cooldown expiry and successful reset | Successful authentication deletes the throttle row; expired lock no longer blocks a valid login | SQLite throttle row and system clock | **VERIFIED** by tests for successful reset and an expired timestamp. Expiry was simulated by setting the persisted `locked_until` in the past; a 60-second wall-clock wait was not performed. |
| Login audit trail | `app.py` calls `auth.log_action()` for `LOGIN_SUCCESS`, `LOGIN_FAILED`, `LOGIN_COOLDOWN`, and `LOGOUT` | `audit_log` table; submitted username/action/detail, not password | **VERIFIED** for call paths through tests; no independent audit-log forensic review was performed. These records are dashboard authentication events, not endpoint events. |
| Server-side sessions | `DatabaseSessionInterface`, hashed session ID, 30-minute idle and 8-hour absolute lifetimes; session rotates after login | SQLite `auth_sessions`; secure/HttpOnly/SameSite=Lax cookie settings | **PARTIALLY VERIFIED** by session rotation, cookie, logout revocation, and protected-route tests. Long-duration expiry was not waited out in real time. |
| CSRF | `app.enforce_csrf()` requires session CSRF token for unsafe requests; only a valid sensor token to `/api/ingest` uses the sensor exception | Flask session and constant-time comparisons | **VERIFIED** by auth and ingestion tests. |
| Role access | `admin_required`, `compliance_required`, `login_required`; current role is reloaded from database | Flask session, SQLite `users` | **VERIFIED** for admin-only endpoint-activity access, unauthenticated access, and role changes in tests. |
| Sensor bearer-token authentication | `app._sensor_token_authorized()` requires a configured token of at least 32 characters and uses `secrets.compare_digest()` | `INSIGHTER_SENSOR_API_TOKEN`; shared token | **VERIFIED** by valid/invalid token tests. This is not per-device identity or dashboard authorization. |

### Endpoint sensor and telemetry

| Feature | Implementation | Dependencies / data | Status and evidence |
|---|---|---|---|
| External configuration and validation | `sensor_agent.load_config()`; `config.json` beside script/executable | JSON, environment token | **VERIFIED** by config validation tests. Missing optional settings default off. |
| Independent endpoint identity | `endpoint_identity()` uses process account and hostname | `getpass`, `socket`, `platform` | **VERIFIED** by identity tests. A service account may not identify the interactive user. |
| Network counters | `psutil.net_io_counters(pernic=False)` sent/received deltas summed to MiB | `psutil`; host-wide counters | **PARTIALLY VERIFIED**: counter math and unavailable/reset behavior tested with mocks; real changing traffic was not measured. No per-user/process attribution. |
| Disk counters | `psutil.disk_io_counters()` read/write byte deltas in encrypted supplemental payload | `psutil`; host-wide counters | **PARTIALLY VERIFIED**: math/status tested with mocks; no live disk workload verification. Not converted to file counts. |
| Sampling time and off-hours | Local sampling hour; off-hours is before 06:00 or after 21:00; event timestamp UTC | Local clock and UTC system clock | **VERIFIED** by event construction tests; timezone/DST behavior was not separately exercised. |
| Event payload and backend field mapping | Sensor aliases (`mb_transferred`, `off_hours`, `category`) normalized by `ingestion.store_events()` | JSON over HTTP(S) | **VERIFIED** by Flask test-client integration tests. |
| Retry and duplicate-safe send | `EventSender`: bounded queue, exponential backoff, timeout, no redirects; backend event IDs retained 30 days | `requests`, SQLite | **VERIFIED** by sender and ingestion tests. Queue is memory-only and may drop oldest data at capacity. |
| Logging and shutdown | Rotating JSON log, signal handling, adjacent stop-file shutdown | `logging`, OS signal, writable deployment directory | **VERIFIED** by sensor tests and packaged stop-file smoke. Stop requests are not checked during the blocking sample sleep. |
| Endpoint deployment independent of dashboard login | Sensor token path is limited to ingestion and does not create dashboard sessions | Shared bearer token and `/api/ingest` | **VERIFIED locally**: test confirms `/api/whoami` is unauthenticated while token-authenticated sensor ingestion succeeds. A separate endpoint/Admin-PC deployment was not tested. |
| Windows executable and external config | `build_sensor.py`, PyInstaller one-file/no-console packaging | Windows, PyInstaller, `psutil`, `requests`, `watchdog` | **PARTIALLY VERIFIED**: isolated executable built and run with external config, optional monitors, and stop-file shutdown. A clean PC without Python was unavailable. |

### Optional failed-login collection

| Feature | Implementation | Dependencies / data | Status and evidence |
|---|---|---|---|
| Enable/disable configuration | `failed_login_monitoring.enabled`, defaults to `false` | External sensor JSON config | **VERIFIED** by config tests. |
| Read-only audit policy check | `failed_login_monitor.FailedLoginMonitor` invokes `auditpol.exe` for the Logon subcategory; it never changes policy or elevates | Windows audit policy and process privileges | **PARTIALLY VERIFIED** with mocked command output. Live query was blocked: local `auditpol` returned privilege-not-held error 1314. |
| Event ID 4625 count | PowerShell `Get-WinEvent` query uses record-ID cursor, returns only event record IDs and target usernames to the sensor process | Windows Security log access | **PARTIALLY VERIFIED** with mocked events and packaged startup under unavailable permissions. Live event parsing/counting is **BLOCKED** by local Security-log access denial. |
| Account attribution and privacy | Counts only `TargetUserName` matching `getpass.getuser()`; no per-event identity/XML/password/address is sent | Sensor process identity | **PARTIALLY VERIFIED** with matching tests. It does not claim to represent every account or the interactive user when run as a service. |
| Zero versus unavailable | `0` only for a successful, policy-confirmed query with no matching events; otherwise `null`; backend accepts as supported only `available` plus nonnegative integer | `telemetry_status.failed_logins`, `logs.feature_status` | **VERIFIED** through mocked collector, backend persistence, and null-vs-zero tests. |
| Model participation | Existing five-feature model unchanged; incomplete rows remain excluded | `model.FEATURES`, model eligibility | **VERIFIED**: zero can be persisted as supported, but other missing/machine-wide features still exclude sensor rows. |

### USB and filesystem monitoring

| Feature | Implementation | Dependencies / data | Status and evidence |
|---|---|---|---|
| Removable volume discovery | `usb_monitor.py`, `psutil.disk_partitions()` plus Windows `GetDriveTypeW == DRIVE_REMOVABLE` | Windows mounted drive roots | **PARTIALLY VERIFIED** with mocked drive-type/multiple-volume cases. No physical attach/remove test. Fixed-type and unmounted USB devices may be missed. |
| USB connect/disconnect events | Initial snapshot emits connected for present volumes; later differences emit connect/disconnect | Poll interval, drive root only | **VERIFIED** at event mapping/unit-test level; physical delivery is **BLOCKED** by unavailable hardware. |
| File create/modify/delete/rename counts | `filesystem_monitor.py` and watchdog observer recursively monitor roots; directory events ignored | `watchdog` notifications | **VERIFIED** using real temporary-directory operations. Notifications do not prove file transfer direction or exfiltration. |
| File privacy | Counts and drive root only; no file path/name/content or read/open auditing | Sensor event schema rejects extra path/name fields | **VERIFIED** by handler and backend path-rejection tests. |
| Separate persistence and deduplication | `endpoint_activity_events`, `ingested_event_ids`; additive `CREATE TABLE/INDEX IF NOT EXISTS` | SQLite | **VERIFIED** by Flask integration tests; table is created on accepted activity ingestion. |
| Admin display | `GET /api/endpoint-activity`, separate PyQt page at navigation index 10 | Admin session, PyQt6 API client | **VERIFIED** by API/widget tests. Physical visual target-PC testing is unperformed. |

### Backend, database, privacy

| Feature | Implementation | Dependencies / data | Status and evidence |
|---|---|---|---|
| Flask routes and local server | `app.py`, default `127.0.0.1:5051`; `INSIGHTER_HOST`/`INSIGHTER_PORT` can configure direct-run binding | Flask development server | **PARTIALLY VERIFIED** by app test client and tests. No production WSGI or live LAN deployment was tested. |
| `/api/ingest` compatibility | Admin session/CSRF path remains; sensor bearer-token path requires an event list and keeps response shape `{status, inserted}` | Flask, `ingestion.py` | **VERIFIED** by sensor and existing admin ingestion tests. |
| Validation and role/category checks | Event JSON/list shape, sensor aliases, active sector role/category checks, operation counts/drive root and event ID | `sector_config.py`, SQLite | **VERIFIED** by integration tests. |
| Event-ID deduplication | `INSERT OR IGNORE` with ID insertion and event storage in a transaction; old IDs pruned after 30 days | `ingested_event_ids` | **VERIFIED** for duplicate retry acknowledgement and single-row persistence. Persistence across a backend process restart was not separately exercised. |
| Payload privacy | `privacy.sanitize_event()` keeps user identity for grouping and Fernet-encrypts non-identity fields | `INSIGHTER_PII_SECRET` or `INSIGHTER_SECRET_KEY`; cryptography | **VERIFIED** for encrypted payload presence and identity exclusion in tests. This is payload-level encryption, not a guarantee of encrypted database files. |
| SQLite encryption | `database.py` uses SQLCipher if installed; otherwise standard SQLite fallback | Optional `sqlcipher3` | **PARTIALLY VERIFIED** by code path. Current environment used plaintext SQLite (`database.db`); SQLCipher was not tested. |
| Database schema/migrations | `users`, `auth_sessions`, `login_throttle`, `audit_log`, `deployment_config`, `logs`, role-baseline tables; `ensure_logs_schema()` adds missing columns; activity/dedup tables created lazily | SQLite additive DDL | **VERIFIED** for log schema and additive event persistence in tests. The inspected local DB had no `endpoint_activity_events` or `ingested_event_ids` yet because those are created when used; no existing data was modified for this inventory. |

### Machine learning and administrator desktop

| Feature | Implementation | Dependencies / data | Status and evidence |
|---|---|---|---|
| Five-feature model contract | `model.FEATURES`: `login_hour`, `files_accessed`, `data_transferred_mb`, `failed_logins`, `off_hours_access` | pandas, NumPy, scikit-learn | **VERIFIED** by model and full-suite tests; contract unchanged. |
| Missing-data and eligibility rules | `_feature_statuses()`, `_scoreable_observations()`, `_prepare_model_features()`; incomplete/non-finite rows are not imputed as zero | `logs.feature_status` | **VERIFIED** by model calibration tests. |
| Isolation Forest and One-Class SVM | `_fit_group_models()` weighted ensemble, role-specific training where sample/user thresholds allow | scikit-learn | **PARTIALLY VERIFIED** through existing model tests and full suite. This task did not alter model code or conduct statistical validation. |
| Role baselines, risk and alerts | `role_baseline.py`, thresholds and alert routes in `app.py`/`model.py` | SQLite deployment configuration | **PARTIALLY VERIFIED** by role-baseline, threshold, alert, and regression tests; no model-quality/false-positive study was performed. |
| PyQt authentication/navigation/data views | `desktop_app/main.py`, `main_window.py`, API client, Overview/Sessions/Profiles/Role Behavior/Alerts and other system pages | PyQt6 and Flask API | **PARTIALLY VERIFIED** by startup, API/widget, and full-suite tests. No manual visual review on a separate Admin PC. |
| Periodic dashboard refresh | `MainWindow` timer runs every 5 seconds and refreshes the active page; activity page requests the admin-only API when active | PyQt `QTimer`, async API client | **VERIFIED by source and page/API tests**; a human-observed live refresh on target hardware was not performed. |

### Other implemented system modules

| Area | Key files | Status / limits |
|---|---|---|
| Synthetic activity generation and simulation | `generate_logs.py`, `simulation.py` | **VERIFIED** by full suite; generated data is not real endpoint evidence. |
| Sector and role configuration | `sector_config.py`, deployment-config routes, `desktop_app/uav_config_tab.py` | **VERIFIED** by sector/deployment tests. |
| Active Directory and external API ingestion | `ingestion.py` LDAP/API functions, `desktop_app/integrations_tab.py` | **PARTIALLY VERIFIED** by unit/mocked tests; no live production directory/provider was connected during this task. |
| Compliance/audit views and pseudonymized outputs | `app.py`, `privacy.py`, audit/compliance dashboard tabs | **PARTIALLY VERIFIED** by RBAC/privacy tests; no regulatory compliance certification. |
| LLM advice and resource pages | `llm_advisor.py`, `desktop_app/ai_worker.py`, resource tabs | **PARTIALLY VERIFIED** by offline/mocked tests; external model availability and semantic quality are not guaranteed. |
| Native endpoint failed-login audit policy enablement, exact file reads/opens, endpoint fleet management, durable local spool, per-device enrollment, installer/service/updater | None | **NOT IMPLEMENTED**. Windows audit policy is deliberately never changed by the sensor. |

## Evidence and limitations

At this task's pre-change state the full suite passed **322 tests**. The final post-change count and package smoke outcome are recorded in [SYSTEM_TEST_REPORT.md](./SYSTEM_TEST_REPORT.md). Live Windows Security collection and physical USB behavior could not be verified in this environment. No claim is made that the system is production-ready, that network/disk data is user-attributed, or that aggregate file operation counts indicate data exfiltration.
