# InSighter Endpoint Sensor

## 1. Architecture findings

The current application is a PyQt6 client and Flask backend. Its real ingestion route is `POST /api/ingest`, which originally accepted an administrator's session-authenticated request (CSRF protected), then normalized event records into `logs`. The model consumes exactly these numeric fields: `login_hour`, `files_accessed`, `data_transferred_mb`, `failed_logins`, and `off_hours_access`. Event rows also carry `user`, `role`, `source`, `data_category`, an encrypted copy of non-identity input fields, and a server-side ingestion timestamp.

The new sensor is a separate Windows process, independent of the desktop app. It sends one event at a time to the existing endpoint and uses a bearer token configured through an environment variable. It does not add a new public endpoint or change either anomaly detector.

## 2. Backend compatibility changes

The sensor's requested wire format uses `username`, `mb_transferred`, `off_hours`, and `category`, whereas the current backend expects `user`, `data_transferred_mb`, `off_hours_access`, and `data_category`. The endpoint-sensor ingestion path maps those fields to the established names and continues to populate the existing detector columns.

Related backend changes:

- `ingestion.py` normalizes sensor aliases, checks sensor roles and data categories against the active sector, and uses an event-ID table for idempotent retries. Event IDs are retained for 30 days.
- `ingestion.py` adds the backward-compatible `logs.feature_status` column, backfills old sensor rows with conservative support statuses, stores current status metadata, and leaves unsupported numeric measurements `NULL`.
- `model.py` refuses to impute missing inputs as zero and excludes observations that do not have all five model features measured. This preserves existing scoring for complete historical/synthetic observations but means the current endpoint sensor does not produce user-level risk scores.
- `desktop_app/sessions_tab.py` displays unavailable values and the count of usable model features, distinguishing unscored sensor rows from complete observations.
- `app.py` accepts a configured bearer token only for `/api/ingest`; existing administrator session + CSRF behavior remains available. Sensor ingestion is separately audited as `endpoint_sensor`.
- `app.py` honors `INSIGHTER_HOST` when run directly. It still defaults to loopback (`127.0.0.1`) for local desktop operation.

The sensor-qualified user key (`HOSTNAME\username`) is stored for grouping and is visible in Sessions. A new `logs.feature_status` column records which detector inputs are supported for an observation. Historical sensor rows are migrated conservatively: file-access and failed-login values are marked unsupported, and network transfer is marked machine-wide. The current detector is per-user and requires all five inputs, so these sensor events are deliberately excluded from user-level model fitting and scoring until valid per-user network and audit measurements are available. Host OS and sensor metadata, raw counters, timestamp, and metric-status details remain in the encrypted event payload.

## 3. Collection behavior and limitations

- **Network:** `psutil.net_io_counters(pernic=False)` is sampled over the configured window. Sent and received byte deltas are added and mapped to `data_transferred_mb`. This is machine-wide traffic, not traffic attributable to the logged-in user or to a particular application.
- **Disk:** machine-wide read/write byte deltas are collected as supplemental telemetry in the encrypted event payload. They are not file counts.
- **Files accessed:** sent and stored as `null`. The encrypted `telemetry_status.files_accessed` and explicit `feature_status.files_accessed` mark the measurement unsupported. No filesystem audit source exists.
- **Failed logins:** sent and stored as `null`, with `unsupported_windows_security_log_not_enabled` and `feature_status.failed_logins = unsupported`. Windows Security event-log collection is not implemented. Reading event 4625 reliably requires Windows-specific permissions, audit policy, bookmarks, and duplicate handling; the sensor does not claim to monitor it.
- **Off-hours:** based on the endpoint's local clock and the existing InSighter rule (before 06:00 or after 21:00). The event timestamp itself is UTC ISO 8601.
- **Temporal field meaning:** `login_hour` is the local hour when the sensor sampled the endpoint, not a collected operating-system logon event.
- **Identity:** the process account from `getpass.getuser()` and hostname are used; the sensor does not guess the active interactive user using the first result from `psutil.users()`. A service running under a service account will therefore report that service account.
- **Unavailable values and scoring:** unsupported file/login fields and unavailable network values are `null`, not zero. The backend persists per-feature status in `logs.feature_status`. Only records with all five configured model features marked `supported` are scored; incomplete observations remain visible in Sessions with a usable-feature count and are excluded from model fitting, user summaries, and alerts. Machine-wide network transfer is retained as telemetry but does not count as a per-user model feature.
- **Offline operation:** failed events are queued in bounded memory and retried with exponential backoff. The queue is lost if the process exits or Windows restarts. When full, the oldest unsent event is dropped with an error log entry. The backend's event IDs prevent duplicates if a request reached the server but its acknowledgement was lost.
- **Scope:** this is a transparent monitoring process for authorized devices. It does not install persistence, evade controls, or change Windows security settings.

## 4. Files and dependencies

- `config.json` — editable external example; change its host and endpoint role/category before running.
- `sensor_agent.py` — configuration, identity, sampling, transport, bounded retries, and rotating JSON-line logs.
- `build_sensor.py` — Windows PyInstaller one-file build helper.
- `test_sensor_agent.py` — unit tests for configuration, metrics, identity, transport, and retry behavior.
- `test_sensor_backend_integration.py` — authenticated ingestion, field mapping, replay handling, and sector validation tests.

The sensor runtime uses `requests` and `psutil`, both already present in `requirements.txt`. `PyInstaller` is a build-time dependency only; the build helper installs it only when explicitly passed `--install-pyinstaller`.

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
5. Run `insighter-sensor.exe` and inspect `insighter_sensor.log`. A delivered event is visible in the Admin UI under Sessions with unavailable values and a usable-feature count. The current per-user detector requires all five inputs; endpoint events are not included in user-level scores or alerts because exact file/login values are unavailable and network traffic is machine-wide.

## 8. Verification and test status

The automated tests cover configuration validation, identity formation, safe counter deltas, host-wide metric labeling, HTTP request format and acknowledgement validation, bounded retry behavior, backend bearer authentication, sensor-field normalization, nullable unsupported metrics, persisted feature-status metadata, exclusion of under-supported observations from scoring, SQLite persistence, duplicate delivery, and rejection of roles outside the active sector.

**Executed in this workspace:** 308 repository tests passed; the focused sensor/backend/ingestion group passed (21 tests), and the broader data-quality regression group passed (57 tests). PyInstaller 6.22.3 built the one-file, no-console Windows executable. The packaged executable was then run against a temporary local Flask server: it authenticated, posted an event, persisted the host-qualified user/role/category in SQLite, and exited after the stop-file request. A config-validation failure was also verified to appear in the sensor log. `py_compile`, Ruff, and `git diff --check` passed.

The package was exercised on Windows, but this machine also has Python installed; a clean Windows machine without Python was not available for a separate installation-independent smoke test. Dashboard visibility is covered by the existing Sessions/Profiles APIs and persisted endpoint row; a separate visual UI automation run has not been performed.

## 9. Remaining work before production

- Persistent encrypted offline queue and per-device token registration/rotation.
- Windows Security event-log reader with explicit audit-policy and permissions setup.
- Audited file-event collection if exact file access is a required signal.
- HTTPS reverse proxy / production WSGI deployment and firewall hardening.
- Field validation and operational trial on representative Windows endpoint configurations.
- Visual demonstration on the target Admin PC and endpoint hardware.
