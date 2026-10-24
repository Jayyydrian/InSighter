"""Explicitly provision or remove the local-only demo accounts."""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auth import hash_password, init_users_table
from database import connect

DEMO_ACCOUNTS = (
    ("admin", "admin123", "admin"),
    ("manager", "manager123", "management"),
    ("hr_officer", "hr_officer123", "compliance"),
)


def provision_demo_accounts():
    """Create all demo users, refusing to overwrite any existing account."""
    init_users_table()
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT username FROM users WHERE username IN (?, ?, ?)",
                tuple(username for username, _, _ in DEMO_ACCOUNTS),
            )
        }
        if existing:
            names = ", ".join(sorted(existing))
            raise RuntimeError(f"Refusing to overwrite existing account(s): {names}")

        for username, password, role in DEMO_ACCOUNTS:
            conn.execute(
                """INSERT INTO users (username, password_hash, role, is_active, is_demo)
                   VALUES (?, ?, ?, 1, 1)""",
                (username, hash_password(password), role),
            )
            conn.execute(
                "INSERT INTO audit_log (username, action, detail) VALUES (?, ?, ?)",
                ("system", "DEMO_ACCOUNT_CREATED", f"username={username};role={role}"),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return DEMO_ACCOUNTS


def remove_demo_accounts():
    """Delete only demo users created by this provisioning command."""
    init_users_table()
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        demo_users = conn.execute(
            "SELECT id, username FROM users WHERE is_demo = 1"
        ).fetchall()
        demo_ids = {row[0] for row in demo_users}
        demo_names = {row[1] for row in demo_users}

        for session_row in conn.execute("SELECT sid_hash, data FROM auth_sessions").fetchall():
            try:
                user_id = json.loads(session_row[1]).get("user_id")
            except (TypeError, ValueError):
                continue
            if user_id in demo_ids:
                conn.execute(
                    "DELETE FROM auth_sessions WHERE sid_hash = ?", (session_row[0],)
                )

        for user_id, username in demo_users:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.execute(
                "INSERT INTO audit_log (username, action, detail) VALUES (?, ?, ?)",
                ("system", "DEMO_ACCOUNT_REMOVED", f"username={username}"),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return tuple(sorted(demo_names))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "remove"))
    action = parser.parse_args().action

    if action == "create":
        accounts = provision_demo_accounts()
        print("Created the local demo accounts:")
        for username, password, role in accounts:
            print(f"  {username} / {password} ({role})")
        print("These published demo passwords are not safe for exposed or production systems.")
    else:
        names = remove_demo_accounts()
        if names:
            print(f"Removed demo accounts: {', '.join(names)}")
        else:
            print("No demo accounts were present.")


if __name__ == "__main__":
    main()
