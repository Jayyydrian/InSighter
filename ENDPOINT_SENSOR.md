# InSighter Endpoint Sensor

## 1. Architecture findings

The current application is a PyQt6 client and Flask backend. Its real ingestion route is `POST /api/ingest`, which originally accepted an administrator's session-authenticated request (CSRF protected), then normalized event records into `logs`. The model consumes exactly these numeric fields: `login_hour`, `files_accessed`, `data_transferred_mb`, `failed_logins`, and `off_hours_access`. Event rows also carry `user`, `role`, `source`, `data_category`, an encrypted copy of non-identity input fields, and a server-side ingestion timestamp.

The new sensor is a separate Windows process, independent of the desktop app. It sends one event at a time to the existing endpoint and uses a bearer token configured through an environment variable. It does not add a new public endpoint or change either anomaly detector.

## 2. Backend compatibility changes

The sensor's requested wire format uses `username`, `mb_transferred`, `off_hours`, and `category`, whereas the current backend expects `user`, `data_transferred_mb`, `off_hours_access`, and `data_category`. The endpoint-sensor ingestion path maps those fields to the established names and continues to populate the existing detector columns.

USB and filesystem activity are handled as separate endpoint event types. They use the existing authenticated `/api/ingest` transport, but are stored in the additive `endpoint_activity_events` table and are not written to `logs` or used by the anomaly models.

Related backend changes:

- `ingestion.py` normalizes sensor aliases, checks sensor roles and data categories against the active sector, and uses an event-ID table for idempotent retries. Event IDs are retained for 30 days.
- `ingestion.py` adds the backward-compatible `logs.feature_status` column, backfills old sensor rows with conservative support statuses, stores current status metadata, and leaves unsupported numeric measurements `NULL`.
- `model.py` refuses to impute missing inputs as zero and excludes observations that do not have all five model features measured. This preserves existing scoring for complete historical/synthetic observations but means the current endpoint sensor does not produce user-level risk scores.
- `desktop_app/sessions_tab.py` displays unavailable values and the count of usable model features, distinguishing unscored sensor rows from complete observations.
- `app.py` accepts a configured bearer token only for `/api/ingest`; existing administrator session + CSRF behavior remains available. Sensor ingestion is separately audited as `endpoint_sensor`.
- `app.py` honors `INSIGHTER_HOST` when run directly. It still defaults to loopback (`127.0.0.1`) for local desktop operation.
- `ingestion.py` validates and stores `usb_connected`, `usb_disconnected`, and `filesystem_activity` events separately, sharing the existing event-ID deduplication table.
- `app.py` exposes an admin-only `GET /api/endpoint-activity` view; the existing PyQt dashboard adds a separate USB & File Activity page without replacing Sessions.

The sensor-qualified user key (`HOSTNAME\username`) is stored for grouping and is visible in Sessions. A new `logs.feature_status` column records which detector inputs are supported for an observation. Historical sensor rows are migrated conservatively: file-access and failed-login values are marked unsupported, and network transfer is marked machine-wide. The current detector is per-user and requires all five inputs, so these sensor events are deliberately excluded from user-level model fitting and scoring until valid per-user network and audit measurements are available. Host OS and sensor metadata, raw counters, timestamp, and metric-status details remain in the encrypted event payload.

## 3. Collection behavior and limitations

- **Network:** `psutil.net_io_counters(pernic=False)` is sampled over the configured window. Sent and received byte deltas are added and mapped to `data_transferred_mb`. This is machine-wide traffic, not traffic attributable to the logged-in user or to a particular application.
- **Disk:** machine-wide read/write byte deltas are collected as supplemental telemetry in the encrypted event payload. They are not file counts.
- **Model feature `files_accessed`:** remains `null` and unsupported. A separate, opt-in filesystem monitor counts file create/modify/delete/rename events on detected removable USB volumes. It does not estimate the existing model feature, and the new counts do not enter model scoring.
- **Failed logins:** optional, disabled by default via `failed_login_monitoring.enabled`. When enabled, `failed_login_monitor.py` checks that Windows Logon failure auditing is configured, reads only new Security Event ID 4625 records, and counts records whose `TargetUserName` matches the account running the sensor. It transmits only the count and observation-window duration; event XML, target account names, passwords, source addresses, and logon details are not sent or retained. The collector does not change Windows audit policy. If auditing is disabled/unconfirmed, permissions are insufficient, the log is unavailable, or a query fails, `failed_logins` remains `null` and the status is respectively `not_configured`, `unavailable`, or `error`. When collection succeeds with no matching events, the value is integer `0` and status is `available`.
- **Off-hours:** based on the endpoint's local clock and the existing InSighter rule (before 06:00 or after 21:00). The event timestamp itself is UTC ISO 8601.
- **Temporal field meaning:** `login_hour` is the local hour when the sensor sampled the endpoint, not a collected operating-system logon event.
- **Identity:** the process account from `getpass.getuser()` and hostname are used; the sensor does not guess the active interactive user using the first result from `psutil.users()`. A service running under a service account will therefore report that service account.
- **Unavailable values and scoring:** unsupported file/login fields and unavailable network values are `null`, not zero. The backend persists per-feature status in `logs.feature_status`. Only records with all five configured model features marked `supported` are scored; incomplete observations remain visible in Sessions with a usable-feature count and are excluded from model fitting, user summaries, and alerts. Machine-wide network transfer is retained as telemetry but does not count as a per-user model feature.
- **Offline operation:** failed events are queued in bounded memory and retried with exponential backoff. The queue is lost if the process exits or Windows restarts. When full, the oldest unsent event is dropped with an error log entry. The backend's event IDs prevent duplicates if a request reached the server but its acknowledgement was lost.
- **Scope:** this is a transparent monitoring process for authorized devices. It does not install persistence, evade controls, or change Windows security settings.

## 4. USB and filesystem monitoring

The checked-in `config.json` enables `endpoint_activity` with a five-second polling interval. The setting is optional in deployed configs: if an older config omits it, the new monitoring worker stays disabled, preserving that deployment's previous behavior. Set `endpoint_activity.enabled` to `false` to disable both monitors.

`usb_monitor.py` enumerates mounted partitions and uses the Windows drive-type API to identify volumes classified as `DRIVE_REMOVABLE`. It reports a connection for each volume present at startup or newly seen on a poll, and a disconnection when a previously seen root disappears. Polling means detection latency is bounded by the configured interval when enumeration succeeds. No serial number, vendor/product ID, volume label, or USB contents are collected. Some USB storage devices that Windows classifies as fixed disks will not be identified by this removable-volume strategy.

`filesystem_monitor.py` uses the `watchdog` observer to monitor the detected roots recursively. It counts file-level create, modify, delete, and rename notifications, ignores directory events, and drains counts by drive on each poll. It never retains or transmits file names, paths, or contents. Read/open activity is not collected. The process account must have sufficient access for Windows to deliver notifications; watch failures are logged and do not stop the existing network/disk telemetry loop.

### Optional Windows failed-login measurement

The sensor's configuration may include:

```json
"failed_login_monitoring": {
  "enabled": false
}
```

Omitting the setting is equivalent to `false`. To enable it, an administrator must configure and verify the Windows **Audit Logon / Failure** policy outside the sensor and grant the sensor process an account allowed to query that policy and read the Security event log. The collector uses read-only `auditpol.exe` and `Get-WinEvent` commands; it never enables auditing or elevates itself. Windows typically restricts access to Security events, so the feature can remain unavailable for ordinary, non-elevated processes. Access denial does not stop standard telemetry, USB monitoring, filesystem monitoring, or ingestion.

Statuses are carried in the encrypted source payload as `telemetry_status.failed_logins`: `available`, `disabled`, `not_configured`, `unavailable`, or `error`. Only a successful query with an integer count (including zero) is treated by the backend as `feature_status.failed_logins = supported`; all other cases are stored as SQL `NULL` and `unsupported`. The backend never infers zero from a missing value. The read cursor is held in memory and initialized to the latest Security event on process startup, so old events are not replayed; failures during downtime/restart are not backfilled. The observation counts events since the previous successful poll. The sensor matches only the target account name against `getpass.getuser()`; it does not assume the process account is the interactive user. If it runs as a service account, that account is the only one attributed. This is a per-process-account count, not a complete endpoint-wide account inventory.

The collector does not transmit per-event timestamps or identities. The standard telemetry event timestamp and `failed_logins_observation_window_seconds` identify the aggregate observation record/window, while the Windows record ID is used only as an in-memory cursor. The existing five-feature model still requires all five inputs: even with failed-login counts available, endpoint observations remain unscored while file access is unavailable and network traffic is only machine-wide.

USB and file events have their own `event_type` (`usb_connected`, `usb_disconnected`, `filesystem_activity`). Filesystem events contain the drive root and an `operation_counts` object, for example `{"create":2,"modify":1}`. The backend rejects file metadata keys, validates operation names/counts, role/category and timestamp, encrypts the accepted source payload, and writes the event to the additive `endpoint_activity_events` table. Event IDs use the existing 30-day deduplication table. Existing `logs` rows, detector features, model eligibility, scoring, and alerts are not changed by these event types.

The Admin client reads these records through admin-only `GET /api/endpoint-activity` and shows them in a separate **USB & File Activity** navigation page. The existing User Sessions table and other dashboard pages are unchanged.

## 4. Files and dependencies

- `config.json` — editable external example; change its host and endpoint role/category before running.
- `sensor_agent.py` — configuration, identity, sampling, transport, bounded retries, and rotating JSON-line logs.
- `failed_login_monitor.py` — optional, read-only Windows audit-policy check and Event ID 4625 counter; no event details are transmitted.
- `usb_monitor.py` — Windows removable-volume discovery and connected/disconnected event detection.
- `filesystem_monitor.py` — recursive watchdog watches and path-free operation counters.
- `desktop_app/endpoint_activity_tab.py` — separate admin view of USB and filesystem events.
- `build_sensor.py` — Windows PyInstaller one-file build helper.
- `test_sensor_agent.py` — unit tests for configuration, metrics, identity, transport, and retry behavior.
- `test_endpoint_activity.py` — removable-volume detection and filesystem operation counting tests.
- `test_sensor_backend_integration.py` — authenticated ingestion, field mapping, replay handling, and sector validation tests.

The sensor runtime uses `requests`, `psutil`, and `watchdog` (pinned in `requirements.txt`). `PyInstaller` is a build-time dependency only; the build helper installs it only when explicitly passed `--install-pyinstaller`.

## 5. Build and packaging

Run on Windows from the repository root, in an environment with the backend requirements installed:

```powershell
python -m pip install -r requirements.txt
python build_sensor.py
```

The default build has a console for development. For deployment:

```powershell
python build_sensor.py --noconsole
```

If PyInstaller is not installed, either install it explicitly with `python -m pip install pyinstaller` or run `python build_sensor.py --noconsole --install-pyinstaller`. The executable is `dist\insighter-sensor.exe`; `dist\config.json` is copied from the example when absent. An existing dist configuration is preserved unless `--force-config` is supplied.

Copy both files to the endpoint. Edit `config.json` beside the executable. No Python installation is needed on the endpoint. The sensor writes rotating logs to `insighter_sensor.log` beside the executable; this folder must be writable. The example uses `CHANGE_ME_TO_ADMIN_PC_IP_OR_HOSTNAME` intentionally and will not start until replaced.

For a graceful stop of a `--noconsole` build, create an empty `insighter_sensor.stop` file beside the executable. The sensor checks for this request at least once per second, removes it, logs the shutdown, and exits. Ctrl+C works for console builds. Unsent in-memory events are not retained after either kind of shutdown.

## 6. Admin PC deployment

1. Configure the normal InSighter application secrets and active deployment sector as described in [README.md](./README.md).
2. Set `INSIGHTER_SENSOR_API_TOKEN` to a strong shared sensor-ingestion token of at least 32 characters. Keep it out of source control, the config file, logs, screenshots, and command history. Supply the same token to endpoints through a protected environment/secret-management mechanism.
3. Keep the desktop launcher on loopback for local-only use. For an isolated demonstration network where endpoint devices must reach the API, run the backend directly with the server bound to the Admin PC's LAN interface:

   ```powershell
   $env:INSIGHTER_HOST = "0.0.0.0"
   $env:INSIGHTER_PORT = "5051"
   python app.py
   ```

   Use the Admin PC's actual private-network address in each sensor's `config.json`. Configure Windows Firewall to allow the backend port only on the dedicated, trusted demonstration network and only from authorized endpoint devices. Do not expose this development Flask server to the public internet.
4. Configure HTTPS termination and a production WSGI server before use on any network where traffic can be observed or modified. The sensor supports HTTPS with certificate validation enabled. The current direct Flask backend is HTTP-only; setting `scheme` to `https` will work only when a correctly configured HTTPS reverse proxy/server is in front of it. Do not disable certificate validation.

The bearer token is a shared credential, not a per-device identity or an encrypted transport. With plain HTTP, a network observer can capture or replay it. For a capstone demonstration, use a trusted isolated network; for real monitoring, use HTTPS, restricted firewall rules, secret rotation, and preferably per-device credentials and revocation.

## 7. Endpoint deployment

1. Copy `insighter-sensor.exe` and `config.json` to a writable folder.
2. Set the token environment variable named by `auth.token_env` (the example uses `INSIGHTER_SENSOR_API_TOKEN`) using the endpoint's approved secret-management method.
3. Set `backend.host` to the Admin PC's reachable private IP or DNS hostname. Confirm `port` and `path` match the backend.
4. Set `endpoint.role` and `endpoint.category` to values in the active sector. Private School examples: `researcher` + `projects`, `intern` + `private_documents`, `admin_staff` + `pii`, or `it_staff` + `intellectual_property`. UAV examples: `drone_operator` + `drone_platform_access` or `disaster_data`.
5. To enable optional Windows failed-login counts, set `failed_login_monitoring.enabled` to `true` only after an administrator has independently configured and verified Logon failure auditing and granted the sensor account read access. The checked-in example disables this feature; missing permissions or policy do not stop other telemetry.
6. Run `insighter-sensor.exe` and inspect `insighter_sensor.log`. Standard endpoint telemetry is visible in Sessions with unavailable values and a usable-feature count. When `endpoint_activity.enabled` is true, the separate USB & File Activity page shows removable-volume changes and path-free file operation counts. Neither event stream currently contributes to user-level scores or alerts: standard telemetry lacks supported per-user features, and USB/filesystem events are stored separately from model inputs.

## 8. Verification and test status

The automated tests cover configuration validation, identity formation, safe counter deltas, host-wide metric labeling, HTTP request format and acknowledgement validation, bounded retry behavior, backend bearer authentication, sensor-field normalization, nullable unsupported metrics, persisted feature-status metadata, exclusion of under-supported observations from scoring, SQLite persistence, duplicate delivery, and rejection of roles outside the active sector.

**Executed in this workspace:** the pre-USB/filesystem baseline passed 308 tests; the pre-failed-login task baseline passed 322 tests; the final full suite passed **340 tests with zero failures, errors, or skips**. The focused collector/auth/sensor/backend set passed 49 tests. Ruff, changed-file Python syntax compilation, and `git diff --check` passed. A temporary Windows PyInstaller one-file/no-console build also passed an isolated run using adjacent config with both optional monitors enabled; Security audit-policy access was reported as unavailable without stopping the sensor, and stop-file shutdown succeeded. Detailed case results are in `SYSTEM_TEST_REPORT.md`.

The watchdog observer was exercised against a temporary directory for create/modify/rename/delete notifications; backend tests verify separate-table storage, duplicate handling, path rejection, and no new `logs` rows. Failed-login unit tests use mocked Windows command results; the local machine denied Security-log access (`UnauthorizedAccessException`) and the audit-policy query returned privilege-not-held error 1314, so actual Windows 4625 collection was not demonstrated. Cross-computer deployment and physical USB behavior were not tested.

The USB detection tests use mocked drive types; no physical USB attach/remove cycle was available. A clean Windows machine without Python was not available for installation-independent package testing. Dashboard visibility is covered by the admin API and widget tests; no physical USB device or visual target-PC test has been performed. The package smoke used a deliberately unreachable loopback port and did not verify real endpoint-to-Admin-PC connectivity.

## 9. Remaining work before production

- Persistent encrypted offline queue and per-device token registration/rotation.
- Physical Windows Security event-log collection with administrator-authorized audit-policy and permissions setup for failed logons.
- Read/open file auditing if required; current collection intentionally counts create/modify/delete/rename only.
- USB device identification beyond mounted removable-drive roots, including fixed-type USB storage.
- HTTPS reverse proxy / production WSGI deployment and firewall hardening.
- Field validation and operational trial on representative Windows endpoint configurations.
- Visual demonstration on the target Admin PC and endpoint hardware.
