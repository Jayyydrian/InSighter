import random

from database import connect
from privacy import sanitize_event
from sector_config import get_active_sector, get_sector_config

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
    roles = list(sector_config["roles"])
    categories = list(sector_config["data_categories"])
    users = {
        "alice":   {"normal": True},
        "bob":     {"normal": True},
        "charlie": {"normal": True},
        "diana":   {"normal": True},
        "eve":     {"normal": False},  # insider threat
    }
    for index, user in enumerate(users):
        users[user]["role"] = roles[index % len(roles)]

    random.seed(42)
    for _ in range(300):
        for user, props in users.items():
            if props["normal"]:
                row = (
                    user,
                    random.randint(8, 18),
                    random.randint(1, 15),
                    round(random.uniform(1, 40), 2),
                    random.randint(0, 1),
                    0
                )
            else:
                row = (
                    user,
                    random.choice([random.randint(0, 5), random.randint(20, 23)]),
                    random.randint(60, 120),
                    round(random.uniform(400, 1000), 2),
                    random.randint(4, 10),
                    1
                )
            event = dict(zip(
                ("user", "login_hour", "files_accessed", "data_transferred_mb", "failed_logins", "off_hours_access"),
                row,
            ))
            event["role"] = props["role"]
            event["data_category"] = categories[0 if props["normal"] else -1]
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
    print("✅  database.db seeded with 1500 log entries.")

if __name__ == "__main__":
    generate()
