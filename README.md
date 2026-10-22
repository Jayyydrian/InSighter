[![CI](https://github.com/Jayyydrian/InSighter/actions/workflows/ci.yml/badge.svg)](https://github.com/Jayyydrian/InSighter/actions/workflows/ci.yml)

# InSighter 

AI-assisted insider threat detection.
Runs locally in under 2 minutes.

---

## What it does

- Generates synthetic activity logs from the active sector's roster
- Runs Isolation Forest anomaly detection on each user's behavior
- Displays a live risk dashboard with scores, alerts, and charts
- Rotates simulated anomalous activity across users instead of assigning one fixed insider identity

---

# InSighter — Mini POC

AI-assisted insider threat detection proof-of-concept.

Two ways to run it:
1. **Desktop app (PyQt6)** — matches Chapter 3's Presentation Tier spec. Recommended.
2. **Web dashboard (Flask + browser)** — kept for quick testing/demo convenience.

Both talk to the same backend (Logic + Authentication + Data tiers).

---

## What it does

- Generates sector-specific synthetic activity for 12 private-school users or 6 UAV disaster-response operators
- Runs Isolation Forest + One-Class SVM (weighted 0.6/0.4 ensemble) on each user's behavior
- Builds role-aware behavioral baselines and adds baseline deviation to risk scores
- Varies anomalous and routine activity per live simulation tick; UAV simulation uses mission-appropriate numeric profiles and alert language
- Accepts protected events from a configured REST API through the desktop Integrations page
- Hashes identifiers and encrypts non-identity event payloads before storage
- Uses SQLCipher encryption automatically when `sqlcipher3` is installed, with SQLite fallback for development
- Login + RBAC: `admin` (full access), `compliance` (pseudonymized records), and `management` (summary-only), per Chapter 3
- Displays a live risk dashboard with scores, alerts, user behavior profiles, and a UAV Configuration Module

## Supported sectors and legacy databases

The supported deployment sectors are `private_school` and `uav_disaster_response`.
`private_school` is the default for new deployments.

When an existing database contains the removed `sme_startup` sector, startup switches
the deployment to `private_school` and writes one `SECTOR_MIGRATED` audit entry with
actor `system`. Existing logs and legacy `sme_startup` rows in `role_baselines` and
`baseline_setup_status` are retained, but the legacy baseline rows are unused by the
active sector. Existing logs remain stored but do not provide dashboard data for the
new sector; an administrator should reseed synthetic data so the dashboard shows
activity for the `private_school` roster.

Benchmark records under `llm_benchmark_review/` are historical artifacts and include
SME/startup data; they do not indicate a currently supported sector.

## Admin role baseline setup

The admin-only first-run setup lets an administrator declare a center of normal for each role and feature. A sector is either **not configured** (built-in learned defaults are used) or **configured**. Values and setup status are stored per sector and survive log reseeding.

A declared value replaces the learned per-role mean only in the role-deviation signal. Per feature, deviation is `abs(actual - declared) / declared`; when the declared value is 0, a positive actual contributes a ratio of 1. The resulting signal is one component of the final score with weight 0.1, so it can contribute at most 10 of 100 points. IsolationForest and One-Class SVM do not use declared values; their inputs, fitting, calibration, and risk thresholds are unchanged.

The detectors learn each role from that role's own activity. Applying a uniform scale to both fit and scored inputs therefore does not reliably move detector judgment to an administrator's center; prototype measurements produced inconsistent, small changes, so detector-input transforms were rejected. In one generated-data experiment initialized at the selected role's generated data-transfer mean, doubling or halving the baseline moved scores by less than 1 point and changed at most one user's risk level. This result is indicative, not a guarantee for other data.

Values are stored and scored per logged event (all minimums are 0): `files_accessed` max 155 files/event (ceil of 1.5 x largest sector p99, 103.22); `data_transferred_mb` max 1,202 MB/event (ceil of 1.5 x 800.7861); and `failed_logins` max 13.5 attempts/event (1.5 x 9). The p99 values are from the supported generated sectors. `off_hours_access` is a fraction of events in [0, 1]; its p99 is 1 and its maximum is capped at the indicator's natural maximum. In the desktop wizard, `files_accessed` displays as files per event, `data_transferred_mb` as MB per event, `failed_logins` as failed logins per 100 events, and `off_hours_access` as a percentage of events; displayed whole numbers do not change the per-event values stored or scored. A soft warning appears when a declared value is more than 5x above or below the observed role mean; warnings never block saving.

**Reset to defaults** removes that sector's declared values and returns it to **not configured**. Setup completion, changed values, and resets are recorded in the audit log as `BASELINE_SETUP_COMPLETED`, `BASELINE_UPDATED`, and `BASELINE_RESET`. A value far from the activity actually observed for its role will mostly raise that role's role-deviation signal for everyone in that role.

---
## Quick Start — Desktop App (recommended)

```bash
pip install -r requirements.txt -r requirements-desktop.txt
python desktop_app/main.py
```

`requirements.txt` installs the backend dependencies; `requirements-desktop.txt` adds
PyQt6 for the desktop client. Before the first start, provision the initial administrator
through `INSIGHTER_ADMIN_USERNAME` and `INSIGHTER_ADMIN_PASSWORD`. The password must be
at least 14 characters. The credentials are consumed only when no active administrator
exists; later starts do not reset an existing account. Supply `INSIGHTER_SECRET_KEY` and
`INSIGHTER_PII_SECRET` from a secret manager, and set `INSIGHTER_COOKIE_SECURE=1` when
serving over HTTPS. The desktop launcher uses `INSIGHTER_COOKIE_SECURE=0` only for its
loopback HTTP backend; do not expose that backend to a network.

The backend uses SQLite-backed opaque sessions with idle and absolute expiry. For a
multi-instance deployment, place the database on storage with reliable SQLite locking or
move session storage and login throttling to a shared Redis/database service before
horizontal scaling. Enable SQLCipher and provide `INSIGHTER_DB_KEY` where database-at-rest
encryption is required.

Production environment checklist:

- Set `INSIGHTER_SECRET_KEY` to a stable, high-entropy secret from a secret manager.
- Set `INSIGHTER_PII_SECRET` to independent high-entropy key material.
- On first provisioning only, set `INSIGHTER_ADMIN_USERNAME` and
  `INSIGHTER_ADMIN_PASSWORD` (minimum 14 characters); remove these variables after
  the first successful bootstrap.
- Set `INSIGHTER_COOKIE_SECURE=1` and terminate TLS at a trusted HTTPS endpoint.
- Provide `INSIGHTER_DB_KEY` when using SQLCipher. Protect SQLite database files and
  backups with restrictive filesystem permissions and encrypted storage.
- Use one shared, durable database for sessions and throttle state. SQLite is intended
  for a single backend process; use a shared transactional store before scaling out.
- Rotate signing, PII, and database keys under a documented rotation procedure; do not
  log their values or place them in source control.

### Local AI Analysis

Install Ollama, start its local service, then pull the default explanation model:

```bash
ollama pull phi4-mini
```

The AI Analysis button uses `phi4-mini` by default. Set `INSIGHTER_LLM_MODEL` to use a
different local model; the legacy `INSIGHTER_OLLAMA_MODEL` variable remains a fallback
override.

### Local LLM performance

The backend warms the configured model in a daemon thread at startup. It does not delay
the server becoming available; set `INSIGHTER_LLM_WARMUP=0` to disable it. Warm-up waits
up to 120 seconds by default (`INSIGHTER_LLM_WARMUP_TIMEOUT`) and can also be disabled
along with other background work by setting `INSIGHTER_DISABLE_BACKGROUND_SIM=1`.

Ollama's request timeout is configurable with `INSIGHTER_LLM_TIMEOUT` (90 seconds by
default, with a 3-second connection timeout). The former
`INSIGHTER_OLLAMA_TIMEOUT_SECONDS` remains a fallback for existing configurations.
The desktop's `INSIGHTER_AI_TIMEOUT` defaults to 120 seconds and is automatically kept at
least 30 seconds above the configured server timeout. You can raise it further if desired.

Generation settings are `INSIGHTER_LLM_NUM_CTX` (2048 tokens by default),
`INSIGHTER_LLM_MAX_TOKENS` (256), and `INSIGHTER_LLM_KEEP_ALIVE` (`30m`). The normal
generated system and alert prompt together currently use about 1,370 tokens at the
longest observed alert, leaving space for generation in the default context.
Check `prompt_eval_count` for several real alerts in `llm_usage_log.jsonl`. If counts
approach 1,700, raise `INSIGHTER_LLM_NUM_CTX` to `3072`; a context that is too small
can truncate the beginning of the prompt, including its safety rules.

For best speed, check `ollama ps` and aim for `100% GPU`. If Ollama reports CPU usage,
lower `INSIGHTER_LLM_NUM_CTX`, close GPU-heavy apps, or optionally set
`OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` **before starting Ollama**.
These two `OLLAMA_*` variables configure the Ollama server, not InSighter. During demos,
`INSIGHTER_DISABLE_BACKGROUND_SIM=1` also frees CPU by disabling background simulation.
The first request after a long idle may take longer while Ollama reloads the model. For
demos, set `INSIGHTER_LLM_KEEP_ALIVE=2h` to keep it loaded longer.

---

## Quick Start — Web Dashboard (alternative)

```bash
pip install -r requirements.txt
python app.py
```
Open `http://localhost:5051` and log in with the same accounts above.

The database is auto-seeded on first run.
Use the **↺ Reseed Data** button to regenerate fresh logs anytime.

### REST ingestion and protected storage

Configure a compatible event source before using **Ingest Events** in the desktop
Integrations page:

```powershell
$env:INSIGHTER_SOURCE_API_URL = "https://your-source.example/api/events"
$env:INSIGHTER_SOURCE_API_TOKEN = "your-bearer-token"  # optional
$env:INSIGHTER_SOURCE_PROVIDER = "google_workspace"
python desktop_app/main.py
```

The source response may be a list or an object containing an `events`, `records`,
`data`, or `logs` list. Each event needs a user identifier and the activity fields
used by the detector. Set `INSIGHTER_PII_SECRET` to control payload encryption.
For encrypted database storage, install a compatible `sqlcipher3` build and set
`INSIGHTER_DB_KEY`; existing plaintext databases are migrated to `database.db.enc`.

### Active Directory ingestion

Install the dependencies and configure an AD bind account before starting the backend:

```powershell
pip install -r requirements.txt
$env:INSIGHTER_SOURCE_PROVIDER = "active_directory"
$env:INSIGHTER_AD_SERVER = "dc.example.com"
$env:INSIGHTER_AD_PORT = "636"       # 389 for plain LDAP
$env:INSIGHTER_AD_USE_SSL = "true"   # false for plain LDAP or a mock server
$env:INSIGHTER_AD_USER = "insighter@example.com"
$env:INSIGHTER_AD_PASSWORD = "your-password"
$env:INSIGHTER_AD_SEARCH_BASE = "DC=example,DC=com"
python app.py
```

The connector searches person/user objects and maps `sAMAccountName`,
`lastLogon`/`lastLogonTimestamp`, `badPwdCount`, `title`, and `department` into the
normalized event fields consumed by the risk model. Missing AD-specific fields become
zero or `unknown`; file access and transfer fields are zero because AD logon metadata does
not provide those measurements. All imported records go through `privacy.sanitize_event`
before insertion.

The `/api/integrations` endpoint performs a bind check and reports Active Directory as
`connected` only when the required settings are present and the bind succeeds. Missing
settings, a missing `ldap3` installation, or a failed bind reports `not_connected` without
preventing the rest of the application from starting.

To test without a real directory, use `ldap3`'s `MockSyncStrategy`: create a mock
`Server(..., get_info=OFFLINE_AD_2012_R2)`, add entries containing `objectCategory=person`,
`sAMAccountName`, AD FILETIME `lastLogon`, and `badPwdCount`, then pass the mock
`Connection` to `fetch_active_directory_events()` or `ingest_from_active_directory()`.
Set the same `INSIGHTER_AD_*` variables shown above, use `INSIGHTER_AD_USE_SSL=false`,
and point `INSIGHTER_AD_SEARCH_BASE` at the mock base. The connector accepts an injected
connection specifically to make this test deterministic and network-free.

The repeatable version of this check is included in `test_ad_integration.py`; run it from
the project root with:

```powershell
python -m unittest test_ad_integration.py
```

---

## Project Structure

```
Insighter/
├── app.py                     ← Flask backend (Logic + Auth + Data tiers)
├── auth.py                    ← BCrypt hashing, RBAC decorators, users/uav_config tables
├── generate_logs.py           ← Synthetic log data generator
├── database.py                ← Shared SQLite/optional SQLCipher connector
├── privacy.py                 ← Identifier hashing and payload encryption
├── ingestion.py               ← REST event ingestion and schema upgrades
├── role_baseline.py           ← Role-aware behavioral baseline calculations
├── model.py                   ← Isolation Forest + One-Class SVM ensemble
├── requirements.txt
├── database.db                ← SQLite DB (auto-created on first run)
├── templates/                 ← Web dashboard UI (Flask-rendered)
│   ├── dashboard.html
│   ├── login.html
│   ├── summary.html
│   └── forbidden.html
└── desktop_app/                ← PyQt6 Presentation Tier (Chapter 3 spec)
    ├── main.py                ← Entry point
    ├── server_launcher.py     ← Boots the Flask backend as a background thread
    ├── api_client.py          ← HTTP client (desktop app <-> Flask backend)
    ├── theme.py                ← QSS stylesheet (matches web dashboard branding)
    ├── login_window.py
    ├── main_window.py         ← Tab assembly, role-based UI, auto-refresh
    ├── dashboard_tab.py        ← Risk Score Dashboard
    ├── profiles_tab.py         ← User Behavior Profiles (baseline vs. deviation)
    ├── role_sessions_tab.py    ← Role-grouped user risk and baseline view
    ├── alerts_tab.py           ← Plain-Language Alerts
    ├── uav_config_tab.py       ← UAV Configuration Module
    └── summary_tab.py          ← Management-role aggregate view
```

---

## Architecture Note

Chapter 3 specifies a **PyQt6 desktop application** as the presentation tier, installed on
the IT administrator's machine. The desktop app in `desktop_app/` implements this: it is a
pure UI layer that talks to the Flask backend (`app.py`) over local HTTP (`127.0.0.1:5050`),
matching the tiered architecture (Presentation ↔ Authentication ↔ Logic ↔ Data) described in
the design chapter. The Flask web dashboard (`templates/`) is retained separately for quick
browser-based testing and is not the primary deliverable.

**Still simplified vs. the full Chapter 3 design** (tracked as remaining work):
- Session-based auth is used instead of JWT (reasonable for a server-rendered/local desktop
  client; JWT is more suited to the Edge Inference Engine's API-facing needs)
- SQLCipher is optional because compatible builds vary by platform; SQLite remains the development fallback
- REST ingestion is available, but provider-specific credentials and mappings are still required
- No Edge Inference Engine (Raspberry Pi 5 / UAV) integration yet

---

## Simulated Users

| User    | Behavior         | Expected Risk |
|---------|------------------|---------------|
| alice   | Normal           | LOW           |
| bob     | Normal           | LOW           |
| charlie | Normal           | LOW           |
| diana   | Normal           | LOW           |
| eve     | Insider (anomalous) | HIGH       |

Eve exhibits: off-hours logins, excessive file access, large data transfers, repeated failed logins.

---

## Tech Stack

- **Python + Flask** — backend & API (Logic + Authentication + Data tiers)
- **PyQt6** — desktop presentation tier
- **scikit-learn** — Isolation Forest + One-Class SVM ensemble
- **pandas** — data aggregation
- **bcrypt** — password hashing
- **SQLite** — log & user storage
- **cryptography** — protected event payloads
- **Vanilla HTML/CSS/JS** — web dashboard alternative (no extra installs)
