# InSighter — Current System Summary

**Snapshot:** 5 November 2026
**System type:** Local-first insider-threat monitoring proof of concept (POC)

This is a dated system snapshot; validation counts below describe the repository
state and test environment on 5 November 2026.

## 1. What the system is

InSighter is a desktop application for monitoring user activity and highlighting behavior that differs from learned patterns. It combines synthetic or normalized activity events, anomaly detection, risk summaries, alerts, role-based access, and optional AI-generated alert explanations. It is intended for demonstration and evaluation; an anomaly is a triage signal, not proof of malicious activity.

The primary runtime is a **PyQt6 desktop client** connected over loopback HTTP to a **Flask backend**. The backend owns authentication, authorization, event storage, scoring, and API responses. SQLite is the default store. It is not a hosted web application, and there is no active browser dashboard in the current client flow.

## 2. Architecture and data flow

1. **Start:** `desktop_app/main.py` starts or locates the local backend through `desktop_app/server_launcher.py`, then opens the sign-in window. Backend diagnostics go to `backend_startup.log`.
2. **Authenticate:** the desktop HTTP client (`desktop_app/api_client.py`) obtains a CSRF token, signs in, and calls role-protected endpoints. The server stores session data and login-throttle state in the database.
3. **Collect activity:** generated simulation events are used by default. Administrators may also push normalized events or pull from a configured REST source; an optional LDAP/Active Directory connector can collect a limited set of directory attributes.
4. **Protect and store:** event inputs are normalized and non-identity source fields are encrypted before being written to `logs`. The username remains available as the event key for analysis and is stored in plaintext.
5. **Score and alert:** the model analyzes recent stored activity, calculates per-user risk summaries, and produces prioritized alert records.
6. **Present and audit:** the desktop refreshes its current role’s views every five seconds. Administrative actions and authentication events are written to `audit_log`.
7. **Optional explanation:** an administrator can request a local Ollama model to explain an alert. If Ollama or the configured model is unavailable, the explanation feature reports that it is unavailable; the monitoring application can continue without it.

## 3. Active product functionality

### Activity sources and simulation

- Two deployment sectors are supported: **Private School** (default) and **UAV Disaster Response**.
- Private School has 12 generated users across researcher, intern, administrative-staff, and IT-staff roles. UAV has six generated drone operators.
- First-run synthetic seeding creates 120 records per roster user (1,440 Private School records or 720 UAV records). The seed is reproducible and uses sector-specific activity ranges and categories.
- A background simulator normally adds a new event about every six seconds. Its event profile is randomized, sector-aware, and sometimes anomalous; it is a simulation, not live UAV telemetry or real user monitoring. It can be disabled through `INSIGHTER_DISABLE_BACKGROUND_SIM=1`.
- The five detector inputs are login hour, files accessed, megabytes transferred, failed logins, and an off-hours indicator. Event records also carry a role, source, category, ingestion timestamp, and encrypted non-identity payload.

### Risk scoring and alerts

- The model uses **Isolation Forest** and **One-Class SVM**, with calibrated 0–100 scores. The blended detector score weights them 60% and 40%.
- Models are fitted by role when there is enough training data (at least 20 events and two users); otherwise the shared-data fit is used.
- A role-baseline deviation signal contributes an additional 10% component to the final score, capped at 100. Administrators can configure per-role expected values for files, transfer volume, failed logins, and off-hours fraction. Until setup is completed, learned non-anomalous role means are used.
- User risk labels use configurable thresholds (defaults: MEDIUM 30, HIGH 45). These **user-level labels** are distinct from alert severity.
- An event is flagged when either detector identifies it as anomalous. Alert severity is assigned separately from fixed file/transfer cutoffs (CRITICAL above 90 files or 600 MB; HIGH above 70 files or 400 MB; otherwise MEDIUM). Alerts include the triggering detector, event fields, computed scores, and a short rule-based description.
- Administrators can inspect per-user scores, recent activity history, sessions/log rows, paginated alerts, and alert explanations. Alerts can be sorted by priority or newest.
- Administrators can reseed the synthetic dataset or request a simulation tick.

### Roles and access

- **Admin:** full monitoring and configuration access, including user scores, detailed activity, alerts, audit log, integrations, deployment settings, baseline setup, reseeding, and ingestion.
- **Compliance:** a dedicated compliance view of alerts and audit events, with identifiers pseudonymized in those returned views.
- **Management:** a summary report in the desktop UI. The backend also permits authenticated access to selected aggregate or metadata endpoints such as summary, taxonomy, monitoring scope, integration status, and resource metrics; management is not limited to the summary endpoint at the API boundary.
- The backend reloads the active user and role from storage for protected requests. It uses salted PBKDF2-HMAC-SHA256 password hashes, login throttling, opaque server-side sessions with idle and absolute expiration, CSRF validation for mutating requests, and local-only login redirects.
- Demo credentials are opt-in and intended only for local demonstrations; they are not provisioned automatically.

### Configuration and operational views

- Sector taxonomy and event monitoring-scope counts are exposed to authenticated users; deployment settings and risk thresholds are administrator-controlled.
- Administrators can set up, update, and reset per-sector role baselines. Setup state and changes are persisted and audited.
- The Resources view reports backend process/host metrics and simulation-tick timings. LLM metrics represent actual local explanation calls; the displayed model benchmark table is historical data, not a live benchmark.
- The audit view records authentication, configuration, ingestion, baseline, and data-reseed events. Compliance output pseudonymizes identifiers in returned audit rows; this does not rewrite identifiers in the stored log.

### Optional integrations and AI

- **Windows endpoint sensor:** a standalone sensor is now implemented in
  `sensor_agent.py` and is packaged with PyInstaller using `build_sensor.py`. It
  submits authenticated events through the existing ingestion route, with endpoint
  identity, host-wide network/disk deltas, explicit unsupported-metric markers, and
  bounded retries. Unsupported fields are now stored as null with `feature_status`;
  incomplete sensor events are excluded from user-level scoring because the current
  detector requires all five measured features. They remain visible in Sessions with
  a usable-feature count. See [ENDPOINT_SENSOR.md](./ENDPOINT_SENSOR.md). It is not a
  file-auditing or Windows Security event-log agent.
- **REST ingestion:** an administrator can push event arrays to the ingestion endpoint or configure a URL for pull ingestion. Optional bearer-token configuration is supported. Input is normalized, checked against active-sector roles, sanitized, and appended rather than replacing existing data.
- **Active Directory:** optional LDAP bind/search support maps account name, last-logon information, bad-password count, title, and department. It does not provide file-access or data-transfer observations, so those detector fields are zero for these imported records.
- **Google Workspace and Microsoft 365:** there are no dedicated native provider clients in this codebase. They can be named as configured REST sources, but real data availability depends on an externally supplied API endpoint and compatible normalized response.
- **Ollama:** local alert explanation defaults to `phi4-mini` and can use a configured local model. Prompt rules constrain explanations to supplied data and proportionate follow-up. Warm-up is background work and can be disabled.

## 4. Working modules

| Area | Main files | Responsibility |
|---|---|---|
| Backend/API and lifecycle | `app.py` | Flask initialization, CSRF, routes, simulation, authorization boundaries, resource metrics, and orchestration |
| Authentication | `auth.py` | Password verification, user bootstrap/migration, throttling, database-backed sessions, and access decorators |
| Persistence | `database.py` | SQLite connections, schema support, optional SQLCipher use, and legacy database migration |
| Event creation and scoring | `generate_logs.py`, `simulation.py`, `model.py` | Synthetic seeding, sector-specific event profiles, detector ensemble, role scoring, alerts, and live event insertion |
| Sector and baselines | `sector_config.py`, `role_baseline.py`, `risk_thresholds.py` | Supported taxonomies, active-sector helpers, role baselines, and risk-threshold validation |
| Privacy and ingestion | `privacy.py`, `ingestion.py` | Event normalization, identifier hashing, payload encryption, REST ingestion, and optional LDAP/AD ingestion |
| AI and diagnostics | `llm_advisor.py` | Ollama availability, bounded alert explanations, warm-up, usage statistics, and benchmark display data |
| Desktop startup and API | `desktop_app/main.py`, `server_launcher.py`, `api_client.py`, `async_utils.py`, `connection_state.py` | Backend launch, HTTP/CSRF client, background requests, and connection/session status |
| Desktop views | `desktop_app/main_window.py`, `dashboard_tab.py`, `sessions_tab.py`, `profiles_tab.py`, `role_sessions_tab.py`, `alerts_tab.py`, `summary_tab.py`, `compliance_tab.py`, `audit_tab.py`, `integrations_tab.py`, `resources_tab.py`, `role_baseline_setup_tab.py`, `uav_config_tab.py` | Role-specific navigation and operational screens |
| Offline evaluation | `cert_adapter.py`, `cert_score.py`, `cert_experiment.py`, `llm_benchmark_review/` | CERT dataset adaptation/scoring experiments and historical LLM benchmark artifacts; these are evaluation utilities, not the live application’s data connectors |

The admin desktop navigation contains Overview, Sessions, Profiles, Role Sessions, Alerts, Resources, Audit, Integrations, Role Baseline Setup, and Deployment/UAV Configuration. Management sees Summary Report; compliance sees Compliance Audit.

## 5. Data and security boundaries

- Core data lives in SQLite tables including activity logs, users, deployment configuration, audit events, authentication sessions, login throttles, and role-baseline setup/values.
- SQLCipher is used only when its optional Python binding is installed and a database key is supplied. Otherwise the application uses ordinary SQLite; setting `INSIGHTER_DB_KEY` alone does not encrypt a plaintext database.
- Stored usernames and audit actors are not encrypted. The encrypted event payload preserves non-identity source details; pseudonymization applies to compliance responses, not the raw records in storage.
- The desktop launcher’s HTTP backend is intended to bind to loopback. Its local cookie-security override is not suitable for network exposure. A deployed service needs externally managed secrets, HTTPS, protected database/backup files, and a supported operational setup.
- SQLite-backed sessions and throttling target a single backend instance. Horizontal scaling requires a shared transactional store or another multi-instance-safe design.
- This is a risk-triage prototype, not an autonomous response system: model scores can be noisy and do not establish intent or compromise.

## 6. Tests and evidence

The repository includes tests for authentication and RBAC, recovery/demo accounts, desktop startup and async behavior, ingestion errors, model calibration, thresholds, baselines, sector behavior, audit/compliance, resources/LLM behavior, and CI safeguards. `scripts/run_tests_ci.py` discovers Python `unittest` tests and checks that tests do not unexpectedly mutate tracked files.

**Validation snapshot (5 November 2026):** `python scripts/run_tests_ci.py` completed successfully: **480 tests passed**, with 24 skipped in the base environment (22 optional WebAuthn tests and 2 OpenCV-dependent tests). With `webauthn==3.0.1` installed, all 480 tests passed with only the 2 OpenCV-dependent tests skipped; each configuration passed three consecutive runs. The Windows endpoint sensor's packaged-executable smoke test is an earlier, separately recorded result and was not repeated for this snapshot. Ruff and `git diff --check` passed.

## 7. Current limitations and deployment caveats

1. Synthetic events are the default and demonstrate the end-to-end workflow; they are not evidence of actual organizational activity.
2. External data coverage is conditional. AD data is limited to directory/logon metadata; Google Workspace and Microsoft 365 depend on a compatible external REST API rather than built-in provider integrations.
3. The current detector uses five coarse behavioral fields and heuristic alert descriptions/severity cutoffs. It is not a complete insider-threat investigation or incident-response platform.
4. Database-at-rest encryption is optional and environment-dependent. In its SQLite fallback mode, the database is plaintext.
5. Local Ollama availability/model performance is machine-dependent. Explanations are optional and do not affect scoring.
6. The CERT pipeline and benchmark files are research/evaluation assets, and their presence does not indicate active CERT data ingestion.
7. For production use, secret provisioning, HTTPS, database protection, account lifecycle, monitoring, retention, recovery, and multi-instance storage need deployment-specific controls.

## 8. Running locally

Follow [How to run the current system.txt](./How%20to%20run%20the%20current%20system.txt) for the documented setup. At a high level, install `requirements.txt` and `requirements-desktop.txt`, provision a secure application secret (or use the desktop launcher’s local key generation), then run:

```powershell
python desktop_app/main.py
```

Ollama and `phi4-mini` are required only for local AI explanations. Demo-account creation is optional and must remain local.
