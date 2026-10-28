"""Start and monitor the local InSighter backend for the desktop client."""

import atexit
from collections import namedtuple
import importlib.util
import logging
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import psutil
import requests

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_PORT = 5051
STARTUP_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 0.25
REQUIRED_MODULES = (
    "flask",
    "werkzeug",
    "requests",
    "psutil",
    "cryptography",
    "numpy",
    "pandas",
    "sklearn",
    "ldap3",
    "PyQt6",
)
PORT = DEFAULT_PORT
BASE_URL = ""
LOG_FILE = str(PROJECT_ROOT / "backend_startup.log")
SERVER_LOG = LOG_FILE
APP_PY_PATH = str(PROJECT_ROOT / "app.py")

_server_process = None
last_error = ""
Listener = namedtuple("Listener", "pid is_insighter app_mtime process_name unknown")


def _log_startup_error(message):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"{message}\n")
    except OSError as exc:
        logger.warning(
            "Could not write backend startup error log at %s: %s", LOG_FILE, exc
        )


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
                if candidate.resolve() == expected_path or (
                    Path(argument).name.lower() == "app.py"
                    and Path(process_cwd).resolve() == PROJECT_ROOT.resolve()
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
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            PermissionError,
            psutil.TimeoutExpired,
        ):
            return False
    except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError):
        return False
    return True


def _set_port(port):
    global PORT, BASE_URL
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError(f"INSIGHTER_PORT must be between 1 and 65535; received {port}.")
    PORT = port
    BASE_URL = f"http://127.0.0.1:{PORT}"
    os.environ["INSIGHTER_PORT"] = str(PORT)


def _database_path():
    configured = os.environ.get("INSIGHTER_DB_PATH")
    path = Path(configured).expanduser() if configured else PROJECT_ROOT / "database.db"
    path = path.resolve()
    os.environ["INSIGHTER_DB_PATH"] = str(path)
    return path


def _restrict_local_secret_permissions(path, is_directory=False):
    resource = "directory" if is_directory else "file"
    if os.name == "nt":
        username = os.environ.get("USERNAME")
        domain = os.environ.get("USERDOMAIN")
        if not username:
            raise RuntimeError("Could not determine the current Windows user for secret-file permissions.")
        account = f"{domain}\\{username}" if domain else username
        permission = f"{account}:(OI)(CI)F" if is_directory else f"{account}:F"
        result = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", permission],
            check=False,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        if result.returncode:
            raise RuntimeError(
                f"Could not restrict permissions on the local InSighter secret {resource}. "
                f"icacls reported: {(result.stderr or result.stdout).strip()}"
            )
    else:
        try:
            os.chmod(path, 0o700 if is_directory else 0o600)
        except OSError as exc:
            raise RuntimeError(
                f"Could not restrict permissions on the local InSighter secret {resource}: {exc}"
            ) from exc


def _local_secret_path():
    """Return the per-user path used for a generated local signing key."""
    user_profile = os.environ.get("USERPROFILE")
    if not user_profile or not user_profile.strip():
        raise RuntimeError(
            "USERPROFILE must be set to create the local InSighter signing key."
        )
    return Path(user_profile).expanduser() / ".insighter" / "secret_key"


def _verify_directory_visible_outside_python(path):
    if os.name != "nt":
        return

    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "dir", str(path)],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            f"The local InSighter secret directory {path} is not visible outside Python "
            f"when using {sys.executable}. "
            f"{detail}"
        )


def _ensure_local_secret():
    """Load a persistent local key, generating one only when the user has none."""
    configured = os.environ.get("INSIGHTER_SECRET_KEY")
    if configured:
        return False

    secret_file = _local_secret_path()
    secret_dir = secret_file.parent
    secret_dir.mkdir(parents=True, exist_ok=True)
    if not secret_dir.is_dir():
        raise RuntimeError(
            f"Could not create the local InSighter secret directory: {secret_dir}"
        )
    _verify_directory_visible_outside_python(secret_dir)
    _restrict_local_secret_permissions(secret_dir, is_directory=True)

    generated = False
    try:
        with secret_file.open("x", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(48))
            generated = True
    except FileExistsError:
        pass

    try:
        _restrict_local_secret_permissions(secret_file)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        if generated:
            try:
                secret_file.unlink()
            except OSError as cleanup_error:
                raise RuntimeError(
                    "Could not secure the generated local secret file, and could not "
                    f"remove it: {secret_file} ({cleanup_error})"
                ) from exc
        raise RuntimeError(
            f"Could not secure the local secret file {secret_file}; refusing to use it: {exc}"
        ) from exc
    key = secret_file.read_text(encoding="utf-8").strip()
    if not key:
        raise RuntimeError(
            f"The local secret file is empty: {secret_file}. Remove it and restart to generate a new key."
        )
    os.environ["INSIGHTER_SECRET_KEY"] = key
    return generated


def _port_is_free(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False


def _healthy_backend(port):
    base_url = f"http://127.0.0.1:{int(port)}"
    try:
        response = requests.get(f"{base_url}/api/_backend-info", timeout=1)
        if response.status_code != 200:
            return False
        info = response.json()
        return isinstance(info, dict) and "pid" in info and "app_mtime" in info
    except (requests.RequestException, ValueError):
        return False


def _next_free_port(start):
    for port in range(max(1, int(start) + 1), 65536):
        if _port_is_free(port):
            return port
    raise RuntimeError(
        f"No free local TCP port is available after configured port {start}."
    )


def _preflight_checks(database_path):
    if sys.version_info < (3, 11):
        return (
            f"InSighter desktop requires Python 3.11 or newer; "
            f"this interpreter is {sys.version_info[0]}.{sys.version_info[1]}."
        )

    missing = [
        module for module in REQUIRED_MODULES
        if importlib.util.find_spec(module) is None
    ]
    if missing:
        return (
            "Required Python packages are missing: "
            f"{', '.join(missing)}. Install them with "
            "`python -m pip install -r requirements.txt -r requirements-desktop.txt`."
        )

    try:
        with tempfile.NamedTemporaryFile(
            prefix=".insighter-write-check-", dir=PROJECT_ROOT, delete=True
        ):
            pass
    except OSError as exc:
        return (
            f"The InSighter folder is not writable ({PROJECT_ROOT}): {exc}. "
            "Move the project to a writable folder."
        )

    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=".insighter-db-check-", dir=database_path.parent, delete=True
        ):
            pass
    except OSError as exc:
        return (
            f"The database location is not writable ({database_path.parent}): {exc}. "
            "Choose a writable INSIGHTER_DB_PATH."
        )

    return None


def _begin_startup_log():
    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    with open(LOG_FILE, "w", encoding="utf-8") as handle:
        handle.write(f"InSighter backend startup: {timestamp}\n")
        handle.flush()


def _append_startup_log(message):
    with open(LOG_FILE, "a", encoding="utf-8") as handle:
        handle.write(f"{message}\n")
        handle.flush()


def _read_log_tail(limit=30):
    try:
        lines = Path(LOG_FILE).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"Could not read startup log {LOG_FILE}: {exc}"
    return "\n".join(lines[-limit:]) or "(startup log is empty)"


def _failure_hint(log_text):
    lowered = log_text.lower()
    if "insighter_db_key" in lowered:
        return (
            "Hint: set INSIGHTER_DB_KEY before starting the desktop app when SQLCipher "
            "is installed."
        )
    if "insighter_secret_key" in lowered:
        return "Hint: set INSIGHTER_SECRET_KEY, or check the local per-user secret file."
    if "insighter_admin_username" in lowered or "insighter_admin_password" in lowered:
        return (
            "Hint: set INSIGHTER_ADMIN_USERNAME and INSIGHTER_ADMIN_PASSWORD together; "
            "the initial password must be at least 14 characters."
        )
    if "modulenotfounderror" in lowered or "no module named" in lowered:
        return (
            "Hint: install backend and desktop dependencies with "
            "`python -m pip install -r requirements.txt -r requirements-desktop.txt`."
        )
    if "readonly database" in lowered or "unable to open database file" in lowered:
        return (
            "Hint: choose a writable INSIGHTER_DB_PATH and ensure its parent folder is writable."
        )
    if "address already in use" in lowered or "winerror 10048" in lowered:
        return "Hint: the selected port was claimed during startup; retry or set INSIGHTER_PORT."
    if "permission denied" in lowered or "access is denied" in lowered:
        return "Hint: move the project/database to a folder your Windows user can write."
    return "Hint: review the traceback below and correct the reported configuration or startup error."


def _sqlcipher_available():
    try:
        importlib.import_module("sqlcipher3.dbapi2")
    except ImportError:
        return False
    return True


def _require_sqlcipher_database_key():
    if _sqlcipher_available() and not os.environ.get("INSIGHTER_DB_KEY"):
        raise RuntimeError(
            "SQLCipher is installed, but INSIGHTER_DB_KEY must be set before startup. "
            "The desktop launcher will not reuse INSIGHTER_SECRET_KEY as the database key."
        )


def _format_startup_failure(message):
    tail = _read_log_tail()
    hint = _failure_hint(tail)
    error = (
        f"{message}\n\n{hint}\n\n"
        f"Startup log ({LOG_FILE}), last lines:\n{tail}"
    )
    print(f"[server_launcher] {error}", flush=True)
    return error


def _prepare_environment():
    database_path = _database_path()
    configured_port = os.environ.get("INSIGHTER_PORT")
    port = int(configured_port) if configured_port else DEFAULT_PORT
    _set_port(port)
    return database_path, port


def _start_backend_process():
    global _server_process

    env = os.environ.copy()
    if not env.get("INSIGHTER_DB_PATH") or not env.get("INSIGHTER_PORT"):
        raise RuntimeError(
            "Refusing to start the backend without explicit INSIGHTER_DB_PATH and INSIGHTER_PORT."
        )
    Path(env["INSIGHTER_DB_PATH"]).expanduser().resolve()
    int(env["INSIGHTER_PORT"])
    env["INSIGHTER_COOKIE_SECURE"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        with open(LOG_FILE, "a", encoding="utf-8") as log_handle:
            _server_process = subprocess.Popen(
                [sys.executable, APP_PY_PATH],
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        with open(LOG_FILE, "a", encoding="utf-8") as log_handle:
            traceback.print_exc(file=log_handle)
        return False


def _stop_backend_process():
    global _server_process
    if _server_process is not None and _server_process.poll() is None:
        try:
            _server_process.terminate()
            _server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _server_process.kill()
            _server_process.wait(timeout=5)
        except OSError as exc:
            logger.warning("Could not terminate backend process started by this app: %s", exc)


atexit.register(_stop_backend_process)


def ensure_backend_running(timeout=STARTUP_TIMEOUT_SECONDS):
    """Preflight, start, and wait for a loopback backend without killing other processes."""
    global _server_process
    global last_error
    global BASE_URL

    last_error = ""
    try:
        _begin_startup_log()
        database_path, requested_port = _prepare_environment()
        if not os.environ.get("INSIGHTER_DB_PATH") or not os.environ.get("INSIGHTER_PORT"):
            raise RuntimeError("INSIGHTER_DB_PATH and INSIGHTER_PORT must be set before startup.")
        if timeout <= 0:
            last_error = "Backend startup requires a positive readiness timeout."
            return False

        if _healthy_backend(requested_port):
            print(
                f"[server_launcher] Using existing InSighter backend on port {requested_port}.",
                flush=True,
            )
            return True

        if not _port_is_free(requested_port):
            selected_port = _next_free_port(requested_port)
            _set_port(selected_port)
            print(
                f"[server_launcher] Port {requested_port} is occupied; "
                f"using next free port {selected_port}.",
                flush=True,
            )

        preflight_error = _preflight_checks(database_path)
        if preflight_error:
            _append_startup_log(f"Preflight failed: {preflight_error}")
            last_error = _format_startup_failure(preflight_error)
            return False

        try:
            _require_sqlcipher_database_key()
        except RuntimeError as exc:
            message = str(exc)
            _append_startup_log(message)
            last_error = _format_startup_failure(message)
            return False

        try:
            generated_key = _ensure_local_secret()
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            message = f"Could not prepare the local application secret: {exc}"
            _append_startup_log(message)
            last_error = _format_startup_failure(message)
            return False
        if generated_key:
            print(
                f"[server_launcher] Generated a stable local secret at "
                f"{_local_secret_path()}.",
                flush=True,
            )

        # The explicit database path is visible in the same status line used for progress.
        print(
            f"[server_launcher] Starting backend on port {PORT}; "
            f"database: {database_path}",
            flush=True,
        )
        # A bind can race between the availability probe and backend startup. Retry once.
        for attempt in range(2):
            _server_process = None
            if not _start_backend_process():
                last_error = _format_startup_failure(
                    "Could not start the InSighter backend."
                )
                return False

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if _server_process is not None and _server_process.poll() is not None:
                    log_tail = _read_log_tail()
                    if attempt == 0 and (
                        "address already in use" in log_tail.lower()
                        or "winerror 10048" in log_tail.lower()
                    ):
                        _set_port(_next_free_port(PORT))
                        break
                    last_error = _format_startup_failure(
                        "The InSighter backend exited before it was ready."
                    )
                    return False
                if _healthy_backend(PORT):
                    return True
                time.sleep(POLL_INTERVAL_SECONDS)
            else:
                last_error = _format_startup_failure(
                    f"The InSighter backend did not start on port {PORT} within "
                    f"{timeout} seconds."
                )
                _stop_backend_process()
                return False

        last_error = _format_startup_failure(
            "The InSighter backend could not claim a free local port after one retry."
        )
        return False
    except (OSError, ValueError, RuntimeError) as exc:
        try:
            _append_startup_log(f"Startup preflight failed: {exc}")
        except OSError:
            pass
        last_error = _format_startup_failure(
            f"InSighter startup preflight failed: {exc}"
        )
        return False
    finally:
        # Keep ApiClient's imported BASE_URL aligned with any port selected above.
        BASE_URL = f"http://127.0.0.1:{PORT}"
