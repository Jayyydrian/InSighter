"""
Launches the InSighter Flask backend (Logic + Authentication + Data tiers)
as a separate process so the PyQt6 desktop app (Presentation tier) can reliably
wait for a live, reachable HTTP backend before opening the login window.
"""

import atexit
import logging
import os
import subprocess
import sys
import time
import traceback
from collections import namedtuple
from pathlib import Path

import psutil
import requests

logger = logging.getLogger(__name__)

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
last_error = ""
Listener = namedtuple("Listener", "pid is_insighter app_mtime process_name unknown")


def _log_startup_error(message: str):
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write(message)
    except Exception as exc:
        logger.warning("Could not write backend startup error log at %s: %s", LOG_FILE, exc)


def _ping_backend():
    try:
        response = requests.get(f"{BASE_URL}/login", timeout=1)
        return response.status_code in (200, 302)
    except requests.exceptions.RequestException:
        return False


def _identify_listener(port):
    """Return listener ownership and whether it can be identified as InSighter."""
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError):
        return Listener(None, False, None, None, True)

    listener = next(
        (
            connection for connection in connections
            if connection.status == psutil.CONN_LISTEN
            and connection.laddr
            and connection.laddr.port == port
        ),
        None,
    )
    if listener is None:
        return Listener(None, False, None, None, False)

    pid = listener.pid
    if pid is None:
        return Listener(None, False, None, None, True)
    try:
        process = psutil.Process(pid)
        process_name = process.name()
        try:
            command_line = process.cmdline()
            process_cwd = process.cwd()
        except (psutil.AccessDenied, PermissionError):
            return Listener(pid, False, None, process_name, True)

        expected_path = Path(APP_PY_PATH).resolve()
        command_identifies_app = False
        for argument in command_line:
            candidate = Path(argument)
            if not candidate.is_absolute():
                candidate = Path(process_cwd) / candidate
            try:
                if candidate.resolve() == expected_path:
                    command_identifies_app = True
                    break
                if (
                    Path(argument).name.lower() == "app.py"
                    and Path(process_cwd).resolve() == Path(PROJECT_ROOT).resolve()
                ):
                    command_identifies_app = True
                    break
            except (OSError, RuntimeError):
                continue

        if not command_identifies_app:
            return Listener(pid, False, None, process_name, False)

        try:
            response = requests.get(f"{BASE_URL}/api/_backend-info", timeout=1)
            info = response.json() if response.status_code == 200 else None
        except (requests.exceptions.RequestException, ValueError):
            info = None

        if isinstance(info, dict) and "pid" in info and "app_mtime" in info:
            try:
                matches = int(info["pid"]) == pid
                app_mtime = float(info["app_mtime"])
            except (TypeError, ValueError):
                matches = False
                app_mtime = None
            if not matches:
                return Listener(pid, False, None, process_name, False)
            return Listener(pid, True, app_mtime, process_name, False)

        return Listener(pid, True, None, process_name, False)
    except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
        return Listener(pid, False, None, None, True)


def _current_backend_is_stale(listener):
    """Return freshness for an identified backend, or None if it is unknown."""
    if listener is None or not listener.is_insighter:
        return None
    try:
        on_disk_mtime = os.path.getmtime(APP_PY_PATH)
        if listener.app_mtime is not None:
            return listener.app_mtime < on_disk_mtime - 1
        process = psutil.Process(listener.pid)
        return process.create_time() < on_disk_mtime - 1
    except (OSError, psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
        return None


def _port_conflict_reason(listener, port):
    if listener is None or listener.unknown or listener.pid is None or not listener.process_name:
        return f"Port {port} is already in use by another program."
    return (
        f"Port {port} is in use by {listener.process_name} (PID {listener.pid}), "
        "which is not InSighter. Close it or set INSIGHTER_PORT to another port."
    )


def _terminate_listener(listener, timeout=5):
    """Terminate only a listener that was positively identified as InSighter."""
    if listener is None or not listener.is_insighter:
        return False
    current = _identify_listener(PORT)
    if not current.is_insighter or current.pid != listener.pid:
        return False
    try:
        process = psutil.Process(listener.pid)
        process.terminate()
        process.wait(timeout=timeout)
    except psutil.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=timeout)
        except (
            psutil.NoSuchProcess, psutil.AccessDenied, PermissionError,
            psutil.TimeoutExpired,
        ):
            return False
    except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
        return False
    return True


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
        except Exception as exc:
            logger.warning("Could not terminate backend process: %s", exc)


atexit.register(_stop_backend_process)


def ensure_backend_running(timeout=15):
    """Start the Flask backend in a separate process if it is not already reachable."""
    global _server_process
    global last_error

    last_error = ""
    listener = _identify_listener(PORT)
    if listener.pid is not None or listener.unknown:
        if not listener.is_insighter:
            last_error = _port_conflict_reason(listener, PORT)
            return False

        stale = _current_backend_is_stale(listener)
        if stale is None:
            last_error = f"Could not verify the InSighter backend on port {PORT}."
            return False
        if not stale:
            deadline = time.time() + timeout
            while time.time() < deadline:
                if _ping_backend():
                    return True
                time.sleep(0.2)
            last_error = f"The InSighter backend on port {PORT} did not respond."
            return False

        print(
            "[server_launcher] existing InSighter backend is stale; restarting",
            flush=True,
        )
        if not _terminate_listener(listener):
            last_error = f"Could not safely stop stale InSighter backend PID {listener.pid}."
            return False
        _server_process = None
        deadline = time.time() + 5
        while time.time() < deadline:
            current = _identify_listener(PORT)
            if current.pid is None and not current.unknown:
                break
            time.sleep(0.2)
        else:
            last_error = (
                f"Could not free port {PORT} after stopping the stale InSighter backend."
            )
            return False
    elif _ping_backend():
        last_error = f"Port {PORT} is already in use by another program."
        return False

    if _server_process is None or _server_process.poll() is not None:
        if not _start_backend_process():
            last_error = f"Could not start the InSighter backend. See {LOG_FILE} for details."
            return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if _server_process is not None and _server_process.poll() is not None:
            print(f"[server_launcher] backend process exited prematurely. See {LOG_FILE} for details.", flush=True)
            if os.path.exists(LOG_FILE):
                print(f"[server_launcher] startup log written to {LOG_FILE}", flush=True)
            last_error = (
                f"The InSighter backend exited before it was ready. See {LOG_FILE}."
            )
            return False

        if _ping_backend():
            return True

        time.sleep(0.2)

    if os.path.exists(LOG_FILE):
        print(f"[server_launcher] Backend failed to start. See {LOG_FILE} for details.", flush=True)

    last_error = (
        f"The InSighter backend did not start on port {PORT} within {timeout} seconds."
    )
    return False