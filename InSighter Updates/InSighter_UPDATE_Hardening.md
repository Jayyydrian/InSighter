# InSighter Update Report: Hardening
**Generation Date:** 2026-10-22 11:23:56 UTC  
**Primary Focus:** Authentication, server-side session lifecycle, CSRF, and RBAC hardening.  
**Overall System Posture:** Under Remediation / Staging; major application-level controls are implemented, but production deployment still requires operational prerequisites and follow-up work.

---

## 1. Executive Summary & Impact Analysis

InSighter's authentication path has been reworked to remove shipped default accounts, store salted PBKDF2-HMAC-SHA256 password verifiers, persist opaque revocable sessions server-side, and revalidate user state for protected requests. Request forgery and redirect risks were also addressed through CSRF validation on mutating methods, POST-only state-changing routes, and local-only post-login redirects.

- **Core Objective:** Replace demo-oriented login/session behavior with explicit credential provisioning and server-enforced authentication, session, and role controls.
- **Risk Delta:** The previously identified critical exposure from known default credentials is removed from application seeders and login UI; major session replay/fixation, timing, throttle, stale-role, CSRF, and redirect weaknesses now have defenses. Residual risk remains around operational account provisioning, shared-store scaling, and deployment/network configuration.
- **System Stability:** Auth database schema now tracks active users, login throttle state, and server-side sessions. Existing users are disabled on active-status migration and need controlled reprovisioning. Logout, reseed, and simulate now require POST; desktop clients and tests were updated to use CSRF tokens and the new methods.
- **Verification:** Full repository CI runner passed **186 / 186 tests** (minimum configured floor: 181). Focused authentication and RBAC suites passed **12 / 12 tests** after final auth cleanup.

## 2. Granular Change Matrix

| File / Module | Change Category | Specific Functions / Block Updated | Description of Logic Modification | Security / Operational Benefit |
| :--- | :--- | :--- | :--- | :--- |
| [app.py](../app.py) | Authentication / Session | App configuration, `login()`, `logout()`, `_safe_local_redirect()` | Requires `INSIGHTER_SECRET_KEY`; installs database session interface; rotates session on successful login; validates local redirects; makes logout POST-only. | Prevents default signing-key behavior, fixation/replay, and open redirects. |
| [app.py](../app.py) | CSRF / Route Safety | `enforce_csrf()`, `csrf_token()`, `/api/reseed`, `/api/simulate` | Verifies session-bound tokens on POST/PUT/PATCH/DELETE and changes state-changing admin routes to POST. | Mitigates CSRF and unsafe-method triggering. |
| [auth.py](../auth.py) | Cryptography / Login | `hash_password()`, `verify_password()`, `authenticate_login()` | PBKDF2-HMAC-SHA256 at 600,000 iterations, 16-byte unique salt, constant-time comparison; dummy verification; normalized identity and IP+username throttle. | Hardens password storage and reduces timing leakage and targeted account-lockout risk. |
| [auth.py](../auth.py) | Session Store / RBAC | `DatabaseSessionInterface`, `rotate_session()`, `current_user()`, auth decorators | Persists session data keyed by a hash of a random opaque session ID; enforces 30-minute idle/eight-hour absolute expiry; rechecks active user and role for protected requests. | Enables server-side revocation and applies role changes on subsequent requests. |
| [database.py](../database.py) | Secret Configuration | `_database_key()` | Removes hardcoded SQLCipher key fallback and requires `INSIGHTER_DB_KEY` when SQLCipher is enabled. | Prevents predictable database encryption keys. |
| [privacy.py](../privacy.py) | Secret Configuration | `_secret()` | Removes hardcoded PII secret fallback; requires `INSIGHTER_PII_SECRET` or the app signing key. | Avoids predictable PII hashing/encryption key material. |
| [desktop_app/api_client.py](../desktop_app/api_client.py) | Client Compatibility | `_refresh_csrf_token()`, `login()`, `logout()`, `reseed()`, `simulate()` | Fetches CSRF tokens, attaches `X-CSRF-Token` to mutations, and uses POST for logout/reseed/simulate. | Keeps desktop workflows compatible with server-side CSRF and method enforcement. |
| [desktop_app/login_window.py](../desktop_app/login_window.py), [templates/login.html](../templates/login.html) | UI Hardening | Login form and credentials hints | Removes demo credentials; browser login form includes hidden CSRF token. | Removes credential disclosure and supports CSRF validation. |
| [desktop_app/server_launcher.py](../desktop_app/server_launcher.py) | Local Deployment | `_start_backend_process()` | Sets `INSIGHTER_COOKIE_SECURE=0` for the desktop app's loopback HTTP backend only. | Preserves local desktop operation; must not be exposed on a network. |
| [README.md](../README.md) | Operations / Deployment | Quick Start and production checklist | Documents environment-based administrator bootstrap, secret requirements, HTTPS cookie configuration, SQLite scaling boundary, and user provisioning gap. | Gives operators deployment and cleanup requirements. |
| [test_auth_security.py](../test_auth_security.py), [test_compliance_rbac.py](../test_compliance_rbac.py), [test_desktop_async.py](../test_desktop_async.py) | Verification | Auth, RBAC, transport regressions | Adds coverage for unique salts, throttling, rotation/revocation, CSRF, redirects, live role checks, and client method changes. | Makes key security guarantees regression-testable. |
| [test_role_baselines.py](../test_role_baselines.py), [test_sector_deployment.py](../test_sector_deployment.py), [test_sector_removal.py](../test_sector_removal.py), [test_llm_performance.py](../test_llm_performance.py), [llm_benchmark_review/tests/test_llm_advisor.py](../llm_benchmark_review/tests/test_llm_advisor.py), [test_support.py](../test_support.py) | Test Integration | Test identity provisioning and write helpers | Replaces forged session role fields with persisted test identities and CSRF-enabled test requests. | Ensures tests exercise the actual authorization boundary. |

## 3. Deep-Dive Process Architecture & Flow Diagrams

### 3.1 Data & Control Flow

1. **Entry Point / Interception:** Mutations are restricted to appropriate HTTP methods. Before request handling, POST/PUT/PATCH/DELETE requests must present a session-bound token via `X-CSRF-Token` or the browser form's `_csrf_token`.
2. **Authentication & Validation:** Login normalizes the username using trim and case-folding. A persisted throttle key combines the client IP and normalized username. The third failed attempt starts a 60-second delay; backoff increases progressively to a one-hour cap, and counters decay after 15 minutes.
3. **Credential Validation:** The application looks up the account and uses PBKDF2-HMAC-SHA256 with a 16-byte random salt and 600,000 iterations. Unknown, inactive, and locked accounts receive a dummy verifier check. Digest comparison uses `hmac.compare_digest`.
4. **Session & Authorization:** On success, the pre-auth session record is deleted and a fresh random session ID is issued. Only its SHA-256 digest is persisted. Protected routes load the current active user and role from the database instead of trusting cached role claims.
5. **Business Logic & State Mutation:** Authenticated writes proceed only after CSRF validation and the route's role decorator. `/api/reseed`, `/api/simulate`, and `/logout` are POST-only.
6. **Egress & Response:** The browser receives an opaque cookie with `HttpOnly`, `SameSite=Lax`, and `Secure` enabled by default. Logout clears the session and deletes the server-side record. Login `next` redirects are constrained to local relative paths.

### 3.2 Sequence Logic (Text Breakdown)

`[Client] -> [CSRF / Method Gate] -> [IP+Username Throttle] -> [User Lookup] -> [PBKDF2 or Dummy Verification] -> [Session ID Rotation] -> [Persist Hashed Session ID] -> [Secure Cookie + Redirect]`

```mermaid
sequenceDiagram
    participant C as Client
    participant F as Flask Request Guards
    participant A as Auth Service
    participant D as SQLite Store
    C->>F: POST /login + CSRF token
    F->>A: Validate login attempt
    A->>D: Read user and composite throttle state
    A->>A: PBKDF2-HMAC-SHA256 check or dummy check
    A->>D: Clear throttle; delete old session; persist new session
    F-->>C: Redirect + opaque HttpOnly/SameSite cookie
    C->>F: Protected request + session cookie
    F->>D: Load session and active user/role
    F-->>C: Authorized response or 401/403
```

## 4. Security & Compliance Controls Checklist

| Security Control Domain | Requirement / Guideline | Implementation Status | Verified In Code |
| :--- | :--- | :--- | :--- |
| **Password Storage** | PBKDF2-HMAC-SHA256, 600,000 iterations, random 16-byte salt, constant-time comparison | PASS | [auth.py](../auth.py) |
| **Throttling / DoS** | Composite IP + normalized username key, three-failure threshold, progressive backoff and decay | PASS | [auth.py](../auth.py) |
| **Timing Defense** | Dummy verifier for unknown/inactive/locked users and generic failure message | PASS | [auth.py](../auth.py), [app.py](../app.py) |
| **Session Isolation** | Opaque server-side session IDs, login rotation, logout revocation, idle and absolute expiry | PASS | [auth.py](../auth.py), [app.py](../app.py) |
| **Transport Security** | `HttpOnly`, `SameSite=Lax`, Secure by default (`INSIGHTER_COOKIE_SECURE=1`) | PASS with local-dev exception | [auth.py](../auth.py), [app.py](../app.py) |
| **CSRF & HTTP Safety** | CSRF validation on mutating methods; mutation routes use POST | PASS | [app.py](../app.py), [desktop_app/api_client.py](../desktop_app/api_client.py) |
| **Authorization (RBAC)** | Server-side active-user and role revalidation | PASS | [auth.py](../auth.py) (`login_required`, `admin_required`, `compliance_required`) |
| **Redirect Safety** | Local relative path enforcement | PASS | [app.py](../app.py) |
| **Secrets / Bootstrap** | No default accounts; required runtime secret and one-time admin bootstrap pair | PASS, operator setup required | [app.py](../app.py), [auth.py](../auth.py), [database.py](../database.py), [privacy.py](../privacy.py) |
| **Multi-Instance Session Store** | Shared session and throttle persistence | NOT IMPLEMENTED | SQLite-backed in [auth.py](../auth.py); single-node boundary documented in [README.md](../README.md) |

## 5. Deployment, Environment & Secrets Reference

### 5.1 Environment Variables

| Variable Name | Required / Optional | Default Value | Usage & Production Guidance |
| :--- | :--- | :--- | :--- |
| `INSIGHTER_SECRET_KEY` | **Required** | None | Stable, high-entropy application secret. `app.py` fails at startup if absent. |
| `INSIGHTER_PII_SECRET` | Recommended; fallback accepted | None | Independent high-entropy key for PII hashing/encryption. `privacy.py` falls back to `INSIGHTER_SECRET_KEY`. |
| `INSIGHTER_ADMIN_USERNAME` | Bootstrap only; set with password | None | Normalized username for initial admin creation. Remove after bootstrap. |
| `INSIGHTER_ADMIN_PASSWORD` | Bootstrap only; set with username | None | Initial admin password; minimum 14 characters. Remove after bootstrap. |
| `INSIGHTER_COOKIE_SECURE` | Optional; production must be secure | `1` | Enables Secure cookies. The desktop launcher sets `0` for its loopback HTTP process only. |
| `INSIGHTER_DB_KEY` | Conditional | None | Required if SQLCipher is enabled. Keep separate from database files and backups. |
| `INSIGHTER_DB_PATH` | Optional | SQLite path selected by `database.py` | Selects the database file; durable storage and restrictive permissions are required. |

### 5.2 Infrastructure & Architecture Constraints

- **Worker Model:** Sessions and throttle counters use the configured SQLite database. Operate as a single backend process/node; before horizontal scaling or multiple independent workers, migrate both to a shared transactional store such as Redis or a production database.
- **Bootstrap Cleanup:** Provision the first admin through a secret manager or protected environment. Verify initial login, then remove both bootstrap variables. They do not reset an existing active admin.
- **TLS and Proxy:** Terminate TLS at a trusted endpoint and retain `INSIGHTER_COOKIE_SECURE=1`. Restrict direct backend access and ensure the client IP used by throttling cannot be spoofed through untrusted proxy headers.
- **User Provisioning:** Self-registration and in-app account management are not implemented. Additional management/compliance users require a controlled operator workflow that hashes passwords with the application function, validates allowed roles, and emits an audit record.
- **Adjacent Integration Follow-up:** `cert_adapter.py` currently reads `INSIGHTER_ADMIN_USER`, while login bootstrap uses `INSIGHTER_ADMIN_USERNAME`. Align or document the CERT ingestion credential name before relying on that integration in deployment.

## 6. Verification, Testing & QA Metrics

- **CI Pipeline Status:** PASSED
- **Total Executed Tests:** **186 / 186 passed**; CI minimum floor was 181.
- **Targeted Security Test Coverage:**
  - `test_auth_security.py`: Password salt/iteration checks, three-failure throttle, session rotation and revocation, CSRF rejection, GET mutation rejection, safe redirect, and live role revalidation.
  - `test_compliance_rbac.py`: Admin, compliance, and management access separation.
  - `test_desktop_async.py`: Desktop CSRF bootstrap, request handling, logout and mutation transport compatibility.
- **Known Edge Cases / Non-Issues:**
  - The old username-only throttle schema/helper was removed; current throttling uses the composite key.
  - Local HTTP and `INSIGHTER_COOKIE_SECURE=0` are limited to the desktop loopback integration and are not production settings.
  - CI test credentials and secrets are test-only fixtures; they are not production bootstrap defaults.

## 7. Next Steps & Technical Debt Backlog

1. **Immediate Tasks:** Deploy to a staging environment with `INSIGHTER_SECRET_KEY`, `INSIGHTER_PII_SECRET`, and the initial administrator pair injected securely; verify HTTPS cookie behavior, login, logout revocation, and recovery procedures.
2. **Short-Term Technical Debt:** Add an audited admin-only user-management workflow for creating, disabling, and resetting management/compliance accounts; align `cert_adapter.py`'s admin variable name with the documented deployment contract.
3. **Long-Term Scaling:** Move server-side sessions and throttling from SQLite to a shared transactional service before multi-worker or multi-instance deployment; add cross-instance revocation and operational monitoring for throttle events.
