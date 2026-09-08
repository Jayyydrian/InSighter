# InSighter — Mini POC

AI-assisted insider threat detection proof-of-concept.
Runs locally in under 2 minutes.

---

## What it does

- Generates 1,500 fake user activity logs (5 users, one insider)
- Runs Isolation Forest anomaly detection on each user's behavior
- Displays a live risk dashboard with scores, alerts, and charts
- "Eve" is the simulated insider — she will score HIGH risk

---

# InSighter — Mini POC

AI-assisted insider threat detection proof-of-concept.

Two ways to run it:
1. **Desktop app (PyQt6)** — matches Chapter 3's Presentation Tier spec. Recommended.
2. **Web dashboard (Flask + browser)** — kept for quick testing/demo convenience.

Both talk to the same backend (Logic + Authentication + Data tiers).

---

## What it does

- Generates 1,500 fake user activity logs (5 users, one insider)
- Runs Isolation Forest + One-Class SVM (weighted 0.6/0.4 ensemble) on each user's behavior
- Builds role-aware behavioral baselines and adds baseline deviation to risk scores
- Accepts protected events from a configured REST API through the desktop Integrations page
- Hashes identifiers and encrypts non-identity event payloads before storage
- Uses SQLCipher encryption automatically when `sqlcipher3` is installed, with SQLite fallback for development
- Login + RBAC: `admin` (full access), `compliance` (pseudonymized records), and `management` (summary-only), per Chapter 3
- Displays a live risk dashboard with scores, alerts, user behavior profiles, and a UAV Configuration Module
- "Eve" is the simulated insider — she will score HIGH risk

---

## Quick Start — Desktop App (recommended)

```bash
pip install -r requirements.txt
python desktop_app/main.py
```

This automatically starts the Flask backend in the background (port 5051) and opens the
PyQt6 login window. Log in with:

| Username | Password    | Role         | Access                          |
|----------|-------------|--------------|----------------------------------|
| admin    | admin123    | admin        | Full: dashboard, profiles, alerts, UAV config |
| manager  | manager123  | management   | Summary counts only              |
| hr_officer | hr_officer123 | compliance | Flagged records and audit trail with pseudonymous identities |

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

### CERT r4.2 feature-scale finding

The r4.2 adapter computes removable-device activity from `device.csv` and
retains it in the standalone cleaned feature table. It is deliberately not
part of the current shared ensemble: adding the raw Connect-event count to the
mixed synthetic/CERT feature matrix changed the relative scale of the
unsupervised models and broke the existing synthetic regression, moving Eve
from HIGH to MEDIUM while normal synthetic users also moved upward.

This is a constraint of the current shared-baseline approach, not evidence that
the raw signal is useless. A future experiment should normalize the signal and
fit baselines per source or per role before adding it to the ensemble. The
role-aware baseline module is an existing starting point for that work.

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

