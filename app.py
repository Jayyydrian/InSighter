from flask import Flask, render_template, jsonify, request, redirect, url_for, session
from werkzeug.exceptions import BadRequest
from model import score_users, get_recent_alerts, inject_live_event
from generate_logs import generate
from auth import init_users_table, verify_login, login_required, admin_required, log_action
from ingestion import ensure_logs_schema, ingest_events, ingest_from_api
from database import Row, connect, ensure_database
import psutil, time, os, threading, secrets, requests

app = Flask(__name__)
app.secret_key = os.environ.get("INSIGHTER_SECRET_KEY", secrets.token_hex(32))

ensure_database()
startup_conn = connect()
has_logs = startup_conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='logs'"
).fetchone()
startup_conn.close()
if not has_logs:
    generate()

init_users_table()  # creates `users` table + seeds admin/manager accounts if missing
ensure_logs_schema()  # upgrades databases created before API ingestion was added

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

# ── Auth routes ──────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = verify_login(username, password)
        if role:
            session["username"] = username
            session["role"] = role
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
        return render_template("login.html", error="Invalid username or password."), 401
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
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
    return jsonify({"status": "reseeded"})

@app.route("/api/simulate")
@admin_required
def simulate():
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
    model = IsolationForest(contamination=0.08, random_state=42, n_estimators=200)
    model.fit_predict(X)
    ml_ms = (time.perf_counter() - t2) * 1000

    record_tick(ml_ms, db_read_ms, db_write_ms)
    return jsonify({"status": "ok"})

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


@app.route("/api/uav-config", methods=["GET"])
@admin_required
def get_uav_config():
    conn = connect()
    row = conn.execute("""
        SELECT threshold_high, threshold_medium, log_targets,
               sync_interval_minutes, drone_operator_username, updated_at
        FROM uav_config WHERE id = 1
    """).fetchone()
    conn.close()
    keys = ["threshold_high", "threshold_medium", "log_targets",
            "sync_interval_minutes", "drone_operator_username", "updated_at"]
    return jsonify(dict(zip(keys, row)))

@app.route("/api/uav-config", methods=["POST"])
@admin_required
def update_uav_config():
    data = request.get_json(force=True)
    conn = connect()
    conn.execute("""
        UPDATE uav_config SET
            threshold_high = ?,
            threshold_medium = ?,
            log_targets = ?,
            sync_interval_minutes = ?,
            drone_operator_username = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 1
    """, (
        data.get("threshold_high", 45),
        data.get("threshold_medium", 30),
        data.get("log_targets", ""),
        data.get("sync_interval_minutes", 15),
        data.get("drone_operator_username", "drone_operator"),
    ))
    conn.commit()
    conn.close()
    return jsonify({"status": "saved"})


@app.route("/api/audit-log")
@login_required
def audit_log():
    """
    Chapter 3's Privacy-Compliant Audit Mode: controlled visibility into
    who accessed or changed what, for Data Privacy Act accountability.
    Available to both roles (admin + management), per the design spec.
    """
    conn = connect()
    conn.row_factory = Row
    rows = conn.execute(
        "SELECT username, action, detail, timestamp FROM audit_log ORDER BY id DESC LIMIT 100"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/integrations")
@login_required
def integrations():
    """
    Status of the three data-source integrations named in Chapter 3.
    The generic REST connector is the foundation for provider-specific
    connections; provider credentials and mappings are still required.
    """
    configured = bool(os.environ.get("INSIGHTER_SOURCE_API_URL", "").strip())
    provider = os.environ.get("INSIGHTER_SOURCE_PROVIDER", "").strip().lower()
    supported = {"active_directory", "google_workspace", "microsoft_365"}
    return jsonify([
        {
            "name": name,
            "key": key,
            "status": "connected" if configured and provider == key else "not_connected",
            "note": (
                "REST connector configured; use Ingest API to append protected events."
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
    fetches events from ``INSIGHTER_SOURCE_API_URL``. Both modes remain behind
    the same administrator authentication and use ``ingest_events`` for
    validation, privacy protection, and SQLCipher-backed persistence.
    """
    try:
        payload = request.get_json(silent=False) if request.is_json else None
        if isinstance(payload, dict) and "events" in payload:
            inserted = ingest_events(payload["events"], source="push")
        else:
            inserted = ingest_from_api()
    except (BadRequest, ValueError, requests.RequestException) as exc:
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
    port = int(os.environ.get("INSIGHTER_PORT", 5000))
    print(f"\n  InSighter backend  ->  http://127.0.0.1:{port}\n")
    app.run(debug=False, port=port)   # debug=False → no reloader, cleaner CPU readings
