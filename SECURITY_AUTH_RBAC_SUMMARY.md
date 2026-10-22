# Authentication and RBAC Security Overhaul

## 1. Executive Summary

InSighter's login and authorization path has been substantially hardened: published default accounts were removed, password storage now uses PBKDF2-HMAC-SHA256, opaque sessions are stored server-side, and protected requests revalidate the active user's role. The changes address the major application-level risks found in the review; production readiness still depends on secret provisioning, HTTPS, a controlled user-management process, and keeping the SQLite deployment single-node. The complete CI suite passed **186 tests**; focused authentication and RBAC tests passed **12 tests** after the final auth cleanup.

## 2. Comprehensive Security Resolution Matrix

| Vulnerability / Issue | Risk Level | Implemented Remediation | Files Modified |
| :--- | :--- | :--- | :--- |
| **Seeded/Hardcoded Credentials** | Critical | Removed default account seeding and GUI/documentation credential hints. First admin creation is environment-provisioned; existing accounts are disabled when migrating to active-user tracking. | [auth.py](auth.py), [README.md](README.md), [templates/login.html](templates/login.html), [desktop_app/login_window.py](desktop_app/login_window.py) |
| **Password Hashing Weakness** | High | PBKDF2-HMAC-SHA256, 600,000 iterations, a fresh 16-byte random salt per password, and `hmac.compare_digest` verification. | [auth.py](auth.py), [test_auth_security.py](test_auth_security.py) |
| **Lockout & Account DoS** | High | Throttle key combines source IP and normalized username; three failures trigger a 60-second initial delay with progressive backoff capped at one hour. State decays after 15 minutes and clears on successful login. | [auth.py](auth.py), [test_auth_security.py](test_auth_security.py) |
| **Timing Side-Channel Attacks** | High | Unknown, inactive, and locked identities are checked against a PBKDF2 dummy hash; invalid and cooling-down login attempts return the same generic response. | [auth.py](auth.py), [app.py](app.py), [test_auth_security.py](test_auth_security.py) |
| **Replayable / Unexpired Sessions** | High | Opaque random session IDs are stored by SHA-256 digest in SQLite; login rotates the ID, logout deletes its record, and server-side idle/absolute expiry is 30 minutes/eight hours. Cookies are HttpOnly, Secure by default, and SameSite=Lax. | [auth.py](auth.py), [app.py](app.py), [test_auth_security.py](test_auth_security.py) |
| **Stale RBAC Session Claims** | Medium | Protected decorators reload the user and current role from the database and reject inactive or missing users on each request. | [auth.py](auth.py), [test_auth_security.py](test_auth_security.py), [test_compliance_rbac.py](test_compliance_rbac.py) |
| **Unsafe `GET` Mutations & CSRF** | Medium | Logout, reseed, and simulate require POST. Mutating methods require a session-bound CSRF token; the browser form and desktop client now acquire and submit it. | [app.py](app.py), [templates/login.html](templates/login.html), [desktop_app/api_client.py](desktop_app/api_client.py), [desktop_app/server_launcher.py](desktop_app/server_launcher.py) |
| **Open Redirects** | High | Post-login `next` values are limited to local relative paths; scheme/host URLs, protocol-relative paths, and backslash forms fall back to the dashboard. | [app.py](app.py), [test_auth_security.py](test_auth_security.py) |
| **Fallback Cryptographic Secrets** | High | Flask signing and PII secrets must be provided by deployment configuration; SQLCipher requires a configured database key. No source-coded development fallback remains. | [app.py](app.py), [database.py](database.py), [privacy.py](privacy.py), [README.md](README.md) |

## 3. Key Architectural Changes

### Authentication and Throttling

- `auth.py` stores password verifiers in the format `pbkdf2_sha256$iterations$salt$digest`, using 600,000 PBKDF2-HMAC-SHA256 iterations and a per-password 16-byte random salt.
- Digest comparison uses `hmac.compare_digest`; unknown, inactive, or locked accounts use a dummy password verifier to reduce account-existence timing differences.
- Login throttling is persisted under a hash of the source IP and case-folded username. The threshold is three failures, with an initial 60-second delay, increasing backoff, a 15-minute decay window, and reset after successful authentication.
- The bootstrap administrator password must be at least 14 characters. Existing accounts are disabled as part of the active-user schema migration and must be provisioned again through an approved process.

### Session Lifecycle

- Flask's signed client-side session implementation was replaced by a custom SQLite-backed session interface. The browser holds only a random opaque session ID; the database stores its SHA-256 digest and session data.
- Login deletes the pre-authentication session record and assigns a new random ID. Logout clears the session and deletes the server-side record, invalidating copied IDs.
- Idle expiry is 30 minutes and absolute expiry is eight hours. Cookie attributes are `HttpOnly`, `SameSite=Lax`, and `Secure` by default.
- `INSIGHTER_COOKIE_SECURE=0` is set only by the desktop launcher for its loopback HTTP backend. Production deployments must retain Secure cookies and use HTTPS.

### Access Control, Routes, and Client Integration

- RBAC decorators fetch the current active user and role from the database for each protected request rather than authorizing from a stale session role.
- A session-bound token is issued by `/api/csrf-token` and checked for POST, PUT, PATCH, and DELETE requests. Login's HTML form submits the same token as a hidden field; the desktop client sends it in `X-CSRF-Token`.
- `/logout`, `/api/reseed`, and `/api/simulate` are POST-only. The desktop client was updated to use these methods and to handle CSRF bootstrap.
- Login redirects are validated as local relative paths. Existing admin and compliance route separation remains server-enforced.

### Configuration and Environment

- `app.py` fails at startup without `INSIGHTER_SECRET_KEY`; `privacy.py` requires `INSIGHTER_PII_SECRET` or the signing secret; SQLCipher deployments require `INSIGHTER_DB_KEY`.
- `README.md` documents administrator bootstrap and deployment settings. `templates/login.html` and the Qt login window no longer show sample credentials.
- Tests now provision isolated test users and exercise real persisted sessions and CSRF instead of forging role fields in a client-side session.

## 4. Deployment and Operational Guidance

### Required Environment Variables

- `INSIGHTER_SECRET_KEY`: stable, high-entropy Flask signing/application secret. The application refuses to start without it.
- `INSIGHTER_PII_SECRET`: independent high-entropy key for identifier hashing and payload encryption. Alternatively, the code accepts `INSIGHTER_SECRET_KEY` as a compatibility fallback, but a separate key is recommended.
- `INSIGHTER_ADMIN_USERNAME` and `INSIGHTER_ADMIN_PASSWORD`: set together for initial administrator provisioning. Password minimum is **14 characters**. These variables are only used if no active administrator exists.
- `INSIGHTER_COOKIE_SECURE`: defaults to `1`; retain `1` for HTTPS deployments. The desktop launcher overrides it to `0` for loopback-only local HTTP.
- `INSIGHTER_DB_KEY`: required when SQLCipher is installed/enabled; protect it independently from the database file.

### Bootstrap Cleanup

1. Place the bootstrap username and password in a secret manager or protected deployment environment for first startup.
2. Confirm the administrator can sign in over the intended secure channel.
3. Remove `INSIGHTER_ADMIN_USERNAME` and `INSIGHTER_ADMIN_PASSWORD` from the runtime environment and deployment manifests. The bootstrap is not a password-reset mechanism for an existing active administrator.
4. Store and rotate signing, PII, and database keys through a documented secret-management process; never commit or log their values.

### Infrastructure and Scaling Limits

- The session and throttle stores currently share the application SQLite database. This is intended for a **single backend process/node** with durable storage and reliable SQLite locking; it is not a horizontally scalable shared session service.
- Before running multiple workers or instances, migrate sessions and throttling to a shared transactional store such as Redis or a production database, including atomic throttle updates and session revocation.
- For production, terminate TLS at a trusted proxy, keep `INSIGHTER_COOKIE_SECURE=1`, restrict direct backend access, and configure proxy trust carefully so client IP values used in throttling cannot be spoofed.
- Where SQLCipher is used, set `INSIGHTER_DB_KEY` and protect database files and backups with restrictive permissions and encrypted storage.

### User Provisioning Workflow

- There is no self-registration or in-app user-management workflow. Bootstrap creates only the initial administrator; it does not automatically create management or compliance users.
- Until an audited admin-only user-management feature exists, provision additional accounts through a controlled operator process that uses the application's password-hashing function, validates roles, records an audit event, and never stores or transmits plaintext passwords in logs or source control.

### Verification

- Full repository CI test runner: **186 tests passed**, with the configured minimum floor of 181.
- Focused authentication and RBAC tests after final auth cleanup: **12 tests passed**.
- Coverage includes unique password salts, PBKDF2 parameters, three-failure throttling, generic login failures, fresh session IDs, logout revocation, CSRF rejection, GET mutation rejection, safe redirects, live role revalidation, and admin/compliance/management separation.