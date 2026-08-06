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
- Login + RBAC: `admin` (full access) vs `management` (summary-only), per Chapter 3
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

---

## Quick Start — Web Dashboard (alternative)

```bash
pip install -r requirements.txt
python app.py
```
Open `http://localhost:5051` and log in with the same accounts above.

The database is auto-seeded on first run.
Use the **↺ Reseed Data** button to regenerate fresh logs anytime.

---

## Project Structure

```
Insighter/
├── app.py                     ← Flask backend (Logic + Auth + Data tiers)
├── auth.py                    ← BCrypt hashing, RBAC decorators, users/uav_config tables
├── generate_logs.py           ← Synthetic log data generator
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
- SQLite is not yet SQLCipher-encrypted
- Log data is synthetic, not yet ingested from real AD / Google Workspace / M365 APIs
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
- **Vanilla HTML/CSS/JS** — web dashboard alternative (no extra installs)

