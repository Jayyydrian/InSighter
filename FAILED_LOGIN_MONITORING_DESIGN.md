# Optional Failed-Login Monitoring: Design and Operating Reference

## 1. Purpose and scope

This document describes two independent mechanisms that can observe failed authentication:

1. **InSighter administrator dashboard authentication**, handled by the Flask application and `auth.py`.
2. **Optional Windows endpoint failed-logon telemetry**, collected by `failed_login_monitor.py` and sent by `sensor_agent.py`.

The first is an authentication-protection control for InSighter accounts. The second is an opt-in aggregate telemetry feature for a narrowly defined Windows account. They do not share counters, storage, or a detection algorithm. Windows endpoint counts are not a mirror of the InSighter login throttle.

The endpoint feature is implemented but disabled by default. It neither enables audit policy nor changes Windows permissions. It is not a full endpoint account inventory, a credential monitor, nor a reliable measure of all interactive sign-in failures.

## 2. InSighter dashboard login behavior

`auth.py` implements the backend's own login throttle:

| Property | Behavior |
|---|---|
| Key | SHA-256 digest of normalized username plus `request.remote_addr`; raw pair is not used as the stored key |
| Failure window | 15 minutes |
| Threshold | Three failed attempts |
| First cooldown | 60 seconds |
| Repeated lockouts | Backoff grows exponentially after the threshold and is capped at one hour |
| Persistence | `login_throttle` SQLite table |
| Success | Correct login clears the corresponding throttle row |
| Audit | Login success/failure, cooldown, and logout actions are written to `audit_log` |
| Enforcement | Dashboard session/login flow; it does not depend on Windows event-log access |

The user-facing message on cooldown does not expose whether the username exists. This rate-limits attempts against InSighter dashboard accounts by username/IP pair; it does not count Windows Security events or failed logons to arbitrary local/domain accounts. Different client IP values result in separate throttle keys.

Password verification uses the existing salted PBKDF2-HMAC-SHA256 implementation and constant-time digest comparison. The optional endpoint feature did not change `auth.py`, the password scheme, login limits, session behavior, or dashboard authentication routes.

## 3. Windows endpoint collector

### 3.1 Enablement and dependencies

The optional setting is:

```json
"failed_login_monitoring": {
  "enabled": false
}
```

The checked-in configuration is disabled. A missing setting also defaults to disabled, preserving older sensor configurations. Enable only on Windows and only after an administrator has independently configured and verified Audit Logon failure auditing and granted the process enough rights to query the Security log. The collector does not elevate, configure policy, or request credentials.

The implementation uses the Python standard library plus Windows `auditpol.exe` and PowerShell `Get-WinEvent`. PowerShell subprocesses have a 10-second timeout. Collection runs as part of the existing sensor cycle; it is not a Windows service or independent agent.

### 3.2 Startup and sampling flow

1. `sensor_agent.py` obtains the process account from `getpass.getuser()` and constructs `FailedLoginMonitor(enabled, target_user, ...)`.
2. `FailedLoginMonitor.start()` returns `disabled` if not enabled, `unavailable` on a non-Windows host or inaccessible query, and `not_configured` if Logon failure auditing is not confirmed.
3. If audit policy is confirmed, it queries the newest Security record and stores its record ID as an in-memory starting cursor. Older events are not backfilled.
4. On subsequent sensor observations, `observe()` queries Event ID 4625 records with IDs after the prior cursor and no later than the latest observed record.
5. It reads only `TargetUserName` from each event and counts records matching the process account. Matching is case-insensitive; a domain-qualified process identity can match its account-name portion.
6. It advances the cursor after a successful query and reports the count and observation-window duration. No event XML, per-event timestamp, source address, target identity, logon type, password, or other event detail is transmitted.
7. The sensor places the aggregate value/status in its normal endpoint event and uses its existing bearer-token sender.
8. Backend ingestion validates the status and count before persisting a supported value. It does not derive or substitute a zero when the value is absent.

Principal implementation functions:

| Function / member | Responsibility |
|---|---|
| `_audit_failure_enabled(output)` | Parse `auditpol` CSV output and determine whether failure auditing is enabled for the Logon subcategory |
| `_account_name_matches(target_user, process_user)` | Match case-insensitively against the process account/full qualified name or its short account component |
| `FailedLoginMonitor._auditpol_path()` | Find `auditpol.exe` using `PATH` or the Windows system directory |
| `FailedLoginMonitor._check_audit_policy()` | Perform the read-only policy query and classify it as available, not configured, or unavailable |
| `FailedLoginMonitor._read_latest_record_id()` | Read the newest Security event record ID to establish a non-backfilling cursor |
| `FailedLoginMonitor.start()` | Validate platform, policy, and access; initialize cursor and monotonic sample time |
| `FailedLoginMonitor._read_new_events(cursor)` | Query new 4625 records, parse the bounded JSON response, and validate types |
| `FailedLoginMonitor.observe()` | Count matching records, advance the in-memory cursor, measure interval, and return a `FailedLoginObservation` |

### 3.3 Status and value contract

`FailedLoginObservation` contains `count`, `status`, and optional `observation_window_seconds`.

| Status | Count | Meaning |
|---|---:|---|
| `disabled` | `null` | Configuration is off |
| `not_configured` | `null` | Failure auditing is not enabled/confirmed or the policy subcategory cannot be identified |
| `unavailable` | `null` | Not Windows, `auditpol` is missing/denied, or initial Security log access fails |
| `error` | `null` | A later query fails, returns malformed data, times out, or indicates a log reset |
| `available` | Nonnegative integer | A successful observation, including `0` when no matching event occurred |

The count `0` is a valid observation, not an error or missing value. The backend treats the field as measured only when `telemetry_status.failed_logins` is exactly `available` and the value is a nonnegative integer (booleans are rejected). All other states are stored as SQL `NULL` and marked unsupported in `logs.feature_status`.

When Security Event access fails, the collector returns a null count and the rest of the sensor continues. In the packaged smoke test, both optional endpoint monitors were enabled while Windows audit-policy access was unavailable; the sensor logged that status and shut down normally using its stop file.

## 4. Attribution, privacy, and measurement limits

- **Attribution is to the sensor process account.** A scheduled task or service running as `SYSTEM`, a domain service account, or another non-interactive user will count only matching failures for that process account. It does not infer the currently logged-in desktop user.
- **Not all failures necessarily appear as 4625.** The collector relies on the configured Windows failure audit policy and records available to the process.
- **No persistence of cursor.** The latest record ID is held in memory. Restart begins at the newest record, so failures while stopped and events before startup are not replayed.
- **No full logon-event history.** The sensor reports counts since its latest successful poll. It does not store individual event IDs, names, timestamps, source addresses, or XML.
- **No authorization changes.** Audit policy, local security groups, event-log ACLs, and privileges remain administrator-managed.
- **No guarantee of exactly-once counts across outages.** A failed query returns `error` and does not report a fabricated count. The in-memory cursor and next successful read determine the observed range; restarts intentionally skip history.
- **No model feature expansion.** The existing model has exactly five features. The new collector only makes the `failed_logins` feature potentially supported. Endpoint `files_accessed` is still unsupported, and endpoint network traffic is machine-wide; consequently an endpoint record remains ineligible for the existing per-user scoring path.

## 5. Data flow and storage

An endpoint observation is sent through the existing `POST /api/ingest` route using the shared sensor bearer token. No new route, database table, or migration was added for this feature.

`ingestion.store_events()` retains the reported `failed_logins` value only if both the `available` status and a valid nonnegative integer are present. It records the support state in `logs.feature_status` and preserves other source data through the existing sanitization/encryption behavior. A disabled or inaccessible collector therefore does not become an artificial zero in Sessions or the model input frame.

The existing model and dashboard behavior are unchanged:

- `model.FEATURES` remains `login_hour`, `files_accessed`, `data_transferred_mb`, `failed_logins`, and `off_hours_access`.
- Eligibility still requires all existing features to be genuinely supported.
- Isolation Forest, One-Class SVM, role baselines, feature engineering, and alert generation were not changed.
- Dashboard `failed_logins` values from administrator/model-compatible data remain distinct from optional endpoint collector status.
- Dashboard login failures continue to appear in the authentication audit trail, not as sensor telemetry.

## 6. Security model

The collector only reads the event-log information needed for an aggregate count and does not send event identities or details. Sensor transport still uses the existing shared bearer token and configured HTTP(S) endpoint. HTTPS certificate verification is required by sensor configuration, but the example configuration uses HTTP; on HTTP the token and event data are not transport-encrypted. Failed-login data uses no special endpoint credential, encryption, or new API authorization mechanism beyond the existing ingestion path and application payload handling.

The shared token is not device identity. A token holder can submit endpoint telemetry accepted by the existing ingestion authorization path. Restrict its distribution and the network route; use HTTPS in a controlled production deployment. Do not treat this collector as a replacement for Windows security auditing or an authoritative SIEM.

## 7. Verification evidence and deployment status

Automated tests in `test_failed_login_monitor.py` mock Windows policy and event-query responses, including successful zero counts, account matching, cursor behavior, unavailable policy/access, malformed responses, errors, and log reset. Sensor tests cover default-off configuration and nonfatal unavailable behavior. Backend integration tests cover valid zero versus `NULL` and feature-status handling. The full suite passed 340 tests (0 failures); the focused authentication/sensor/backend set passed 49 tests.

Live Windows Security event collection was **not verified** on the test host:

- `Get-WinEvent -LogName Security -MaxEvents 1` failed with `UnauthorizedAccessException`.
- `auditpol.exe /get /subcategory:Logon /r` failed with privilege-not-held error 1314.

Therefore the implementation's parsing/counter logic is mock-tested, and the optional failure path is package-smoke-tested, but successful extraction from a real Windows Security log remains unverified. Any authorized field test should explicitly check the effective audit policy, process account permissions, a controlled test failure, expected event/account match, aggregate count, and null-vs-zero persistence. Do not create real failed-login attempts against a production or third-party account as a test.

## 8. Operational troubleshooting

| Observed state | Interpretation / safe next action |
|---|---|
| No failed-login status because feature is disabled | Confirm external config path and `failed_login_monitoring.enabled`; omission means off |
| `not_configured` | An administrator should inspect effective Audit Logon failure policy; the sensor does not modify it |
| `unavailable` at startup | Check Windows platform, `auditpol.exe` availability/permission, Security log ACL, and process account |
| `error` after successful start | Check Security log access, PowerShell availability/timeouts, and whether the log was cleared/reset |
| `available` with `0` | A successful poll found no matching records for the process account in the observed range |
| Count differs from interactive sign-in failures | Check whether the sensor runs under a service account, whether events are 4625, effective audit policy, and cursor/restart gaps |

Do not resolve collection errors by silently writing zero, enabling audit policy from the sensor, running with elevated privileges by default, or weakening backend validation.
