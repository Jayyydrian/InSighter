"""
Launches the InSighter Flask backend (Logic + Authentication + Data tiers)
as a separate process so the PyQt6 desktop app (Presentation tier) can reliably
wait for a live, reachable HTTP backend before opening the login window.
"""

import atexit
import os
import subprocess
import sys
import time
import traceback

import psutil
import requests

# Make the project root (parent of desktop_app/) importable so `import app` works
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

PORT = int(os.environ.get("INSIGHTER_PORT", 5051))
BASE_URL = f"http://127.0.0.1:{PORT}"
LOG_FILE = os.path.join(PROJECT_ROOT, "backend_startup_error.log")
SERVER_LOG = os.path.join(PROJECT_ROOT, "backend_startup.log")
APP_PY_PATH = os.path.join(PROJECT_ROOT, "app.py")

_server_process = None


def _log_startup_error(message: str):
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write(message)
    except Exception:
        pass


def _ping_backend():
    try:
        response = requests.get(f"{BASE_URL}/login", timeout=1)
        return response.status_code in (200, 302)
    except requests.exceptions.RequestException:
        return False


def _current_backend_is_stale():
    """Return whether the answering backend predates the current app.py."""
    try:
        info = requests.get(f"{BASE_URL}/api/_backend-info", timeout=1).json()
    except (requests.exceptions.RequestException, ValueError):
        return True
    on_disk_mtime = os.path.getmtime(APP_PY_PATH)
    return info.get("app_mtime", 0) < on_disk_mtime - 1


def _kill_process_on_port(port, timeout=5):
    """Best-effort termination of any process bound to the backend port."""
    killed_any = False
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError):
        print(
            f"[server_launcher] insufficient privileges to inspect port {port}; "
            "skipping stale-process cleanup",
            flush=True,
        )
        return False
    for conn in connections:
        if conn.laddr and conn.laddr.port == port and conn.pid:
            try:
                proc = psutil.Process(conn.pid)
                print(
                    f"[server_launcher] stopping stale backend PID {conn.pid} on port {port}",
                    flush=True,
                )
                proc.terminate()
                killed_any = True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    if killed_any:
        deadline = time.time() + timeout
        while time.time() < deadline and _ping_backend():
            time.sleep(0.2)
    return killed_any


def _start_backend_process():
    global _server_process

    env = os.environ.copy()
    env["INSIGHTER_PORT"] = str(PORT)
    env["PYTHONIOENCODING"] = "utf-8"
    os.environ["INSIGHTER_PORT"] = str(PORT)

    try:
        with open(SERVER_LOG, "a", encoding="utf-8") as log_handle:
            _server_process = subprocess.Popen(
                [sys.executable, "app.py"],
                cwd=PROJECT_ROOT,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        print(f"[server_launcher] started backend process on port {PORT}", flush=True)
        return True
    except Exception:
        error_text = traceback.format_exc()
        print("\n========== BACKEND STARTUP ERROR ==========", flush=True)
        print(error_text, flush=True)
        print("===========================================\n", flush=True)
        _log_startup_error(error_text)
        return False


def _stop_backend_process():
    global _server_process
    if _server_process is not None and _server_process.poll() is None:
        try:
            _server_process.terminate()
        except Exception:
            pass


atexit.register(_stop_backend_process)


def ensure_backend_running(timeout=15):
    """Start the Flask backend in a separate process if it is not already reachable."""
    global _server_process

    if _ping_backend():
        if not _current_backend_is_stale():
            return True
        print(
            "[server_launcher] existing backend is running stale code; restarting",
            flush=True,
        )
        _kill_process_on_port(PORT)
        _server_process = None

    if _server_process is None or _server_process.poll() is not None:
        if not _start_backend_process():
            return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _server_process is not None and _server_process.poll() is not None:
            print(f"[server_launcher] backend process exited prematurely. See {LOG_FILE} for details.", flush=True)
            if os.path.exists(LOG_FILE):
                print(f"[server_launcher] startup log written to {LOG_FILE}", flush=True)
            return False

        if _ping_backend():
            return True

        time.sleep(0.2)

    if os.path.exists(LOG_FILE):
        print(f"[server_launcher] Backend failed to start. See {LOG_FILE} for details.", flush=True)

    return False