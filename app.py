import re

from flask import Flask, render_template, jsonify, request, redirect, url_for, session
from werkzeug.exceptions import BadRequest
from model import score_users, get_recent_alerts, inject_live_event
from generate_logs import generate
from llm_advisor import explain_alert
from auth import (
    admin_required,
    compliance_required,
    init_users_table,
    login_required,
    log_action,
    authenticate_login,
)
from ingestion import (
    active_directory_status,
    ensure_logs_schema,
    ingest_from_configured_source,
    store_events,
)
from database import Row, connect, ensure_database
from privacy import hash_identifier
import psutil, time, os, threading, secrets, requests
from sector_config import DEFAULT_SECTOR, SECTORS, get_active_sector, get_sector_config, monitoring_scope as calculate_monitoring_scope

app = Flask(__name__)
app.secret_key = os.environ.get("INSIGHTER_SECRET_KEY", secrets.token_hex(32))
# Lets desktop_app/server_launcher.py tell a genuinely-fresh backend process apart
# from a stale one left running from before app.py was last edited on disk, instead
# of silently reusing whatever already answers on the port (see /api/_backend-info).
_APP_FILE_MTIME = os.path.getmtime(__file__)

ensure_database()
startup_conn = connect()
has_logs = startup_conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='logs'"
).fetchone()
startup_conn.close()
if not has_logs:
    generate()

init_users_table()  # creates `users` table + seeds admin/manager accounts if missing
ensure_logs_schema()

# ── Process-level resource tracker ──────────────────────────────────────────
_proc = psutil.Process(os.getpid())
_proc.cpu_percent(interval=None)   # prime the counter (first call always 0)

_stats = {
    "ml_inference_ms":  0.0,
    "db_read_ms":       0.0,
    "db_write_ms":      0.0,
    "events_total":     0,
    "events_per_sec":   0.0,
    "last_tick_ms":     0.0,
    "_tick_times":      [],   # internal ring buffer for eps
}
_stats_lock = threading.Lock()

def record_tick(ml_ms, db_read_ms, db_write_ms):
    now = time.time()
    with _stats_lock:
        _stats["ml_inference_ms"] = round(ml_ms, 2)
        _stats["db_read_ms"]      = round(db_read_ms, 2)
        _stats["db_write_ms"]     = round(db_write_ms, 2)
        _stats["last_tick_ms"]    = round(ml_ms + db_read_ms + db_write_ms, 2)
        _stats["events_total"]   += 1
        _stats["_tick_times"].append(now)
        # keep last 10 timestamps to compute events/sec
        _stats["_tick_times"] = _stats["_tick_times"][-10:]
        times = _stats["_tick_times"]
        if len(times) >= 2:
            span = times[-1] - times[0]
            _stats["events_per_sec"] = round((len(times) - 1) / span, 2) if span > 0 else 0.0

@app.route("/api/_backend-info")
def backend_info():
    """Unauthenticated freshness probe for desktop_app/server_launcher.py."""
    return jsonify({"pid": os.getpid(), "app_mtime": _APP_FILE_MTIME})


# ── Auth routes ──────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role, retry_after = authenticate_login(username, password)
        if role:
            session["username"] = username
            session["role"] = role
            log_action(username, "LOGIN_SUCCESS", f"role={role}")
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
        if retry_after:
            log_action(username or "(blank)", "LOGIN_COOLDOWN", "too many failed attempts")
            return render_template(
                "login.html",
                error=f"Too many unsuccessful sign-in attempts. Try again in about {retry_after} seconds.",
            ), 429
        log_action(username or "(blank)", "LOGIN_FAILED", "invalid credentials")
        return render_template("login.html", error="Invalid username or password."), 401
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    if "username" in session:
        log_action(session["username"], "LOGOUT", "")
    session.clear()
    return redirect(url_for("login"))


# ── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def dashboard():
    if session.get("role") == "admin":
        return render_template("dashboard.html")

    # management role: aggregate-only summary view, no individual user data
    users = score_users()
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for u in users:
        counts[u["risk_level"]] = counts.get(u["risk_level"], 0) + 1
    return render_template(
        "summary.html",
        username=session.get("username"),
        role=session.get("role"),
        high_count=counts["HIGH"],
        medium_count=counts["MEDIUM"],
        low_count=counts["LOW"],
    )

@app.route("/api/scores")
@admin_required
def scores():
    return jsonify(score_users())

@app.route("/api/alerts")
@admin_required
def alerts():
    return jsonify(get_recent_alerts())


# In-memory cache for AI explanations, keyed by alert (log) id -- the underlying
# log row never changes once written, so a given alert only needs to be sent to
# the LLM once. Not persisted across restarts; that's fine for a demo/dev tool.
_alert_explanation_cache = {}
_alert_explanation_cache_lock = threading.Lock()


@app.route("/api/alerts/<int:alert_id>/explain", methods=["POST"])
@admin_required
def explain_alert_route(alert_id):
    with _alert_explanation_cache_lock:
        cached = _alert_explanation_cache.get(alert_id)
    if cached is not None:
        return jsonify(cached)

    alert = next((a for a in get_recent_alerts() if a["id"] == alert_id), None)
    if alert is None:
        return jsonify({"error": "Alert not found (it may have scrolled out of the recent window)."}), 404

    sector = get_active_sector() or DEFAULT_SECTOR
    result = explain_alert(alert, sector, get_sector_config(sector))

    if result["available"]:
        with _alert_explanation_cache_lock:
            _alert_explanation_cache[alert_id] = result
    return jsonify(result)


@app.route("/api/compliance/alerts")
@compliance_required
def compliance_alerts():
    """Return flagged incidents with stable pseudonymous user identifiers."""
    return jsonify([
        {**alert, "user": hash_identifier(alert["user"])}
        for alert in get_recent_alerts()
    ])

@app.route("/api/sessions")
@admin_required
def sessions():
    """Raw session/log rows for the User Sessions page (most recent first)."""
    conn = connect()
    conn.row_factory = Row
    rows = conn.execute(
        "SELECT * FROM logs ORDER BY id DESC LIMIT 150"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/reseed")
@admin_required
def reseed():
    generate()
    with _stats_lock:
        _stats["events_total"]   = 0
        _stats["events_per_sec"] = 0.0
        _stats["_tick_times"]    = []
    with _alert_explanation_cache_lock:
        _alert_explanation_cache.clear()
    log_action(session.get("username"), "DATA_RESEEDED", "synthetic log data regenerated")
    return jsonify({"status": "reseeded"})

@app.route("/api/simulate")
@admin_required
def simulate():
    _run_simulation_tick()
    return jsonify({"status": "ok"})


def _run_simulation_tick():
    # DB write
    t0 = time.perf_counter()
    inject_live_event()
    db_write_ms = (time.perf_counter() - t0) * 1000

    # DB read + ML inference
    t1 = time.perf_counter()
    import pandas as pd
    conn = connect()
    df = pd.read_sql("SELECT * FROM logs", conn)
    conn.close()
    db_read_ms = (time.perf_counter() - t1) * 1000

    t2 = time.perf_counter()
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler
    features = ["login_hour","files_accessed","data_transferred_mb","failed_logins","off_hours_access"]
    scaler = StandardScaler()
    X = scaler.fit_transform(df[features])
    model = IsolationForest(
        contamination=0.08,
        random_state=42,
        n_estimators=max(40, int(os.environ.get("INSIGHTER_MODEL_ESTIMATORS", "80"))),
    )
    model.fit_predict(X)
    ml_ms = (time.perf_counter() - t2) * 1000

    record_tick(ml_ms, db_read_ms, db_write_ms)


def _background_simulation_loop(interval_seconds):
    """Keeps injecting + scoring synthetic events on a timer, independent of
    any logged-in session or open browser tab. This is what makes the demo
    "live" the instant the server process starts -- before anyone logs in,
    and continuously across account switches -- instead of depending on the
    old client-side JS interval that only ran while an admin had the
    dashboard open with "Start Simulation" toggled on."""
    while True:
        try:
            _run_simulation_tick()
        except Exception:
            # A transient DB/model hiccup should never kill the background loop.
            pass
        time.sleep(interval_seconds)


if os.environ.get("INSIGHTER_DISABLE_BACKGROUND_SIM") != "1":
    _sim_interval = float(os.environ.get("INSIGHTER_SIM_INTERVAL_SECONDS", "6"))
    threading.Thread(
        target=_background_simulation_loop, args=(_sim_interval,), daemon=True
    ).start()

@app.route("/api/whoami")
@login_required
def whoami():
    return jsonify({"username": session.get("username"), "role": session.get("role")})


@app.route("/api/summary")
@login_required
def summary_counts():
    """Aggregate-only counts, safe for the 'management' role (no per-user data)."""
    users = score_users()
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for u in users:
        counts[u["risk_level"]] = counts.get(u["risk_level"], 0) + 1
    return jsonify(counts)


def _deployment_config_payload():
    conn = connect()
    row = conn.execute("""
        SELECT sector, threshold_high, threshold_medium, log_targets,
               sync_interval_minutes, drone_operator_username, updated_at
        FROM deployment_config WHERE id = 1
    """).fetchone()
    conn.close()
    keys = ["sector", "threshold_high", "threshold_medium", "log_targets",
            "sync_interval_minutes", "drone_operator_username", "updated_at"]
    payload = dict(zip(keys, row))
    payload["taxonomy"] = get_sector_config(payload["sector"])
    return payload


@app.route("/api/deployment-config", methods=["GET"])
@app.route("/api/uav-config", methods=["GET"])
@admin_required
def get_uav_config():
    return jsonify(_deployment_config_payload())

@app.route("/api/deployment-config", methods=["POST"])
@app.route("/api/uav-config", methods=["POST"])
@admin_required
def update_uav_config():
    data = request.get_json(force=True)
    # Only change the sector if the caller explicitly included it; otherwise
    # keep whatever is currently configured. Previously this fell back to
    # DEFAULT_SECTOR ("sme_startup") whenever "sector" was missing from the
    # payload, which silently reverted the deployment to SME/Startup any time
    # settings were saved from a client that didn't send a sector field.
    if "sector" in data:
        sector = data.get("sector") or DEFAULT_SECTOR
        if sector not in SECTORS:
            return jsonify({"error": "Invalid deployment sector."}), 400
    else:
        sector = get_active_sector() or DEFAULT_SECTOR
    conn = connect()
    conn.execute("""
        UPDATE deployment_config SET
            sector = ?,
            threshold_high = ?,
            threshold_medium = ?,
            log_targets = ?,
            sync_interval_minutes = ?,
            drone_operator_username = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 1
    """, (
        sector,
        data.get("threshold_high", 45),
        data.get("threshold_medium", 30),
        data.get("log_targets", ""),
        data.get("sync_interval_minutes", 15),
        data.get("drone_operator_username", "drone_operator"),
    ))
    conn.commit()
    conn.close()
    log_action(session.get("username"), "CONFIG_UPDATED",
               f"sector={sector}, thresholds={data.get('threshold_high')}/{data.get('threshold_medium')}, "
               f"sync={data.get('sync_interval_minutes')}min")
    return jsonify({"status": "saved"})


@app.route("/api/deployment-config/taxonomy")
@login_required
def deployment_taxonomy():
    sector = get_active_sector()
    return jsonify({"sector": sector, **get_sector_config(sector)})


@app.route("/api/deployment-config/monitoring-scope")
@login_required
def monitoring_scope():
    conn = connect()
    result = calculate_monitoring_scope(conn)
    conn.close()
    return jsonify(result)


@app.route("/api/audit-log")
@admin_required
def audit_log():
    """
    Chapter 3's Privacy-Compliant Audit Mode: controlled visibility into
    who accessed or changed what, for Data Privacy Act accountability.
    Available to administrators with real usernames.
    """
    conn = connect()
    conn.row_factory = Row
    rows = conn.execute(
        "SELECT username, action, detail, timestamp FROM audit_log ORDER BY id DESC LIMIT 100"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/compliance/audit-log")
@compliance_required
def compliance_audit_log():
    """Return the audit trail with every available username pseudonymized."""
    conn = connect()
    conn.row_factory = Row
    rows = conn.execute(
        "SELECT username, action, detail, timestamp FROM audit_log ORDER BY id DESC"
    ).fetchall()
    usernames = [row[0] for row in conn.execute(
        """SELECT username FROM users
           UNION
           SELECT user FROM logs"""
    ).fetchall()]
    conn.close()

    def pseudonymize_detail(detail):
        for username in sorted((name for name in usernames if name), key=len, reverse=True):
            detail = re.sub(
                rf"(?<!\w){re.escape(username)}(?!\w)",
                hash_identifier(username),
                detail,
                flags=re.IGNORECASE,
            )
        return detail

    return jsonify([
        {
            **dict(row),
            "username": hash_identifier(row["username"]) if row["username"] else None,
            "detail": pseudonymize_detail(row["detail"] or ""),
        }
        for row in rows
    ])


@app.route("/api/integrations")
@login_required
def integrations():
    """
    Status of the three data-source integrations named in Chapter 3.
    Honestly reflects current dev state: synthetic data only, no live
    API connection has been implemented yet.
    """
    provider = os.environ.get("INSIGHTER_SOURCE_PROVIDER", "").strip().lower()
    configured = bool(os.environ.get("INSIGHTER_SOURCE_API_URL", "").strip())
    supported = {"active_directory", "google_workspace", "microsoft_365"}
    ad_status, ad_note = active_directory_status()
    return jsonify([
        {
            "name": name,
            "key": key,
            "status": ad_status if key == "active_directory" else (
                "connected"
                if os.environ.get("INSIGHTER_SOURCE_API_URL", "").strip() and provider == key
                else "not_connected"
            ),
            "note": (
                ad_note
                if key == "active_directory"
                else "REST connector configured; use Ingest Events to append protected events."
                if configured and provider == key
                else (
                    "Set INSIGHTER_SOURCE_PROVIDER to this source to connect it."
                    if configured and provider in supported
                    else "API connection not configured — currently running on synthetic data."
                )
            ),
        }
        for name, key in (
            ("Active Directory", "active_directory"),
            ("Google Workspace", "google_workspace"),
            ("Microsoft 365", "microsoft_365"),
        )
    ])


@app.route("/api/ingest", methods=["POST"])
@admin_required
def ingest():
    """Ingest events by push or pull through the same normalization path.

    Push mode accepts ``{"events": [...]}`` in the JSON request body. When
    that key is absent, the endpoint keeps its original pull behavior and
    fetches events from the configured source.
    """
    try:
        payload = request.get_json(silent=False) if request.is_json else None
        if isinstance(payload, dict) and "events" in payload:
            inserted = store_events(payload["events"], source="push")
        else:
            inserted = ingest_from_configured_source()
    except (BadRequest, ValueError, requests.RequestException, RuntimeError, OSError) as exc:
        log_action(session.get("username"), "INGEST_FAILED", str(exc))
        return jsonify({"error": str(exc)}), 400
    log_action(session.get("username"), "INGEST_COMPLETED", f"events={inserted}")
    return jsonify({"status": "ok", "inserted": inserted})


@app.route("/api/resources")
@login_required
def resources():
    # CPU % for THIS process only (interval=None uses delta since last call)
    proc_cpu = round(_proc.cpu_percent(interval=None), 1)
    mem      = _proc.memory_info()
    ram_mb   = round(mem.rss / 1024 / 1024, 1)

    # Normalize process CPU by core count so it's 0-100% scale
    cores       = psutil.cpu_count(logical=True) or 1
    proc_cpu_n  = round(proc_cpu / cores, 1)

    with _stats_lock:
        snap = dict(_stats)

    return jsonify({
        "proc_cpu":        proc_cpu_n,       # process CPU % (normalized)
        "proc_cpu_raw":    proc_cpu,          # raw (can exceed 100% on multi-core)
        "ram_used_mb":     ram_mb,
        "ram_total_mb":    round(psutil.virtual_memory().total / 1024 / 1024, 1),
        "ram_percent":     round(mem.rss / psutil.virtual_memory().total * 100, 1),
        "threads":         _proc.num_threads(),
        "disk_percent":    round(psutil.disk_usage("/").percent, 1),
        "uptime_s":        round(time.time() - _proc.create_time()),
        "ml_inference_ms": snap["ml_inference_ms"],
        "db_read_ms":      snap["db_read_ms"],
        "db_write_ms":     snap["db_write_ms"],
        "last_tick_ms":    snap["last_tick_ms"],
        "events_total":    snap["events_total"],
        "events_per_sec":  snap["events_per_sec"],
    })

if __name__ == "__main__":
    port = int(os.environ.get("INSIGHTER_PORT", 5051))
    print(f"\n  InSighter backend -> http://127.0.0.1:{port}\n")
    app.run(debug=False, port=port)   # debug=False -> no reloader, cleaner CPU readings
