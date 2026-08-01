"""
Launches the InSighter Flask backend (Logic + Authentication + Data tiers)
in a background thread so the PyQt6 desktop app (Presentation tier) has
something to talk to, without requiring the admin to start it separately.
"""

import os
import sys
import time
import threading
import traceback
import requests

# Make the project root (parent of desktop_app/) importable so `import app` works
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

PORT = 5050
BASE_URL = f"http://127.0.0.1:{PORT}"
LOG_FILE = os.path.join(PROJECT_ROOT, "backend_startup_error.log")

_server_thread = None


def _run_server():
    try:
        os.environ["INSIGHTER_PORT"] = str(PORT)

        # Ensure database.db and templates are found
        os.chdir(PROJECT_ROOT)

        import app as flask_app_module

        print(f"[server_launcher] app module imported OK, starting on port {PORT}", flush=True)

        flask_app_module.app.run(
            host="127.0.0.1",
            port=PORT,
            debug=False,
            use_reloader=False,
        )

    except Exception:
        error_text = traceback.format_exc()

        # Always print (may or may not be visible depending on how the GUI is launched)
        print("\n========== BACKEND STARTUP ERROR ==========", flush=True)
        print(error_text, flush=True)
        print("===========================================\n", flush=True)

        # Always write to a log file so the error is visible no matter what
        try:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write(error_text)
        except Exception:
            pass


def ensure_backend_running(timeout=8):
    """Start the Flask backend in a daemon thread if it isn't already running."""
    global _server_thread

    # Already running?
    try:
        requests.get(f"{BASE_URL}/login", timeout=1)
        return True
    except requests.exceptions.RequestException:
        pass

    # Don't launch twice
    if _server_thread is None or not _server_thread.is_alive():
        _server_thread = threading.Thread(target=_run_server, daemon=True)
        _server_thread.start()

    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            requests.get(f"{BASE_URL}/login", timeout=1)
            return True
        except requests.exceptions.RequestException:
            time.sleep(0.2)

    # Timed out — check if a log file was written with the real error
    if os.path.exists(LOG_FILE):
        print(f"[server_launcher] Backend failed to start. See {LOG_FILE} for details.", flush=True)

    return False