import sqlite3
import random

def generate():
    conn = sqlite3.connect("database.db")
    conn.execute("DROP TABLE IF EXISTS logs")
    conn.execute("""
        CREATE TABLE logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT,
            login_hour INTEGER,
            files_accessed INTEGER,
            data_transferred_mb REAL,
            failed_logins INTEGER,
            off_hours_access INTEGER
        )
    """)

    users = {
        "alice":   {"normal": True},
        "bob":     {"normal": True},
        "charlie": {"normal": True},
        "diana":   {"normal": True},
        "eve":     {"normal": False},  # insider threat
    }

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
            conn.execute(
                "INSERT INTO logs (user, login_hour, files_accessed, data_transferred_mb, failed_logins, off_hours_access) VALUES (?,?,?,?,?,?)",
                row
            )

    conn.commit()
    conn.close()
    print("✅  database.db seeded with 1500 log entries.")

if __name__ == "__main__":
    generate()
