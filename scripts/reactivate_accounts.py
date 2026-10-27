"""List or reactivate existing accounts in an explicitly selected database."""

import argparse
from datetime import datetime
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database


def _resolve_database_path(argument):
    value = argument or os.environ.get("INSIGHTER_DB_PATH")
    if not value:
        raise ValueError("Provide --db <path> or set INSIGHTER_DB_PATH.")
    path = Path(value).expanduser().resolve()
    print(f"Database: {path}")
    return path


def _require_existing_database(path):
    if not path.is_file():
        raise ValueError(f"Database file does not exist: {path}")


def _open_database(path):
    previous_path = database.DB_PATH
    database.DB_PATH = str(path)
    return database.connect(), previous_path


def _list_accounts(path):
    conn, previous_path = _open_database(path)
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).fetchone()
        if not table:
            raise ValueError("The selected database has no users table.")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "is_active" in columns and "is_demo" in columns:
            query = (
                "SELECT username, role, is_active, is_demo "
                "FROM users ORDER BY username"
            )
        elif "is_active" in columns:
            query = (
                "SELECT username, role, is_active, 0 AS is_demo "
                "FROM users ORDER BY username"
            )
        elif "is_demo" in columns:
            query = (
                "SELECT username, role, 1 AS is_active, is_demo "
                "FROM users ORDER BY username"
            )
        else:
            query = (
                "SELECT username, role, 1 AS is_active, 0 AS is_demo "
                "FROM users ORDER BY username"
            )
        rows = conn.execute(query).fetchall()
    finally:
        conn.close()
        database.DB_PATH = previous_path
    print("username\trole\tis_active\tis_demo")
    for row in rows:
        print(f"{row[0]}\t{row[1]}\t{row[2]}\t{row[3]}")


def _backup_database(path):
    backup_path = path.with_name(
        f"{path.stem}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}{path.suffix}"
    )
    if backup_path.exists():
        raise FileExistsError(f"Backup path already exists: {backup_path}")

    source, previous_path = _open_database(path)
    database.DB_PATH = str(backup_path)
    try:
        destination = database.connect()
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
        database.DB_PATH = previous_path
    print(f"Backup: {backup_path}")
    return backup_path


def _reactivate_accounts(path, usernames):
    conn, previous_path = _open_database(path)
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).fetchone()
        if not table:
            raise ValueError("The selected database has no users table.")
        existing = {
            row[0]
            for row in conn.execute("SELECT username FROM users")
        }
        unknown = sorted(set(usernames) - existing)
        if unknown:
            raise ValueError(f"Unknown username(s): {', '.join(unknown)}")
    finally:
        conn.close()
        database.DB_PATH = previous_path

    _backup_database(path)
    conn, previous_path = _open_database(path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "is_active" not in columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
            )
        if "is_demo" not in columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                action TEXT NOT NULL,
                detail TEXT DEFAULT '',
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("BEGIN IMMEDIATE")
        for username in dict.fromkeys(usernames):
            cursor = conn.execute(
                "UPDATE users SET is_active = 1 "
                "WHERE username = ? AND is_active != 1",
                (username,),
            )
            if cursor.rowcount:
                conn.execute(
                    "INSERT INTO audit_log (username, action, detail) "
                    "VALUES (?, ?, ?)",
                    ("recovery-script", "ACCOUNT_REACTIVATED", f"username={username}"),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
        database.DB_PATH = previous_path


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="list account status without password hashes")
    reactivation = commands.add_parser(
        "reactivate", help="reactivate named existing accounts"
    )
    for command in (listing, reactivation):
        command.add_argument("--db", help="database path (defaults to INSIGHTER_DB_PATH)")
        command.add_argument(
            "--i-know-this-is-my-real-db",
            action="store_true",
            help="allow targeting the repository's database.db",
        )
    reactivation.add_argument("usernames", nargs="+")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        path = _resolve_database_path(args.db)
        if path == (PROJECT_ROOT / "database.db").resolve() and not (
            args.i_know_this_is_my_real_db
        ):
            raise ValueError(
                "Refusing to use the repository database.db without "
                "--i-know-this-is-my-real-db."
            )
        _require_existing_database(path)
        if args.command == "list":
            _list_accounts(path)
        else:
            _reactivate_accounts(path, args.usernames)
    except (OSError, ValueError, database.sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
