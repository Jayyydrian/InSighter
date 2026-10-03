"""Sector-aware numeric activity profiles shared by seeding and live simulation."""


def activity_values(sector, anomalous, rng):
    """Return the fixed detector feature tuple with bounded sector-appropriate variation."""
    if sector == "uav_disaster_response":
        if anomalous:
            login_hour = rng.choice([rng.randint(0, 4), rng.randint(21, 23)])
            files_accessed = rng.randint(35, 100)
            data_transferred_mb = round(rng.uniform(180, 850), 2)
            failed_logins = rng.randint(3, 8)
        else:
            login_hour = rng.randint(6, 21)
            files_accessed = rng.randint(0, 12)
            data_transferred_mb = round(rng.uniform(5, 80), 2)
            failed_logins = rng.randint(0, 1)
    elif anomalous:
        login_hour = rng.choice([rng.randint(0, 5), rng.randint(21, 23)])
        files_accessed = rng.randint(55, 120)
        data_transferred_mb = round(rng.uniform(350, 1000), 2)
        failed_logins = rng.randint(4, 10)
    else:
        login_hour = rng.randint(8, 18)
        files_accessed = rng.randint(1, 15)
        data_transferred_mb = round(rng.uniform(1, 40), 2)
        failed_logins = rng.randint(0, 1)

    off_hours_access = int(login_hour < 6 or login_hour > 21)
    return login_hour, files_accessed, data_transferred_mb, failed_logins, off_hours_access