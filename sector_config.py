"""Fixed deployment-sector taxonomies and configuration helpers."""

SECTORS = {
    "private_school": {
        "label": "Private School",
        "roles": ("researcher", "intern", "admin_staff", "it_staff"),
        "data_categories": ("projects", "private_documents", "intellectual_property", "pii"),
    },
    "sme_startup": {
        "label": "SME & Startup",
        "roles": ("finance", "hr", "it_admin"),
        "data_categories": ("financial_transactions", "client_data", "employee_records"),
    },
    "uav_disaster_response": {
        "label": "UAV Disaster Response",
        "roles": ("drone_operator",),
        "data_categories": ("drone_platform_access", "disaster_data"),
    },
}
DEFAULT_SECTOR = "sme_startup"


def get_sector_config(sector=None):
    return SECTORS.get(sector or DEFAULT_SECTOR, SECTORS[DEFAULT_SECTOR])


def get_active_sector(conn=None):
    owns_connection = conn is None
    if owns_connection:
        from database import connect
        conn = connect()
    try:
        try:
            row = conn.execute("SELECT sector FROM deployment_config WHERE id = 1").fetchone()
        except Exception:
            row = None
        return row[0] if row and row[0] in SECTORS else (DEFAULT_SECTOR if row else None)
    finally:
        if owns_connection:
            conn.close()


def role_allowed(role, sector=None):
    normalized = str(role or "").strip().lower()
    return normalized in get_sector_config(sector)["roles"]


def data_category_allowed(category, sector=None):
    normalized = str(category or "").strip().lower()
    return normalized in get_sector_config(sector)["data_categories"]


def monitoring_scope(conn):
    """Return the active sector's in-scope event percentage."""
    sector = get_active_sector(conn) or DEFAULT_SECTOR
    categories = set(get_sector_config(sector)["data_categories"])
    total = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    columns = {row[1] for row in conn.execute("PRAGMA table_info(logs)")}
    if "data_category" not in columns or not categories:
        in_scope = 0
    else:
        in_scope = conn.execute(
            "SELECT COUNT(*) FROM logs WHERE LOWER(data_category) IN ({})".format(
                ",".join("?" for _ in categories)
            ),
            tuple(categories),
        ).fetchone()[0]
    return {
        "sector": sector,
        "monitored_categories": sorted(categories),
        "total_events": total,
        "in_scope_events": in_scope,
        "in_scope_percent": round((in_scope / total) * 100, 1) if total else 0.0,
    }
