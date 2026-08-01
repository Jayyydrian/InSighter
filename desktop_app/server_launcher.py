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

import requests

# Make the project root (parent of desktop_app/) importable so `import app` works
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

PORT = 5050
BASE_URL = f"http://127.0.0.1:{PORT}"
LOG_FILE = os.path.join(PROJECT_ROOT, "backend_startup_error.log")
SERVER_LOG = os.path.join(PROJECT_ROOT, "backend_startup.log")

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


def _start_backend_process():
    global _server_process

    env = os.environ.copy()
    env["INSIGHTER_PORT"] = str(PORT)

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
        return True

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