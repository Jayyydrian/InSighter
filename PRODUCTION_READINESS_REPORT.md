# Endpoint Sensor / Telemetry Collector Production Readiness Report

## Executive verdict

**NOT PRODUCTION READY.**

The current implementation is a working prototype for collecting Windows host-level counters plus opt-in removable-volume and aggregate file-operation activity, sending events to existing Flask ingestion, and persisting them for review in InSighter. It has authentication, duplicate suppression, bounded retry, payload encryption, and measurement-quality handling. It does not yet meet the operational, identity, transport, collection, resilience, or validation requirements expected of a production endpoint-monitoring agent.

Standard sensor rows do not currently qualify for the existing per-user anomaly detector because not all model features are supported and attributable. USB/filesystem events are stored separately and never enter the existing model inputs. This avoids treating missing measurements as zeroes or scoring machine-wide network activity as though it belonged to one user.

## Scope and evidence

This assessment covers the repository implementation and verification recorded for the endpoint sensor, its Flask ingestion path, SQLite persistence, anomaly-scoring eligibility, and desktop visibility.

| Area | Files inspected or changed |
|---|---|
| Sensor and packaging | `sensor_agent.py`, `usb_monitor.py`, `filesystem_monitor.py`, `build_sensor.py`, `config.json` |
| Backend authentication and ingestion | `app.py`, `ingestion.py`, `privacy.py`, `sector_config.py`, `generate_logs.py` |
| Anomaly scoring and presentation | `model.py`, `desktop_app/sessions_tab.py`, existing dashboard/API paths |
| Tests | `test_sensor_agent.py`, `test_endpoint_activity.py`, `test_sensor_backend_integration.py`, `test_model_calibration.py`, `test_phase3_contracts.py`, existing ingestion and desktop tests |
| Operational documentation | `ENDPOINT_SENSOR.md`, `README.md`, `summary_es_tc.md`, `SYSTEM_SUMMARY_REPORT.md` |
| CI validation | `.github/workflows/ci.yml`, `scripts/run_tests_ci.py` |

This is a repository and local-test assessment, not an external penetration test, compliance certification, or production pilot.

## Readiness by category

| Category | Status | Evidence and remaining gap |
|---|---|---|
| Core collection process | **PARTIAL** | `sensor_agent.py` samples host-wide network and disk counters and derives local sampling hour/off-hours. Optional monitors detect mounted Windows `DRIVE_REMOVABLE` volumes and count create/modify/delete/rename events without retaining paths or names. Exact file accesses, failed logons, and per-user/process activity are not collected. |
| Endpoint/user identity | **PARTIAL** | Events use host name plus the account running the process. This is not device enrollment or cryptographic device identity, and a service account may not identify the interactive user. |
| Measurement semantics | **IMPLEMENTED, BOUNDED** | Unsupported file/login and unavailable network values are represented as `NULL`; `feature_status` records support and scope. Machine-wide network values are not accepted as per-user detector features. |
| Backend ingestion | **IMPLEMENTED, LIMITED** | `/api/ingest` accepts sensor batches, validates the configured shared bearer token, normalizes fields, checks active-sector role/category compatibility, encrypts the event payload, persists to SQLite, and returns insertion counts. |
| Authentication and authorization | **PARTIAL** | A single shared token is used for sensor ingestion. There is no per-device credential issuance, enrollment, rotation, or revocation; no device-scoped authorization. Sector role/category checks are present. |
| Transport security | **NOT PRODUCTION READY** | TLS verification is enabled by the sensor when using HTTPS, but HTTP is also supported and no production TLS terminator/reverse-proxy deployment is delivered here. The Flask development server must not be exposed as a production service. |
| Retry and offline handling | **PARTIAL** | Retries use bounded exponential backoff and an in-memory queue; deduplication makes repeated delivery safer. The queue is lost on process exit/reboot, and overflow discards the oldest queued event. |
| Persistence and duplicate handling | **IMPLEMENTED, LIMITED** | SQLite stores accepted events; event-ID deduplication is transactional and duplicate IDs are retained for 30 days. No endpoint-side durable encrypted spool or replay after restart exists. |
| Anomaly detection | **PARTIAL / NOT SENSOR-READY** | Existing Isolation Forest / One-Class SVM and role-baseline paths require five complete, supported features. Sensor file-access and failed-login features are unsupported and network transfer is machine-wide, so current sensor events do not contribute to user-level scores or alerts. No endpoint-specific model exists. |
| Desktop visibility | **PARTIAL** | Standard sensor records appear in Sessions with data-quality/usability information. A separate USB & File Activity page shows endpoint activity records. There is no endpoint inventory/health view or raw telemetry inspector; encrypted supplemental fields are not shown. |
| Installation and lifecycle | **PARTIAL** | A Windows one-file PyInstaller build and external configuration are provided; a stop-file mechanism supports graceful shutdown. There is no installer, managed service registration, auto-start, signed update, or managed uninstall workflow. |
| Privacy and retention operations | **PARTIAL** | Non-identity telemetry payloads use the existing encrypted-payload path, and deduplication records expire after 30 days. There is no sensor-specific retention policy/administrative purge workflow, endpoint consent/control plane, or full telemetry access/export workflow. |
| Validation | **PARTIAL** | The full 322-test suite passes (308-test baseline); focused activity tests and an isolated Windows package/activity-worker smoke test pass. Clean-machine installation, physical USB behavior, real Wi-Fi/hotspot operation, production TLS/reverse proxy, Windows audit-policy behavior, soak/load, and visual end-to-end UI testing have not been verified. |

## Implemented controls and behavior

- The sensor requires a configured token from an environment variable and sends it as a bearer credential to `/api/ingest`.
- Sensor-token authorization is scoped to the ingestion route; the existing administrator/session and CSRF path remains for normal administrative ingestion.
- Backend role/category values are checked against the active sector configuration.
- Accepted event data follows the existing privacy sanitization/encrypted-payload path. User/host identity fields and quality metadata have distinct storage behavior; the documentation should not be read as claiming all identifiers are encrypted.
- Sensor-generated event IDs provide duplicate suppression, with a 30-day server-side deduplication window.
- Unsupported file-access and failed-login measurements are persisted as SQL `NULL`, not fabricated zeroes. `logs.feature_status` captures support/scope, and old sensor rows receive conservative migration statuses.
- The model rejects incomplete inputs instead of filling missing features with zero. Sensor events without all five supported model inputs remain visible but are excluded from model fitting/scoring and user risk summaries.
- The optional activity worker reports mounted removable-volume connect/disconnect events and aggregate file create/modify/delete/rename counts. These events use `endpoint_activity_events`, share event-ID deduplication, and do not enter `logs` or model features.
- The admin-only endpoint-activity API and separate dashboard page expose those event summaries without changing the existing Sessions view.
- Sensor HTTP calls validate expected acknowledgements, disable redirects, use a timeout, and log failures. Retry is bounded; queue storage is volatile.

## High-priority gaps before production

1. **Production transport and hosting:** require HTTPS end-to-end or a hardened TLS-terminating deployment; provide a production WSGI/server configuration, firewall guidance, and safe network binding. Do not expose the Flask development server.
2. **Per-device trust:** replace the shared token with device enrollment and unique, revocable credentials; define credential rotation and protect secrets at rest and during provisioning.
3. **Durable delivery:** persist queued events to an encrypted local spool with bounded disk use, crash/reboot recovery, replay, and operator-visible backlog/overflow state.
4. **Real endpoint signals:** if exact file access or failed logons are requirements, implement Windows audit-policy/event-log collection with least-privilege permissions, bookmarks, event deduplication, and explicit privacy boundaries. Do not infer these from counters.
5. **Attribution and model fitness:** keep host-wide measurements separate from user-level features. Design endpoint-aware scoring only after collecting representative, correctly attributed data and establishing false-positive/false-negative evaluation.
6. **Endpoint fleet operations:** add managed installation/service lifecycle, health/inventory and last-seen status, configuration rollout, signed update/rollback, and removal/revocation.
7. **Privacy operations:** define retention and deletion for logs, encrypted payloads, and deduplication records; establish access controls and auditability for raw telemetry.
8. **Operational validation:** test on clean supported Windows versions, representative hardware, real network/firewall topologies, production TLS, prolonged backend outages, queue saturation, upgrades, and multi-endpoint load.

## Tests and verification actually performed

| Check | Result |
|---|---|
| Pre-change repository baseline | **308 tests passed**, no failures or errors. |
| Post-change repository CI test runner, `EXPECTED_MIN_TESTS=322` | **322 tests passed**, no failures or errors. CI minimum is 322. |
| Focused pre-feature data-quality regression suite | **57 tests passed**, covering sensor/backend integration, ingestion validation, model eligibility, role/sector behavior, and desktop contracts. |
| Focused post-change sensor/activity/backend/dashboard group | **47 tests passed**. |
| Python syntax compilation of changed implementation/test files | Passed. |
| Ruff lint | Passed. |
| `git diff --check` | Passed. |
| Earlier packaged sensor local-backend smoke | Passed before adding USB/filesystem monitoring: authenticated delivery, SQLite persistence, invalid-config logging, and stop-file shutdown. |
| Windows PyInstaller one-file, no-console build with activity monitoring enabled | Passed in a temporary directory; executable started the activity worker without watchdog import errors and shut down through the stop-file mechanism. |

The first package build attempt failed because PyInstaller was absent from the active virtual environment; it was installed there without changing a project dependency manifest, and the isolated build passed. The first package smoke configuration was invalid (interval and token below configured minimums); correcting those inputs produced the successful lifecycle check. The initial focused activity fixture lacked the existing `logs` table; the fixture setup was corrected and the focused tests passed.

No performance or scalability measurements were taken. The endpoint package was not independently installed on a clean Windows machine without Python. Physical USB attach/remove behavior, hotspot/Wi-Fi and Windows Firewall configuration, production HTTPS, Windows Security event-log collection, long-duration/queue-exhaustion tests, high concurrency, multi-endpoint load, and visual PyQt end-to-end automation remain unperformed.

## Release recommendation

Keep the sensor in controlled development or a restricted pilot only, with private-network access and an operator-managed token. Do not advertise it as a production endpoint detection/response agent or use its current telemetry as a complete per-user behavioral record. Reassess production readiness only after the high-priority trust, transport, durable delivery, collection, lifecycle, privacy, and validation gaps above are addressed.
