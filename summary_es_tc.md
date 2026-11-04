# InSighter Endpoint Sensor / Telemetry Collector (ES/TC)

**Document type:** Technical reference and implementation record
**Implementation:** `sensor_agent.py`, sensor-enabled ingestion in `app.py` and `ingestion.py`
**Scope:** Current implementation, not a proposal for a future endpoint-monitoring product

## 1. Executive Summary

The InSighter Endpoint Sensor / Telemetry Collector (ES/TC) is an optional, separately run Python program that can be packaged as a Windows one-file executable. It samples a small set of machine-wide operating-system counters, attaches the process account and host identity, formats one event per configured collection interval, and sends events to InSighter's existing `POST /api/ingest` route.

The ES/TC fills the connection between monitored Windows endpoints and the InSighter Admin PC. The backend accepts its events using a shared bearer token, translates the sensor's wire-format aliases, validates role/category against the active sector, protects the non-identity event payload, and persists the event in SQLite. Incomplete endpoint observations remain visible in Sessions but are excluded from user-level model fitting and alerts.

Intended use is transparent, authorized endpoint monitoring in a controlled environment, such as a capstone demonstration over an isolated local Wi-Fi network or hotspot. It is not a full endpoint detection and response product. Current endpoint observations are limited:

- Network and disk counters are **machine-wide**, not attributable to a particular user or application.
- `files_accessed` and `failed_logins` are stored as `null` because the corresponding collectors are not implemented.
- `login_hour` is the local clock hour at sensor sampling time, not an observed Windows logon event.
- When network counters are unavailable, transferred MB is `null`; when available, it is still host-wide rather than per-user.

`logs.feature_status` carries per-feature measurement status. The existing per-user detector requires all five configured features to be genuinely supported, and the sensor currently supports only sampling hour and off-hours for user-level scoring. Endpoint sensor events therefore remain persisted and visible in Sessions, but are excluded from model fitting, user risk summaries, and alerts. This avoids treating missing values as measured zeroes or attributing machine-wide network activity to one user.

## 2. System Architecture

### 2.1 Components

```text
Windows endpoint
  sensor_agent.py / insighter-sensor.exe
    ├── config.json (beside script/executable)
    ├── operating-system username and hostname
    ├── psutil network and disk I/O counters
    ├── bounded in-memory event retry queue
    └── HTTPS or HTTP POST + bearer token
                 |
                 v
Admin PC Flask backend: POST /api/ingest
    ├── bearer-token authentication for sensor requests
    ├── sensor field-name normalization and sector validation
    ├── privacy.sanitize_event (normalization + payload encryption)
    ├── SQLite logs and ingested_event_ids
    └── existing audit log
                 |
                 v
Existing scoring/model path
    ├── model.score_users() / model.get_recent_alerts()
    ├── Isolation Forest + One-Class SVM + role baseline
    └── existing risk summaries and alert records
                 |
                 v
Existing PyQt6 client (administrator views)
    ├── Sessions: raw recent log rows
    ├── Profiles: per-user risk and activity history
    ├── Overview / Alerts: aggregates and flagged events
    └── other existing role-specific pages
```

### 2.2 Communication and responsibilities

The ES/TC is not launched by the desktop client and does not use a desktop login session. Each endpoint runs its own sensor process. It posts directly to a configured host, port, scheme, and path. The default example is `http://CHANGE_ME_TO_ADMIN_PC_IP_OR_HOSTNAME:5051/api/ingest`; the placeholder deliberately fails validation.

The backend's desktop launcher remains local-loopback oriented. A remote endpoint cannot use that loopback listener. For an isolated demonstration, an operator can run `app.py` directly with `INSIGHTER_HOST=0.0.0.0` and a chosen port. That is the Flask development server, not a production hosting setup. Remote use should be behind a properly configured HTTPS endpoint, with network access restricted by firewall policy.

SQLite persistence uses the application's existing configured database. The sensor does not connect to SQLite directly. Neither the Isolation Forest nor One-Class SVM was changed for ES/TC support; the backend maps incoming values into the existing five detector columns.

### 2.3 Runtime sequence

1. Determine the configuration directory from `sys.frozen`: the executable directory for a PyInstaller build, or the source directory for Python execution.
2. Set up a rotating log and load/validate `config.json` plus the configured token environment variable.
3. Identify the process account and endpoint hostname.
4. Sample network and disk counters, wait for the configured measurement window, and sample again.
5. Compute non-negative counter deltas, status codes, a local sampling hour, a UTC timestamp, and a unique event ID.
6. Put the event on a bounded in-memory queue.
7. POST queued events to `/api/ingest`; on transient failures, retain the event and retry with exponential backoff.
8. The backend authenticates and normalizes the event, checks sector role/category, writes a row and deduplication ID to SQLite, and records an audit event.
9. Existing score and alert routines read the logs table when their API endpoints are called.
10. The PyQt client refreshes its current page on its normal refresh schedule; existing Sessions, Profiles, Overview, and Alerts UI paths consume those APIs.

The sensor does not independently trigger scoring or push dashboard updates. Scoring and presentation use existing InSighter request/refresh behavior.

## 3. Project Structure and Files

### 3.1 ES/TC implementation files

| File | Status | Purpose |
|---|---|---|
| `config.json` | Created | Editable external example for host/port/scheme/path, sampling, endpoint role/category, retry, token environment-variable name, and TLS verification. The checked-in host is an intentional placeholder. |
| `sensor_agent.py` | Created / updated | Configuration validation, identity, counter sampling, support metadata, payload formation, HTTP transport, retries, logging, signal/stop-file shutdown, and `main()`. Unsupported measurements are represented by `null`. |
| `build_sensor.py` | Created | Windows-only PyInstaller one-file build command, dependency checks, optional PyInstaller installation, and external config copying. |
| `dist/insighter-sensor.exe` | Generated | Windows PyInstaller executable, built with `--onefile --noconsole`. It is generated output, not Python source. |
| `dist/config.json` | Generated | External configuration copied by the build script. Existing file is preserved unless `--force-config` is used. |
| `test_sensor_agent.py` | Created | Unit tests for configuration, identity, telemetry calculations, HTTP response handling, retry queue, and stop-file behavior. |
| `test_sensor_backend_integration.py` | Created / updated | Flask test-client tests for bearer auth, wire-format mapping, nullable unsupported metrics, SQLite persistence, score exclusion, duplicate delivery, and sector rejection. |
| `test_model_calibration.py` | Updated | Confirms model preparation rejects missing values and under-supported sensor observations are not scoreable. |
| `ENDPOINT_SENSOR.md` | Created | Initial deployment and integration guide; this document supersedes it as the comprehensive reference. |
| `SYSTEM_SUMMARY_REPORT.md` | Updated | High-level system report updated to mention the endpoint sensor and latest verification. |

### 3.2 Existing files changed for support

| File | Change |
|---|---|
| `app.py` | Adds sensor bearer-token authentication for the existing ingestion route, a sensor-specific ingestion branch, audit actor labeling, the `INSIGHTER_HOST` direct-run bind setting, and preserves the existing admin-session ingestion path. |
| `ingestion.py` | Adds sensor field mapping, role/category validation, 30-day event-ID deduplication, `feature_status` schema migration/backfill, and persistence of per-feature support status. |
| `privacy.py` | Preserves explicit `null` values for unavailable numeric measurements instead of casting them to compatibility zeroes. |
| `generate_logs.py` | Adds nullable `feature_status` to newly created `logs` tables. |
| `model.py` | Rejects missing feature values at model preparation and scores only rows where all five configured features are supported. |
| `desktop_app/sessions_tab.py` | Displays unavailable values and a usable-feature count with scored/not-scored state. |
| `README.md` | Adds an endpoint-sensor quick description and a link to the implementation guide. |

`auth.py` and database connection code were not changed. `requirements.txt` and `requirements-desktop.txt` were not changed: the runtime libraries `psutil` and `requests` were already listed/required, and PyInstaller is a build-time dependency only.

### 3.3 External dependencies

| Dependency | Use |
|---|---|
| Python standard library | JSON, system identity, time, UUID, signal handling, paths, logging, queue, and synchronization |
| `psutil` | Network and disk I/O counters |
| `requests` | HTTP(S) POST and timeout/error handling |
| PyInstaller (build time only) | Creates the standalone executable |
| Flask / Werkzeug (Admin PC) | Existing backend and request handling |
| SQLite / existing database layer | Existing event persistence and deduplication table |
| scikit-learn / NumPy / pandas (backend) | Existing scoring pipeline, unchanged |
| PyQt6 (Admin PC client) | Existing presentation layer, unchanged |

The executable bundles Python and the sensor's runtime imports. No Python installation is required on an endpoint after a successful Windows build. The build host does require Python, `psutil`, `requests`, and PyInstaller.

## 4. Detailed Functionality and Implementation

### 4.1 Configuration loading and host addressing

`configuration_path()` checks `sys.frozen`. Frozen execution uses `Path(sys.executable).resolve().parent / "config.json"`; script execution uses `Path(__file__).resolve().parent / "config.json"`. The external file is not embedded into the executable.

`load_config(path=None)`:

- Reads UTF-8 JSON and requires a top-level object with `backend`, `collection`, `endpoint`, `retry`, `auth`, and `tls` objects.
- Rejects a missing config, malformed JSON, invalid types, missing values, invalid host/port/path, unsupported scheme, and unsupported role/category combinations.
- Supports an IP literal or hostname in `backend.host`; it does not scan for or discover the Admin PC automatically. An IP change must be handled by editing `config.json`, or by using a stable DNS hostname.
- Reads the secret from the environment variable named by `auth.token_env`; it does not read the token from `config.json`.
- Requires the token to be at least 32 characters and requires TLS verification to remain enabled.
- Warns at startup when the configured scheme is HTTP; it does not silently change the URL or downgrade HTTPS.

Validated limits:

| Setting | Validation |
|---|---|
| `backend.host` | Valid IP/hostname, non-empty, not the `CHANGE_ME` example, no scheme, whitespace, path, or backslash |
| `backend.port` | Integer 1–65535 |
| `backend.scheme` | `http` or `https` |
| `backend.path` | Local absolute path beginning with `/`, not `//`, no backslash or `..` path segment |
| `backend.timeout_seconds` | Number 1–120 |
| `collection.interval_seconds` | Number 5–86400 |
| `collection.sampling_window_seconds` | Number 1–300 and no greater than the collection interval |
| `endpoint.role` | One supported InSighter role |
| `endpoint.category` | One supported InSighter category, paired with the same sector as the role |
| `retry.initial_seconds`, `retry.max_seconds` | Numbers 1–3600; maximum must be at least initial |
| `retry.queue_max_events` | Integer 1–100000 |
| `auth.token_env` | Non-empty variable name; value must exist and be at least 32 characters |
| `tls.verify` | Must be Boolean `true`; disabling certificate verification is rejected |

`_is_hostname_or_ip()` first uses `ipaddress.ip_address()` and then validates DNS label syntax. IPv6 literals are bracketed by `event_url()` when constructing a URL.

### 4.2 Endpoint and user identity

`endpoint_identity(role, category)` uses `getpass.getuser()` for the OS account of the process and `socket.gethostname()` (with `platform.node()` fallback) for the host. It does **not** select an arbitrary first entry from `psutil.users()`. The returned identity includes:

- `user`: host-qualified detector key, `HOSTNAME\username`
- `username`: unqualified OS username
- `endpoint_id`, `hostname`: host name
- `os_info`: `platform.platform()` string
- `role`, `category`: configured values
- `sensor_version`: `1.0.0`

The host-qualified `user` separates same-named accounts on different endpoints in the backend's existing per-user grouping. If the sensor is launched as a Windows service, the reported account will be the service identity, not necessarily the interactive operator.

### 4.3 Network and disk sampling

`_network_counters()` calls `psutil.net_io_counters(pernic=False)` and returns total bytes sent and received for the machine. `_disk_counters()` calls `psutil.disk_io_counters()` and returns read/write bytes. Both return `None` when the counter is unavailable or psutil raises an OS/psutil error.

`collect_event(config, sleep=time.sleep)` samples both counter groups before and after the configured `sampling_window_seconds`. `safe_counter_delta(previous, current)` returns:

- `(None, "unsupported")` when either side is unavailable,
- `(0, "counter_reset")` if the counter decreased during the window,
- `(current - previous, "available")` otherwise.

The sensor computes the sum of bytes sent and received, and its rate in bytes/second. It converts the sum over the entire sampling window to MiB (`bytes / 1024²`) and maps that to `mb_transferred`. This is a volume of machine-wide traffic across both directions, not an upload-only amount, per-user amount, application amount, or file transfer measurement.

Disk byte deltas are supplemental metrics only. They are not transformed into file counts. Reading/writing a disk may correspond to many kinds of activity and cannot be equated to an exact number of files opened or accessed.

If either network counter is unavailable, `mb_transferred` is `null` and status is unsupported. For a counter reset, that direction's delta is zero, while a valid opposite direction may still contribute. `feature_status.data_transferred_mb` is `machine_wide` when a network value exists and `unsupported` otherwise; it is not considered a per-user model feature.

### 4.4 Time fields, off-hours, and failed logins

- `timestamp` is UTC ISO 8601 with a trailing `Z`.
- `login_hour` is sampled from the endpoint's **local** timezone at collection time. It is not sourced from Windows Security logon events.
- `off_hours` is `1` when local hour is before 06:00 or after 21:00, otherwise `0`; hour 6 and hour 21 are not considered off-hours.
- The backend maps `off_hours` to `off_hours_access`.
- Windows Security event ID 4625 is not queried. No event-log bookmark, audit-policy enablement, permission check, or event deduplication for Windows logs exists. `failed_logins` is `null` with a status marker `unsupported_windows_security_log_not_enabled`.

The event timestamp is part of encrypted source payload. The `logs.ingested_at` value is assigned on the server with `CURRENT_TIMESTAMP`; the current schema does not save the supplied sensor timestamp into its own dedicated column.

### 4.5 Event format and processing

`collect_event()` constructs one event containing:

```json
{
  "event_id": "UUID",
  "user": "HOSTNAME\\username",
  "username": "username",
  "endpoint_id": "HOSTNAME",
  "hostname": "HOSTNAME",
  "os_info": "platform description",
  "sensor_version": "1.0.0",
  "role": "researcher",
  "category": "projects",
  "source": "endpoint_sensor",
  "timestamp": "UTC timestamp",
  "login_hour": 10,
  "files_accessed": null,
  "mb_transferred": 0.001,
  "failed_logins": null,
  "off_hours": 0,
  "measurement_window_seconds": 5,
  "telemetry_status": {},
  "feature_status": {},
  "telemetry_metrics": {}
}
```

The example's numeric values are illustrative. `telemetry_status` contains `network`, `files_accessed`, `failed_logins`, and `disk_io` collection descriptions. `feature_status` contains a status for each of the five model fields; `machine_wide` is an observed network metric that is not valid for per-user scoring. `telemetry_metrics` contains sent/received/total network bytes, rate, disk read/write bytes, and per-counter statuses.

### 4.6 Transport, retry, and duplicate handling

`event_url(config)` combines scheme, host (bracketing IPv6), port, and configured path. `post_event(event, config, session=None)` sends:

```http
POST /api/ingest
Content-Type: application/json
Authorization: Bearer <environment-provided-token>
```

```json
{"events": [ { "...": "one sensor event" } ]}
```

The request uses the configured timeout, `verify=True`, and `allow_redirects=False`. A successful response must be JSON with `status: "ok"` and an integer `inserted` value of 0 or 1. `inserted: 0` is valid for a duplicate event.

`EventSender` stores pending events in a bounded `collections.deque`:

- A transient connection/timeout error, HTTP 5xx, or HTTP 408/425/429 keeps the event queued and schedules an exponential retry from `initial_seconds` up to `max_seconds`.
- HTTP 401 and 403 keep the event queued and retry with the same backoff, while logging a token/configuration error.
- Other 4xx errors and redirects are considered permanent and drop that event with an error log.
- A success removes the event and resets the delay.
- When the queue is full, the oldest pending event is dropped and an error is logged.
- Event IDs generated by the sensor allow the backend to reject duplicates if the first request was persisted but its acknowledgement was lost.

The queue is memory-only. Unsent telemetry is not written to a local durable queue and is lost at process exit/reboot. The queue is bounded to limit resource usage.

### 4.7 Logging and shutdown

`JsonLogFormatter` formats log records as JSON lines with a UTC timestamp, level, logger, message, and exception text when present. `setup_logging()` writes to `insighter_sensor.log` beside the script/executable with `RotatingFileHandler` limits of 1,000,000 bytes and three backups. Script mode also logs to the console. Frozen mode requires the log file to be writable; failure raises a runtime error rather than silently proceeding without operational logs.

Events themselves, usernames, hostnames, role, destination URL, queue status, delivery failures, config errors, and shutdown are logged as operational details. The bearer-token value is not intentionally included in log messages.

`_request_shutdown()` sets the global `STOP_EVENT` on SIGINT/SIGTERM where supported. `_check_file_shutdown()` also checks for `insighter_sensor.stop` beside the config, removes the file when possible, and requests a clean loop exit. This is the documented shutdown mechanism for `--noconsole` builds. The loop checks it during the waiting/delivery period (up to about one second between checks), but `collect_event()` uses a regular sleep; stop requests are not checked during the measurement window, whose validated maximum is 300 seconds. Shutdown logs the number of unsent in-memory events but does not flush or persist them.

`main()` configures logging, validates configuration, installs signal handlers, and runs the collector. Configuration errors return exit code 2; an uncaught run error returns 1; a normal graceful exit returns 0.

## 5. Function and Code Reference

### 5.1 `sensor_agent.py`

| Function/class | Inputs | Output / behavior |
|---|---|---|
| `ConfigurationError` | Error message | Explicit configuration-validation exception caught by `main()`. |
| `PermanentIngestError` | Message and optional HTTP status | Distinguishes terminal backend rejection; status 401/403 is treated specially by the sender. |
| `JsonLogFormatter.format(record)` | Python logging record | JSON text with UTC time, level, logger, message, and optional exception. |
| `configuration_path()` | None | `Path` to adjacent config, selected for script or frozen executable. |
| `_require_object(config, key)` | Parsed object and section key | Valid section dictionary or `ConfigurationError`. |
| `_number(section, key, min, max)` | Setting section, key, bounds | Validated float; rejects booleans, wrong types, and out-of-range values. |
| `_integer(section, key, min, max)` | Setting section, key, bounds | Strict integer validation; booleans are rejected. |
| `_is_hostname_or_ip(value)` | Host string | Boolean validating IP literal or DNS hostname labels. |
| `load_config(path=None)` | Optional config path | Validated, normalized runtime dictionary including token read from environment; raises `ConfigurationError` for invalid setup. |
| `setup_logging(directory, frozen=False)` | Directory and runtime mode | Installs rotating JSON-file logging and console output in script mode; returns intended log path. |
| `endpoint_identity(role, category)` | Configured role/category | Host-qualified user and endpoint/OS metadata; errors if account or hostname unavailable. |
| `safe_counter_delta(previous, current)` | Two counter values | `(delta, status)` tuple supporting unavailable and reset cases. |
| `_network_counters()` | None | Host-wide sent/received byte counters or `None`. |
| `_disk_counters()` | None | Host-wide disk read/write byte counters or `None`. |
| `collect_event(config, sleep=time.sleep)` | Validated configuration and injectable sleep | Samples counters and returns one sensor wire event. |
| `event_url(config)` | Validated config | Full HTTP(S) endpoint URL. |
| `post_event(event, config, session=None)` | Event, config, optional requests-compatible object | Sends event and validates status/body; raises `PermanentIngestError` or requests exceptions on failure. |
| `EventSender.__init__(config, session=None)` | Config and optional HTTP session | Initializes bounded pending queue and retry state. |
| `EventSender.enqueue(event)` | Event dictionary | Adds event or drops oldest when queue limit is reached. |
| `EventSender.seconds_until_attempt()` | None | Time in seconds until next retry, or infinity if queue empty. |
| `EventSender.send_if_due()` | None | Sends head event if due; returns success Boolean and updates queue/backoff. |
| `EventSender._schedule_retry()` | None | Advances deadline and doubles delay up to configured maximum. |
| `EventSender._reset_retry()` | None | Restores initial delay and immediate-ready state. |
| `_check_file_shutdown(stop_event)` | Threading event | Detects/removes adjacent stop file and signals loop exit. |
| `run(config, stop_event=STOP_EVENT)` | Config and stop event | Repeatedly collects, enqueues, delivers, and checks shutdown. |
| `_request_shutdown(signum, frame)` | Signal values | Logs request and sets global stop event. |
| `main()` | Process entry | Logging/configuration setup, signal handlers, runtime invocation, and exit status. |
| `_feature_statuses(row)` | Model row | Returns model features explicitly marked supported and containing finite numeric values; legacy rows without status metadata are interpreted from their stored numeric values, while legacy sensor rows fail closed. |
| `_scoreable_observations(frame)` | DataFrame | Keeps only rows for which all five configured model features are supported. |

### 5.2 `build_sensor.py`

| Function/constant | Purpose |
|---|---|
| `ROOT`, `SENSOR`, `CONFIG`, `DIST`, `WORK` | Resolve source, example config, output folder, and PyInstaller work folder relative to the build script. |
| `_install_pyinstaller()` | Calls the active interpreter's `python -m pip install pyinstaller`; only used when requested and absent. |
| `main()` | Parses `--noconsole`, `--install-pyinstaller`, and `--force-config`; requires Windows, checks `psutil`/`requests`, invokes PyInstaller with `--clean --noconfirm --onefile`, chooses console mode, then copies/preserves external config. |

The script does not add `config.json` to the executable archive; it is deliberately distributed beside the binary.

### 5.3 Backend integration functions

| Function | File | Purpose |
|---|---|---|
| `_sensor_token_authorized()` | `app.py` | Requires a configured token of at least 32 characters and compares the `Bearer` token using `secrets.compare_digest`. |
| `enforce_csrf()` | `app.py` | Allows a valid sensor-token request specifically for `/api/ingest`; returns 401 for a Bearer header with an invalid token; otherwise preserves session-bound CSRF checks. |
| `ingest()` | `app.py` | Chooses sensor-token or current-admin authentication, requires an event list for sensors, invokes ingestion, and writes success/failure audit entries. |
| `ensure_logs_schema()` | `ingestion.py` | Ensures existing log table contains role/source/encrypted payload/ingestion/category/feature-status columns, fills legacy defaults, and migrates older sensor rows conservatively. |
| `store_events(events, source="api")` | `ingestion.py` | Validates event list/object shape, maps sensor aliases, stores unsupported sensor metrics as null, derives model-feature status, checks active-sector role/category, deduplicates event IDs, inserts logs, and commits. |
| `sanitize_event(event, source)` | `privacy.py` | Converts available detector fields to numeric values, preserves null measurements, chooses the event user/role, encrypts non-identity input fields, and returns storage values. |

## 6. Backend Integration

### 6.1 Actual route and access rules

The sensor uses the existing:

```text
POST /api/ingest
```

The route has two accepted authentication paths:

1. **Sensor:** a valid `Authorization: Bearer ...` matching `INSIGHTER_SENSOR_API_TOKEN`. A valid sensor token bypasses the route's normal session-CSRF gate and is then checked by the route itself.
2. **Existing desktop/admin:** current active user session plus CSRF token; the route checks that the active role is `admin`.

An invalid Bearer header receives 401 from the CSRF hook. A request without valid sensor authentication and without a valid administrator session is rejected. A valid session for a non-admin receives 403. The sensor token does not grant access to scores, sessions, alerts, or any other route.

### 6.2 Payload normalization

The sensor POST body is an object containing an `events` list with one event. The endpoint integration maps:

| Sensor field | Backend field |
|---|---|
| `user` (host-qualified) or fallback `username` / `email` | `logs.user` |
| `mb_transferred` | `data_transferred_mb` |
| `off_hours` | `off_hours_access` |
| Backend-derived quality labels | `feature_status` |
| `category` | `data_category` |
| `login_hour` | `login_hour` |
| `files_accessed` | `files_accessed` |
| `failed_logins` | `failed_logins` |
| `role` | `role` |
| server-selected ingestion source | `source = endpoint_sensor` |
| `event_id` | `ingested_event_ids.event_id` |
| Backend-derived `feature_status` | `logs.feature_status` |

The backend does not trust the event's `source` value to select the storage source; the authenticated sensor path calls `store_events(..., source="endpoint_sensor")`.

`sanitize_event()` parses available `login_hour`, file, and failed-login values as integers, transfer as float, and off-hours as a Boolean-like integer; it preserves explicit nulls. This is type conversion, not comprehensive numeric range validation. The backend derives sensor `feature_status`: file and failed-login features are unsupported, machine-wide network is not considered user-supported, and sampling hour/off-hours can be supported.

### 6.3 Validation, storage, and idempotence

`store_events()` requires a list and object for each event. For sensor events, it derives alias fields and checks:

- `role` belongs to the active sector's roles via `role_allowed()`.
- `data_category` belongs to the active sector's category list via `data_category_allowed()`.
- Optional `event_id` is a string of 1–128 characters.

The function creates `ingested_event_ids` if needed and prunes rows older than 30 days. It executes `INSERT OR IGNORE` for event IDs; a duplicate is skipped and does not insert a second log row. The sensor still gets a successful acknowledgement with `inserted: 0`. Because insertion and ID registration share the SQLite transaction, failed batch processing is not committed. For sensor events, the backend forces `files_accessed` and `failed_logins` to SQL `NULL`, uses `NULL` for unavailable network data, and stores non-sensitive per-feature statuses in the new `logs.feature_status` text column. Older sensor rows are migrated with conservative status values.

After normalization and validation, the event is written to the existing `logs` columns:

```text
user, login_hour, files_accessed, data_transferred_mb, failed_logins,
off_hours_access, role, source, payload_encrypted, ingested_at, data_category,
feature_status
```

`ingested_at` is set by the backend with `CURRENT_TIMESTAMP`, not copied from the endpoint timestamp. Non-identity source fields are encrypted by `privacy.encrypt_payload()` using `INSIGHTER_PII_SECRET`, falling back to `INSIGHTER_SECRET_KEY`. The user key remains plaintext for scoring and display.

### 6.4 Audit and error responses

The route records `INGEST_COMPLETED` or `INGEST_FAILED` in the existing audit log. Sensor operations use actor label `endpoint_sensor`; the bearer token is not used as the actor. Validation errors return a generic `{"error": "Invalid event data."}` response and HTTP 400. A valid insertion returns `{"status": "ok", "inserted": N}`.

The sensor expects the exact acknowledgement shape. Backend 400 indicates a permanent rejection and the event is dropped; 401/403 are retried while the event remains queued. Correct configuration and a valid token are therefore required before collection is deployed.

### 6.5 Scoring and display

`model.py` reads the `logs` table. Its existing feature list is:

```python
["login_hour", "files_accessed", "data_transferred_mb",
 "failed_logins", "off_hours_access"]
```

The existing model pipeline applies feature preparation/scaling, fits Isolation Forest and One-Class SVM, and blends their calibrated scores (60% / 40%) with a role-baseline deviation component. Before fitting/scoring, `_scoreable_observations()` checks that every configured feature is finite and marked `supported`. `_prepare_model_features()` now raises rather than silently filling missing values with zero. Historical rows without feature-status metadata retain their previous interpretation if all five numeric values exist. Endpoint events have unsupported file/login fields and machine-wide network values, so they do not pass the all-five-feature gate and are excluded from user-level scores and alerts. No sensor-specific scoring model was added.

Existing API/UI exposure:

- Admin `GET /api/sessions` returns the latest 150 raw log rows. The desktop `SessionsTab` displays user, role, source, measured fields, `Unavailable` for null values, and a usable-feature count with a scored/not-scored label.
- Admin `GET /api/scores` feeds the Overview and Profiles views. Endpoint events currently do not contribute a sensor user to the risk summary because they are not fully supported for the five-feature per-user model.
- Admin `GET /api/user-history?user=...` feeds Profiles history.
- Existing `GET /api/alerts` can return sensor-derived rows when the unchanged model flags them.

The UI does not currently display `telemetry_status`, `telemetry_metrics`, endpoint OS, sensor version, raw sensor timestamp, or disk counters. Those details are encrypted in `payload_encrypted`; `feature_status` (non-sensitive status labels only) is stored separately so Sessions can display data quality without decrypting the payload.

## 7. Telemetry Data Reference

“Direct” means a value is directly read from the local process/OS API; it does not imply per-user attribution. “Derived” identifies calculations from direct readings.

| Field | Type | Collection / source | Backend use | Limitations |
|---|---|---|---|---|
| `event_id` | String UUID | Generated by `uuid.uuid4()` per event | Duplicate suppression | Backend retains the ID up to 30 days; not a durable delivery receipt at endpoint. |
| `user` | String | Derived as hostname + `\` + `getpass.getuser()` | Existing `logs.user`, grouping, and Sessions display | Reports process account, which may be a Windows service account rather than interactive person. Stored plaintext; incomplete sensor events do not enter model scores. |
| `username` | String | Direct process OS username via `getpass.getuser()` | Fallback user mapping; encrypted payload excludes this key | Not an independently authenticated identity. |
| `endpoint_id` | String | Hostname | Encrypted source payload | Not a dedicated SQLite column or dashboard field. |
| `hostname` | String | `socket.gethostname()` with `platform.node()` fallback | Encrypted source payload | Hostname can be changed and is not a cryptographic device identity. |
| `os_info` | String | `platform.platform()` | Encrypted source payload | Informational platform string only. |
| `sensor_version` | String | Constant `1.0.0` | Encrypted source payload | Manually maintained; no auto-update mechanism. |
| `role` | String | External config | Validated against active sector and stored in `logs.role` | Must be manually configured correctly for the endpoint. |
| `category` | String | External config | Validated against active sector; mapped to `data_category` | Static configured label per endpoint, not independently observed classification. |
| `source` | String | Sensor sends `endpoint_sensor`; backend enforces that storage source | Stored as `logs.source` | Payload cannot override server-selected source. |
| `timestamp` | UTC ISO-8601 string | Sensor clock at event construction | Encrypted source payload | Not copied to `logs.ingested_at`; endpoint clock can be inaccurate. |
| `login_hour` | Integer 0–23 in normal operation | Local system clock at sample time | Existing model feature | It is sample hour, not a captured login event; backend does not impose a strict 0–23 range. |
| `off_hours` | Integer 0/1 | Derived from local hour `< 6 or > 21` | Mapped to model `off_hours_access` | A coarse fixed time rule; timezone and work schedule are not configurable. |
| `network_bytes_sent` | Integer or null | Direct `psutil.net_io_counters(pernic=False)` counter, then derived delta | Encrypted supplemental metrics only | Machine-wide; not user/application attribution. Null if unavailable. |
| `network_bytes_received` | Integer or null | Same as above | Encrypted supplemental metrics only | Machine-wide; not user/application attribution. |
| `network_bytes_total` | Integer or null | Derived sent delta + received delta | Used to derive transferred MB | Counts both directions; should not be read as uploaded/exfiltrated bytes. |
| `network_bytes_per_second` | Number or null | Derived total divided by sample-window seconds | Encrypted supplemental metrics only | Machine-wide average for the window. |
| `mb_transferred` | Number or null | Derived total bytes divided by 1,048,576, rounded to six decimals | Stored as `data_transferred_mb`; machine-wide values are not accepted as per-user model evidence | Aggregate bidirectional machine traffic. `null` means unavailable. |
| `files_accessed` | Null | No filesystem audit source | Stored as SQL `NULL`; counts as unavailable to scoring | Not collected; no claim that zero files were accessed. |
| `failed_logins` | Null | Windows Security log not read | Stored as SQL `NULL`; counts as unavailable to scoring | Not collected; no claim that zero logons failed. |
| `disk_read_bytes` | Integer or null | Direct host-wide psutil disk counter, then derived delta | Encrypted supplemental metrics only | Machine-wide; cannot be converted to files opened. |
| `disk_write_bytes` | Integer or null | Direct host-wide psutil disk counter, then derived delta | Encrypted supplemental metrics only | Machine-wide; cannot identify process, user, or file. |
| `measurement_window_seconds` | Number | Configured sample window | Encrypted source payload | Actual wall time can vary due to scheduling and sampling overhead. |
| `telemetry_status.network` | String | Derived from network counter availability/reset | Encrypted source payload | `machine_wide_delta`, `counter_reset`, or `unsupported`. |
| `telemetry_status.files_accessed` | String | Constant `unsupported_no_file_audit` | Encrypted payload | Explicitly reports unsupported collection. |
| `telemetry_status.failed_logins` | String | Constant `unsupported_windows_security_log_not_enabled` | Encrypted payload | Explicitly reports unsupported collection. |
| `telemetry_status.disk_io` | String | Current implementation sets `machine_wide_delta` | Encrypted payload | Per-direction `disk_read_status` and `disk_write_status` are the more accurate availability/reset indicators. |
| `disk_read_status`, `disk_write_status` | String | `safe_counter_delta()` | Encrypted payload | `available`, `counter_reset`, or `unsupported`. |
| Per-feature status values | JSON object/string | Backend mapping from metric availability/scope | `logs.feature_status`; model eligibility and Sessions quality count | `supported`, `unsupported`, or `machine_wide`; only `supported` is used by per-user scoring. |

`feature_status` is persisted as a compact JSON object of model-feature status strings. A `supported` value is required for every configured model feature before a row can be scored. The `machine_wide` network status is deliberately not considered supported for per-user scoring. Sessions shows the number of usable features and marks incomplete rows as not scored; the encrypted `telemetry_status` detail remains hidden from the UI.

## 8. Build and Deployment Process

### 8.1 Build configuration

`build_sensor.py` must be run on Windows. It verifies `psutil` and `requests` are importable, and checks whether `PyInstaller` is installed. It can install PyInstaller only when passed `--install-pyinstaller`. The command invokes:

```text
python -m PyInstaller --clean --noconfirm --onefile
  --name insighter-sensor
  --distpath <repo>\dist
  --workpath <repo>\build\sensor-agent
  --specpath <repo>\build\sensor-agent
  [--noconsole]
  <repo>\sensor_agent.py
```

Default build is console-enabled for development/debugging. Deployment option `--noconsole` suppresses the console; logs are written beside the executable. PyInstaller output is placed in `dist`. The script copies root `config.json` if `dist/config.json` does not exist, preserves an existing config by default, and overwrites it only with `--force-config`.

### 8.2 Build commands

From the repository root in an environment with the backend requirements:

```powershell
python -m pip install -r requirements.txt
python build_sensor.py
```

For the no-console deployment executable:

```powershell
python build_sensor.py --noconsole
```

If PyInstaller is not installed:

```powershell
python -m pip install pyinstaller
# or explicitly allow the build helper to install it:
python build_sensor.py --noconsole --install-pyinstaller
```

The output pair is:

```text
dist\insighter-sensor.exe
dist\config.json
```

PyInstaller build success proves the package was produced, not that it runs correctly on every endpoint. Exercise the binary and configuration on the target Windows image.

### 8.3 Admin PC setup

1. Configure normal InSighter application secrets and database as documented in the project README.
2. Set `INSIGHTER_SENSOR_API_TOKEN` on the backend host to a secret of at least 32 characters. Keep it out of source control, the sensor config file, logs, and command history.
3. For an isolated demonstration network, start the backend directly on the Admin PC. For example:

   ```powershell
   $env:INSIGHTER_HOST = "0.0.0.0"
   $env:INSIGHTER_PORT = "5051"
   python app.py
   ```

4. Determine the Admin PC's reachable private IPv4 address (or configure a stable internal DNS hostname). Allow inbound TCP only on the private/demo network and restrict the Windows Firewall rule to authorized endpoint machines where practical.
5. Do not expose Flask's built-in development server to the public internet. For a network beyond a trusted isolated demonstration, use a production WSGI server and an HTTPS reverse proxy with a valid certificate; set sensor `scheme` to `https`.

`INSIGHTER_HOST` affects the direct `app.py` startup branch. The desktop launcher is a separate local-backend startup path and remains loopback-oriented; launching it does not automatically make the backend available to other Wi-Fi devices.

### 8.4 Endpoint setup

1. Copy `insighter-sensor.exe` and `config.json` to a directory writable by the account that will run the sensor.
2. Edit `backend.host` to the Admin PC's IP or DNS hostname, and verify `port`, `scheme`, and `path`.
3. Choose matching role/category values from the backend's active sector:

   | Sector | Roles | Categories |
   |---|---|---|
   | `private_school` | `researcher`, `intern`, `admin_staff`, `it_staff` | `projects`, `private_documents`, `intellectual_property`, `pii` |
   | `uav_disaster_response` | `drone_operator` | `drone_platform_access`, `disaster_data` |

4. Set the environment variable named by `auth.token_env` (example: `INSIGHTER_SENSOR_API_TOKEN`) to the same backend token through an approved protected method. The token must have 32 or more characters.
5. Run the executable. It will not start collecting if the example host remains unchanged, the token is missing/short, or config validation fails.
6. Inspect `insighter_sensor.log` beside the binary for config, collection, delivery, retry, queue, and shutdown status. Do not put the executable in a read-only directory.
7. To stop a no-console build cleanly, create an empty `insighter_sensor.stop` file beside the executable. The sensor removes the request file and exits when it next checks for it. Console builds may also be stopped with Ctrl+C.

Changing the Admin PC's DHCP address requires updating endpoint configs unless a stable DNS name is used. The sensor does not discover network neighbors or dynamically locate the backend.

## 9. Security Considerations

### 9.1 Implemented controls

- Sensor secret is read from an environment variable, not stored in `config.json`.
- Sensor configuration rejects tokens shorter than 32 characters; backend sensor authorization also requires a configured token at least 32 characters long.
- Backend token comparison uses `secrets.compare_digest()`.
- The bearer token is accepted only for `POST /api/ingest`; it does not grant admin dashboard access or other APIs.
- Existing administrator ingestion continues to require the active admin session and normal CSRF checks.
- HTTP redirects are not followed by the sensor.
- HTTPS requests verify certificates; config validation refuses `tls.verify: false`.
- The backend checks the role against the active sector and checks sensor data category against the sector's allowed category list.
- Event IDs are deduplicated in SQLite for up to 30 days.
- The backend sanitizes numeric model fields and encrypts the remaining non-identity event data using the existing Fernet-based payload helper.
- Sensor logs do not intentionally print token values. Payloads are not written to local disk by the sensor's queue.

### 9.2 Actual limitations

- Example `scheme` is HTTP. HTTP does not encrypt or authenticate the transport. A network observer can capture or replay the shared bearer token and inspect event data. The sensor's token does not make HTTP confidential.
- The backend's direct Flask startup is a development server. `INSIGHTER_HOST=0.0.0.0` exposes it on interfaces but does not provide production TLS or production serving protections.
- One shared token is used by all sensors. There is no per-device token registration, revocation, identity binding, rotation protocol, or endpoint-specific permissions beyond ingestion-only route access.
- The endpoint is not cryptographically identified by `hostname`; the host-qualified user string can be spoofed by an authorized token holder.
- The in-memory retry queue loses data at restart and drops oldest events when full.
- `payload_encrypted` depends on the application's PII/signing key. Database fallback can be plaintext SQLite when SQLCipher is not installed.
- Raw `logs.user` is intentionally retained in plaintext for scoring, dashboard display, and history lookup. Compliance pseudonymization is not applied to the Admin Sessions or Profiles paths.
- Event ID table has no endpoint namespace; UUID collisions are extremely unlikely, but IDs are globally unique only by convention.
- There is no rate limit per sensor token in the route, no maximum request body size implemented specifically for sensor uploads, and no per-endpoint quota.
- The token environment variable must be available to the process. This document does not prescribe a secret store or claim that environment variables are protected from local administrators.

### 9.3 Defensible deployment

For an academic demo: use a dedicated/private hotspot or isolated Wi-Fi network, a long random token, restrictive firewall rules, and no internet-facing exposure. For real deployments: use HTTPS with a production WSGI server, unique revocable device credentials, controlled secret distribution/rotation, persistent encrypted delivery queue, audit/retention policy, server-side request limits, and documented incident response.

## 10. Testing and Verification

### 10.1 Automated tests

`test_sensor_agent.py` covers:

- Valid external configuration, placeholder/malformed host, sample-window constraints, and missing token.
- Host-qualified endpoint identity.
- Normal, reset, and unsupported counter deltas.
- Metric payload construction and support labels.
- Authenticated POST shape, acknowledgement parsing, redirect rejection, and temporary HTTP 429 behavior.
- Bounded queue and retention after transient failure.
- Stop-file graceful shutdown request handling.

`test_sensor_backend_integration.py` covers:

- Sensor payload alias normalization and SQLite persistence.
- Encrypted non-identity payload presence.
- Missing and machine-wide sensor metrics excluded from the existing per-user scoring result.
- Duplicate event acknowledgement without a duplicate `logs` row.
- Invalid token rejection.
- Rejection of role outside the active sector.

Existing `test_ingest_error_handling.py` also covers admin ingestion error handling, CSRF, non-admin denial, and valid event insertion. It was included in the focused test group.

### 10.2 Executed results

| Verification | Result |
|---|---|
| `python -m py_compile sensor_agent.py app.py ingestion.py build_sensor.py test_sensor_agent.py test_sensor_backend_integration.py` | Passed |
| Data-quality regression suite: sensor, ingestion, model calibration, roles/sectors, and desktop contracts | **57 tests passed** |
| Final `python scripts/run_tests_ci.py` | **308 tests passed**, no failures/errors |
| `git diff --check` | Passed |
| PyInstaller 6.22.3 Windows `--onefile --noconsole` build | Passed; executable produced in `dist` |
| Packaged executable invalid-example-config smoke test | Returned exit code 2 and wrote a JSON configuration error to `insighter_sensor.log` |
| Packaged executable against temporary loopback Flask server | Passed: authenticated POST returned HTTP 200; an endpoint row persisted in temporary SQLite |
| Packaged executable stop-file workflow | Passed: exit code 0; stop file removed |
| Endpoint row persistence and exclusion from per-user scoring due to unsupported features/scope | Passed in backend integration test |

One earlier smoke attempt used the no-console build via a process wrapper and returned an unhelpful wrapper exit status; inspecting its log showed the executable had written the expected placeholder-host error. The console-enabled build was then used to confirm process exit code 2 directly. A first focused run after adding the 32-character token policy failed because a test still expected the old test token; the assertion was corrected and the focused suite passed.

### 10.3 Not executed

- A clean Windows machine without Python was not available. The PyInstaller executable ran on Windows, but the build host itself has Python installed.
- Real network connectivity over a physical hotspot/Wi-Fi and Windows Firewall configuration were not tested.
- Real HTTPS with a production reverse proxy/certificate was not tested.
- Windows Security event-log permissions, audit-policy behavior, and failed-logon extraction were not tested because that feature is not implemented.
- Exact file access monitoring is not implemented and therefore was not tested.
- Visual PyQt UI automation on the target Admin PC was not run. Persistence/scoring were verified through backend integration and existing API data paths.
- Long-duration soak, queue exhaustion under a prolonged outage, high concurrency, and multi-endpoint load were not tested.

## 11. Limitations and Future Improvements

### Implemented

- Standalone endpoint sampling process and Windows one-file build.
- External configuration beside script/executable.
- Host/process identity, host-wide network/disk counter sampling, status metadata, event IDs, bearer-token POST, bounded retries, rotating logs, and stop-file shutdown.
- Backend normalization, ingestion-only token auth, sector validation, deduplication, SQLite storage, and use of the existing scoring fields.

### Partially implemented / constrained

- **Network monitoring:** host-wide byte counters work, but cannot attribute activity to user/process or distinguish upload from download in the model's `mb_transferred` scalar.
- **Disk monitoring:** read/write bytes are collected, but only as supplemental encrypted payload metrics, not model features or UI columns.
- **Unavailable metric representation and scoring gate:** implemented with SQL nulls and `feature_status`; endpoint events do not currently qualify for the five-feature per-user model.
- **Retry reliability:** transient retry is bounded and duplicate-safe, but storage is volatile memory only.
- **Endpoint identity:** host/account identifiers distinguish many endpoints but are not authenticated device identities.
- **Backend reachability:** a configurable address supports IP or DNS changes by editing config, but there is no automatic discovery.
- **No-console operations:** file-based stop request exists, but no Windows service manager integration or managed installation/update path is provided.

### Not implemented

- Windows Security event-log failed-login collection and bookmark/deduplication behavior.
- Exact file-open/access auditing.
- Per-user or per-process network/disk attribution.
- Persistent encrypted local spool, queue replay after reboot, or administrative queue inspection.
- Per-device credentials, central registration/revocation, token rotation, or device inventory.
- Native Google Workspace/Microsoft 365 endpoint connectors.
- A sensor inventory/health page or raw sensor payload viewer; Sessions displays model-feature completeness only.
- TLS termination or production WSGI server configuration in this component.
- Installer, Windows service registration, auto-start persistence, updater, or uninstall flow.

### Recommended next steps

Prioritize a secure production transport/deployment, per-device credentials, and durable encrypted queue before extending collection. If failed logins or exact file accesses are required, implement an explicit Windows audit-policy and permissions design with incremental event bookmarks, deduplication, and tests rather than inferring values from counters. Add an endpoint-aware detector only after obtaining genuine, appropriately attributable features and representative training data. Add visual end-to-end testing and a clean-machine package smoke test before a formal deployment.

## 12. Complete Operational Workflow

1. **Preparation:** The operator builds the sensor on Windows, copies the executable and external `config.json`, sets `INSIGHTER_SENSOR_API_TOKEN`, assigns a valid sector role/category, and enters the Admin PC's reachable address.
2. **Startup:** The executable resolves its own directory, opens `insighter_sensor.log`, reads JSON config, validates host, port, path, intervals, category/role, retry limits, TLS setting, and token presence/length. Invalid configuration is logged and terminates with exit code 2.
3. **Identity:** The process reads its OS account via `getpass.getuser()`, host via `socket.gethostname()` (fallback `platform.node()`), and OS description via `platform.platform()`. The detector user key becomes `HOSTNAME\username`.
4. **Sampling:** `collect_event()` reads host-wide network and disk counters; waits the configured sample window; reads them again; and calculates non-negative deltas. Local clock hour and off-hours flag are derived. An event UUID and UTC timestamp are generated.
5. **Quality-aware formatting:** Network MiB is set as `mb_transferred` when observed; otherwise it is `null`. Network is labeled `machine_wide`; unsupported files/failed-login measures are `null` with explicit status. The sensor queues this event in memory.
6. **Transmission:** `EventSender` posts one event to `/api/ingest`, with `Authorization: Bearer ...`, JSON body `{"events":[event]}`, a timeout, no redirects, and TLS verification enabled. Failed transient attempts remain queued and use exponential backoff.
7. **Backend authentication:** `enforce_csrf()` permits a correctly authenticated sensor token for this route only. `ingest()` selects the sensor branch and labels the audit actor `endpoint_sensor`. Admin session/CSRF ingestion remains a separate supported route behavior.
8. **Normalization and validation:** `store_events()` maps `username`/`user`, `mb_transferred`, `off_hours`, and `category` to the existing backend schema. It validates event object/list structure, active-sector role/category, and event-ID format. `sanitize_event()` casts model fields and encrypts non-identity source details.
9. **Persistence and replay handling:** A new event ID and normalized log row are committed in SQLite with `feature_status`. An already-seen event ID is skipped and acknowledged with `inserted: 0`; a new row produces `inserted: 1`. The backend assigns `ingested_at` at receipt time.
10. **Scoring:** On existing score/alert API requests, `model.py` reads logs and filters by active-sector role and all-five-feature completeness before using the Isolation Forest / One-Class SVM ensemble and role-baseline component. Current sensor events are excluded because file/failed-login measures are unavailable and network transfer is machine-wide. No sensor-specific score model was added.
11. **Visualization:** The Admin PyQt Sessions view shows the event, unavailable values, and usable-feature count/not-scored state. Current endpoint events do not appear in Profiles/Overview risk summaries or Alerts. Disk counters, endpoint OS, and source timestamp remain unavailable in current UI views.
12. **Recovery/shutdown:** Temporary network errors leave events in the bounded queue and retry. A full queue drops the oldest event with an error log. A clean stop-file or supported signal ends the loop and logs unsent queue count; those unsent events are discarded when the process exits.

This sequence is the current implemented path. It should not be interpreted as proof of user intent, exact file access, failed logon counts, or user-attributed network transfer.
