import random

from database import connect
from privacy import sanitize_event
from sector_config import get_active_sector, get_sector_config, get_sector_roster
from simulation import activity_values

EVENTS_PER_USER = 120

def generate():
    conn = connect()
    conn.execute("DROP TABLE IF EXISTS logs")
    conn.execute("""
        CREATE TABLE logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT,
            login_hour INTEGER,
            files_accessed INTEGER,
            data_transferred_mb REAL,
            failed_logins INTEGER,
            off_hours_access INTEGER,
            data_category TEXT DEFAULT '',
            role TEXT DEFAULT 'unknown',
            source TEXT DEFAULT 'synthetic',
            payload_encrypted TEXT DEFAULT '',
            ingested_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    sector = get_active_sector(conn)
    sector_config = get_sector_config(sector)
    categories = list(sector_config["data_categories"])
    users = get_sector_roster(sector)

    rng = random.Random(42)
    for _ in range(EVENTS_PER_USER):
        for user, role in users:
            anomalous = rng.random() < 0.045
            row = (user, *activity_values(sector, anomalous, rng))
            event = dict(zip(
                ("user", "login_hour", "files_accessed", "data_transferred_mb", "failed_logins", "off_hours_access"),
                row,
            ))
            event["role"] = role
            event["data_category"] = categories[-1 if anomalous else 0]
            protected = sanitize_event(event, source="synthetic")
            conn.execute(
                """INSERT INTO logs
                (user, login_hour, files_accessed, data_transferred_mb,
                 failed_logins, off_hours_access, role, source, payload_encrypted,
                 data_category)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                tuple(protected[key] for key in (
                    "user", "login_hour", "files_accessed", "data_transferred_mb",
                    "failed_logins", "off_hours_access", "role", "source", "payload_encrypted",
                )) + (event["data_category"],),
            )

    conn.commit()
    conn.close()
    print(f"Seeded database with {EVENTS_PER_USER * len(users)} log entries for {sector}.")

if __name__ == "__main__":
    generate()
