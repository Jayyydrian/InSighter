"""Transparent, low-rate endpoint telemetry collector for InSighter."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import getpass
from ipaddress import ip_address
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import queue
import re
import signal
import socket
import sys
import threading
import time
from typing import Any
import uuid

import psutil
import requests

from failed_login_monitor import FailedLoginMonitor, FailedLoginObservation

SENSOR_VERSION = "1.0.0"
ALLOWED_ROLES = {
    "researcher", "intern", "admin_staff", "it_staff", "drone_operator",
}
ALLOWED_CATEGORIES = {
    "projects", "private_documents", "intellectual_property", "pii",
    "drone_platform_access", "disaster_data",
}
LOGGER = logging.getLogger("insighter.sensor")
STOP_EVENT = threading.Event()


class ConfigurationError(ValueError):
    """Raised when the external sensor configuration is incomplete or invalid."""


class PermanentIngestError(RuntimeError):
    """Raised when the backend rejects an event that should not be retried as-is."""

    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class JsonLogFormatter(logging.Formatter):
    """Emit one machine-readable JSON record per line in UTC."""

    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configuration_path() -> Path:
    """Locate config.json beside the executable or source file."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "config.json"
    return Path(__file__).resolve().parent / "config.json"


def _require_object(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    if not isinstance(value, dict):
        raise ConfigurationError(f"Configuration section '{key}' must be an object.")
    return value


def _number(section: dict[str, Any], key: str, minimum: float, maximum: float) -> float:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"'{key}' must be a number from {minimum:g} to {maximum:g}.")
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"'{key}' must be a number from {minimum:g} to {maximum:g}.")
    return float(value)


def _integer(section: dict[str, Any], key: str, minimum: int, maximum: int) -> int:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ConfigurationError(f"'{key}' must be an integer from {minimum} to {maximum}.")
    return value


def _is_hostname_or_ip(value: str) -> bool:
    try:
        ip_address(value)
        return True
    except ValueError:
        if len(value) > 253:
            return False
        labels = value.rstrip(".").split(".")
        return bool(labels) and all(
            1 <= len(label) <= 63
            and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
            for label in labels
        )


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate deployment settings, including the service token."""
    config_path = Path(path) if path is not None else configuration_path()
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError as exc:
        raise ConfigurationError(
            f"Configuration file not found: {config_path}. Place config.json beside the sensor."
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Could not read valid JSON configuration at {config_path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ConfigurationError("The top-level configuration value must be an object.")

    backend = _require_object(config, "backend")
    host = backend.get("host")
    if not isinstance(host, str) or not host.strip() or "CHANGE_ME" in host.upper():
        raise ConfigurationError(
            "Set backend.host to the Admin PC IP address or hostname in config.json."
        )
    host = host.strip()
    if any(char.isspace() for char in host) or "://" in host or "/" in host or "\\" in host:
        raise ConfigurationError("backend.host must be a hostname or IP address, without a URL scheme.")
    if not _is_hostname_or_ip(host):
        raise ConfigurationError("backend.host must be a valid hostname or IP address.")
    port = _integer(backend, "port", 1, 65535)

    scheme = backend.get("scheme", "http")
    if scheme not in {"http", "https"}:
        raise ConfigurationError("backend.scheme must be 'http' or 'https'.")
    path_value = backend.get("path")
    if (
        not isinstance(path_value, str)
        or not path_value.startswith("/")
        or path_value.startswith("//")
        or "\\" in path_value
        or ".." in path_value.split("/")
    ):
        raise ConfigurationError("backend.path must be a local absolute path such as /api/ingest.")
    timeout = _number(backend, "timeout_seconds", 1, 120)

    collection = _require_object(config, "collection")
    interval = _number(collection, "interval_seconds", 5, 86400)
    sample_window = _number(collection, "sampling_window_seconds", 1, 300)
    if sample_window > interval:
        raise ConfigurationError(
            "collection.sampling_window_seconds must not exceed interval_seconds."
        )

    endpoint = _require_object(config, "endpoint")
    role = endpoint.get("role")
    if not isinstance(role, str) or role.strip().lower() not in ALLOWED_ROLES:
        raise ConfigurationError(
            "'endpoint.role' must match a supported InSighter role: "
            + ", ".join(sorted(ALLOWED_ROLES))
        )
    category = endpoint.get("category")
    if not isinstance(category, str) or category.strip().lower() not in ALLOWED_CATEGORIES:
        raise ConfigurationError(
            "'endpoint.category' must be a supported InSighter data category: "
            + ", ".join(sorted(ALLOWED_CATEGORIES))
        )
    role = role.strip().lower()
    category = category.strip().lower()
    if (role == "drone_operator") != (category in {"drone_platform_access", "disaster_data"}):
        raise ConfigurationError(
            "The endpoint role and category must belong to the same InSighter sector."
        )

    retry = _require_object(config, "retry")
    retry_initial = _number(retry, "initial_seconds", 1, 3600)
    retry_max = _number(retry, "max_seconds", 1, 3600)
    if retry_max < retry_initial:
        raise ConfigurationError("retry.max_seconds must be at least retry.initial_seconds.")
    queue_max = _integer(retry, "queue_max_events", 1, 100000)

    auth = _require_object(config, "auth")
    token_env = auth.get("token_env")
    if not isinstance(token_env, str) or not token_env.strip():
        raise ConfigurationError("Set auth.token_env to the name of the service-token environment variable.")
    token = os.environ.get(token_env.strip(), "").strip()
    if len(token) < 32:
        raise ConfigurationError(
            f"Set a service token of at least 32 characters in the "
            f"'{token_env.strip()}' environment variable."
        )

    tls = _require_object(config, "tls")
    if tls.get("verify") is not True:
        raise ConfigurationError("TLS certificate verification must remain enabled.")
    if scheme == "http":
        LOGGER.warning(
            "Backend transport is unencrypted HTTP. Use HTTPS or an isolated, trusted demonstration network."
        )

    endpoint_activity = config.get("endpoint_activity", {})
    if not isinstance(endpoint_activity, dict):
        raise ConfigurationError("'endpoint_activity' must be an object.")
    activity_enabled = endpoint_activity.get("enabled", False)
    if not isinstance(activity_enabled, bool):
        raise ConfigurationError("'endpoint_activity.enabled' must be a boolean.")
    activity_poll_interval = _number(
        {
            "poll_interval_seconds": endpoint_activity.get(
                "poll_interval_seconds", 5
            )
        },
        "poll_interval_seconds",
        1,
        60,
    )

    failed_login_monitoring = config.get("failed_login_monitoring", {})
    if not isinstance(failed_login_monitoring, dict):
        raise ConfigurationError("'failed_login_monitoring' must be an object.")
    failed_logins_enabled = failed_login_monitoring.get("enabled", False)
    if not isinstance(failed_logins_enabled, bool):
        raise ConfigurationError("'failed_login_monitoring.enabled' must be a boolean.")

    return {
        "backend": {
            "host": host,
            "port": port,
            "scheme": scheme,
            "path": path_value,
            "timeout_seconds": timeout,
        },
        "collection": {
            "interval_seconds": interval,
            "sampling_window_seconds": sample_window,
        },
        "endpoint": {"role": role, "category": category},
        "retry": {
            "initial_seconds": retry_initial,
            "max_seconds": retry_max,
            "queue_max_events": queue_max,
        },
        "auth": {"token": token},
        "tls": {"verify": True},
        "endpoint_activity": {
            "enabled": activity_enabled,
            "poll_interval_seconds": activity_poll_interval,
        },
        "failed_login_monitoring": {"enabled": failed_logins_enabled},
    }


def setup_logging(directory: Path, frozen: bool = False) -> Path:
    """Write bounded operational logs beside the sensor without logging secrets."""
    log_path = directory / "insighter_sensor.log"
    try:
        handler = RotatingFileHandler(
            log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
    except OSError as exc:
        if frozen:
            raise RuntimeError(f"Could not open sensor log file {log_path}: {exc}") from exc
        logging.basicConfig(level=logging.INFO)
        LOGGER.error("Could not open sensor log file %s: %s", log_path, exc)
        return log_path

    handler.setFormatter(JsonLogFormatter())
    LOGGER.setLevel(logging.INFO)
    LOGGER.addHandler(handler)
    LOGGER.propagate = False
    if not frozen:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        LOGGER.addHandler(console)
    return log_path


def endpoint_identity(role: str, category: str) -> dict[str, str]:
    """Identify the logged-in process account, device, and OS without guessing via psutil.users()."""
    username = getpass.getuser().strip()
    hostname = socket.gethostname().strip() or platform.node().strip()
    if not username:
        raise RuntimeError("Could not determine the current operating-system username.")
    if not hostname:
        raise RuntimeError("Could not determine the endpoint hostname.")
    return {
        "user": f"{hostname}\\{username}",
        "username": username,
        "endpoint_id": hostname,
        "hostname": hostname,
        "os_info": platform.platform(),
        "role": role,
        "category": category,
        "sensor_version": SENSOR_VERSION,
    }


def safe_counter_delta(previous: int | float | None, current: int | float | None) -> tuple[int | float | None, str]:
    """Return a non-negative counter delta and identify unavailable/reset counters."""
    if previous is None or current is None:
        return None, "unsupported"
    if current < previous:
        return 0, "counter_reset"
    return current - previous, "available"


def _network_counters() -> dict[str, int] | None:
    try:
        counters = psutil.net_io_counters(pernic=False)
    except (OSError, psutil.Error):
        return None
    if counters is None:
        return None
    return {"bytes_sent": counters.bytes_sent, "bytes_recv": counters.bytes_recv}


def _disk_counters() -> dict[str, int] | None:
    try:
        counters = psutil.disk_io_counters()
    except (OSError, psutil.Error):
        return None
    if counters is None:
        return None
    return {"read_bytes": counters.read_bytes, "write_bytes": counters.write_bytes}


def collect_event(
    config: dict[str, Any],
    sleep=time.sleep,
    failed_login_monitor: FailedLoginMonitor | None = None,
) -> dict[str, Any]:
    """Sample machine-wide network/disk deltas and build a backend-compatible event."""
    window = config["collection"]["sampling_window_seconds"]
    network_before = _network_counters()
    disk_before = _disk_counters()
    sleep(window)
    network_after = _network_counters()
    disk_after = _disk_counters()

    network_sent, sent_status = safe_counter_delta(
        network_before.get("bytes_sent") if network_before else None,
        network_after.get("bytes_sent") if network_after else None,
    )
    network_received, received_status = safe_counter_delta(
        network_before.get("bytes_recv") if network_before else None,
        network_after.get("bytes_recv") if network_after else None,
    )
    disk_read, disk_read_status = safe_counter_delta(
        disk_before.get("read_bytes") if disk_before else None,
        disk_after.get("read_bytes") if disk_after else None,
    )
    disk_write, disk_write_status = safe_counter_delta(
        disk_before.get("write_bytes") if disk_before else None,
        disk_after.get("write_bytes") if disk_after else None,
    )
    network_available = network_sent is not None and network_received is not None
    total_network_bytes = network_sent + network_received if network_available else None
    failed_login_observation = FailedLoginObservation(None, "disabled")
    if failed_login_monitor is not None:
        try:
            failed_login_observation = failed_login_monitor.observe()
        except Exception:
            LOGGER.exception(
                "Optional failed-login collection failed; other telemetry continues."
            )
            failed_login_observation = FailedLoginObservation(None, "error")
    sampled_at = datetime.now().astimezone()
    login_hour = sampled_at.hour

    event = endpoint_identity(
        config["endpoint"]["role"], config["endpoint"]["category"]
    )
    event.update({
        "event_id": str(uuid.uuid4()),
        "source": "endpoint_sensor",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "login_hour": login_hour,
        "files_accessed": None,
        "mb_transferred": (
            round(total_network_bytes / (1024 * 1024), 6)
            if total_network_bytes is not None else None
        ),
        "failed_logins": failed_login_observation.count,
        "off_hours": int(login_hour < 6 or login_hour > 21),
        "measurement_window_seconds": window,
        "telemetry_status": {
            "network": (
                "machine_wide_delta"
                if network_available and sent_status == received_status == "available"
                else sent_status if sent_status != "available" else received_status
            ),
            "files_accessed": "unsupported_no_file_audit",
            "failed_logins": failed_login_observation.status,
            "disk_io": "machine_wide_delta",
        },
        "feature_status": {
            "login_hour": "supported",
            "files_accessed": "unsupported",
            "data_transferred_mb": (
                "machine_wide"
                if total_network_bytes is not None
                else "unsupported"
            ),
            "failed_logins": (
                "supported"
                if failed_login_observation.status == "available"
                and failed_login_observation.count is not None
                else "unsupported"
            ),
            "off_hours_access": "supported",
        },
        "telemetry_metrics": {
            "network_bytes_sent": network_sent,
            "network_bytes_received": network_received,
            "network_bytes_total": total_network_bytes,
            "network_bytes_per_second": (
                round(total_network_bytes / window, 2)
                if total_network_bytes is not None else None
            ),
            "disk_read_bytes": disk_read,
            "disk_write_bytes": disk_write,
            "disk_read_status": disk_read_status,
            "disk_write_status": disk_write_status,
            "failed_logins_observation_window_seconds": (
                round(failed_login_observation.observation_window_seconds, 2)
                if failed_login_observation.observation_window_seconds is not None
                else None
            ),
        },
    })
    return event


def event_url(config: dict[str, Any]) -> str:
    backend = config["backend"]
    host = backend["host"]
    if ":" in host and not host.startswith("["):
        host = "[" + host + "]"
    return f"{backend['scheme']}://{host}:{backend['port']}{backend['path']}"


def post_event(event: dict[str, Any], config: dict[str, Any], session=None) -> None:
    """Send one event and verify the backend's JSON acknowledgement."""
    client = session or requests
    response = client.post(
        event_url(config),
        json={"events": [event]},
        headers={"Authorization": f"Bearer {config['auth']['token']}"},
        timeout=config["backend"]["timeout_seconds"],
        verify=config["tls"]["verify"],
        allow_redirects=False,
    )
    if 300 <= response.status_code < 400:
        raise PermanentIngestError(
            f"Backend returned an unexpected redirect with HTTP {response.status_code}.",
            response.status_code,
        )
    if response.status_code in {408, 425, 429}:
        raise requests.HTTPError(
            f"Backend temporarily rejected the event with HTTP {response.status_code}.",
            response=response,
        )
    if 400 <= response.status_code < 500:
        raise PermanentIngestError(
            f"Backend rejected the event with HTTP {response.status_code}.",
            response.status_code,
        )
    response.raise_for_status()
    try:
        body = response.json()
    except ValueError as exc:
        raise requests.RequestException("Backend returned a non-JSON ingestion response.") from exc
    if (
        not isinstance(body, dict)
        or body.get("status") != "ok"
        or isinstance(body.get("inserted"), bool)
        or not isinstance(body.get("inserted"), int)
        or body["inserted"] not in (0, 1)
    ):
        raise requests.RequestException("Backend returned an invalid ingestion acknowledgement.")


class EventSender:
    """Bounded in-memory retry queue with exponential backoff."""

    def __init__(self, config: dict[str, Any], session=None):
        self.config = config
        self.session = session
        self.pending: deque[dict[str, Any]] = deque()
        self.next_attempt = 0.0
        self.retry_delay = config["retry"]["initial_seconds"]

    def enqueue(self, event: dict[str, Any]) -> None:
        maximum = self.config["retry"]["queue_max_events"]
        if len(self.pending) >= maximum:
            self.pending.popleft()
            LOGGER.error(
                "Offline event queue reached its %d-event limit; the oldest unsent event was dropped.",
                maximum,
            )
        self.pending.append(event)

    def seconds_until_attempt(self) -> float:
        if not self.pending:
            return float("inf")
        return max(0.0, self.next_attempt - time.monotonic())

    def send_if_due(self) -> bool:
        if not self.pending or time.monotonic() < self.next_attempt:
            return False
        event = self.pending[0]
        try:
            post_event(event, self.config, self.session)
        except PermanentIngestError as exc:
            if exc.status_code in {401, 403}:
                LOGGER.error("%s Check the endpoint token and backend configuration.", exc)
                self._schedule_retry()
            else:
                LOGGER.error("%s Dropping the rejected event id=%s.", exc, event.get("event_id"))
                self.pending.popleft()
                self._reset_retry()
            return False
        except requests.RequestException as exc:
            LOGGER.warning("Backend delivery failed; queued event id=%s: %s", event.get("event_id"), exc)
            self._schedule_retry()
            return False
        except (OSError, ValueError) as exc:
            LOGGER.warning("Backend delivery failed; queued event id=%s: %s", event.get("event_id"), exc)
            self._schedule_retry()
            return False

        self.pending.popleft()
        self._reset_retry()
        LOGGER.info("Delivered telemetry event id=%s; queued=%d.", event.get("event_id"), len(self.pending))
        return True

    def _schedule_retry(self) -> None:
        self.next_attempt = time.monotonic() + self.retry_delay
        self.retry_delay = min(
            self.config["retry"]["max_seconds"],
            self.retry_delay * 2,
        )

    def _reset_retry(self) -> None:
        self.next_attempt = 0.0
        self.retry_delay = self.config["retry"]["initial_seconds"]


def _endpoint_activity_event(
    config: dict[str, Any], activity: dict[str, Any]
) -> dict[str, Any]:
    event = endpoint_identity(
        config["endpoint"]["role"], config["endpoint"]["category"]
    )
    event.update(activity)
    event.update({
        "event_id": str(uuid.uuid4()),
        "source": "endpoint_sensor",
        "timestamp": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z"),
    })
    return event


def _endpoint_activity_worker(
    config: dict[str, Any],
    sensor_stop_event: threading.Event,
    worker_stop_event: threading.Event,
    event_queue: queue.Queue,
) -> None:
    """Poll removable volumes and drain path-free filesystem operation counts."""
    from filesystem_monitor import FilesystemMonitor
    from usb_monitor import discover_removable_volumes, volume_changes

    filesystem = FilesystemMonitor()
    previous_volumes: set[str] = set()
    try:
        filesystem.start()
        poll_interval = config["endpoint_activity"]["poll_interval_seconds"]
        while not sensor_stop_event.is_set() and not worker_stop_event.is_set():
            discovered = discover_removable_volumes()
            if discovered is not None:
                current_volumes = set(discovered)
                for activity in volume_changes(previous_volumes, current_volumes):
                    event_queue.put(_endpoint_activity_event(config, activity))
                pending = filesystem.sync_volumes(current_volumes)
                for activity in pending + filesystem.drain_events():
                    event_queue.put(_endpoint_activity_event(config, activity))
                previous_volumes = current_volumes
            worker_stop_event.wait(poll_interval)
    finally:
        filesystem.stop()


def _drain_endpoint_activity(
    sender: EventSender, event_queue: queue.Queue
) -> None:
    while True:
        try:
            sender.enqueue(event_queue.get_nowait())
        except queue.Empty:
            return


def _check_file_shutdown(stop_event: threading.Event) -> bool:
    """Support graceful stop requests for no-console Windows builds."""
    stop_file = configuration_path().parent / "insighter_sensor.stop"
    if not stop_file.exists():
        return False
    try:
        stop_file.unlink()
    except OSError:
        LOGGER.exception("Could not remove shutdown request file %s.", stop_file)
    LOGGER.info("Graceful shutdown requested through %s.", stop_file.name)
    stop_event.set()
    return True


def run(config: dict[str, Any], stop_event: threading.Event = STOP_EVENT) -> None:
    """Collect at the configured interval and retry queued events while running."""
    sender = EventSender(config)
    interval = config["collection"]["interval_seconds"]
    sample_window = config["collection"]["sampling_window_seconds"]
    activity_queue = queue.Queue()
    activity_stop_event = threading.Event()
    activity_thread = None
    endpoint_activity = config.get("endpoint_activity") or {}
    if endpoint_activity.get("enabled", False):
        activity_thread = threading.Thread(
            target=lambda: _run_endpoint_activity_worker_safely(
                config, stop_event, activity_stop_event, activity_queue
            ),
            name="insighter-endpoint-activity",
            daemon=True,
        )
        activity_thread.start()
    failed_login_settings = config.get("failed_login_monitoring") or {}
    failed_login_monitor = None
    if failed_login_settings.get("enabled", False):
        failed_login_monitor = FailedLoginMonitor(
            enabled=True,
            target_user=getpass.getuser(),
        )
        status = failed_login_monitor.start()
        if status != "available":
            LOGGER.warning(
                "Optional Windows failed-login collection status: %s; other telemetry continues.",
                status,
            )
    LOGGER.info(
        "Sensor started for endpoint=%s role=%s backend=%s.",
        socket.gethostname(),
        config["endpoint"]["role"],
        event_url(config),
    )
    try:
        while not stop_event.is_set():
            _check_file_shutdown(stop_event)
            if stop_event.is_set():
                break
            cycle_started = time.monotonic()
            try:
                sender.enqueue(
                    collect_event(
                        config, failed_login_monitor=failed_login_monitor
                    )
                )
                LOGGER.info(
                    "Collected telemetry event id=%s; pending=%d.",
                    sender.pending[-1].get("event_id"),
                    len(sender.pending),
                )
            except Exception:
                LOGGER.exception("Telemetry collection failed; the next cycle will retry.")

            deadline = cycle_started + interval
            while not stop_event.is_set() and time.monotonic() < deadline:
                _check_file_shutdown(stop_event)
                if stop_event.is_set():
                    break
                _drain_endpoint_activity(sender, activity_queue)
                sender.send_if_due()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                retry_wait = sender.seconds_until_attempt()
                stop_event.wait(min(remaining, retry_wait, 1.0))
            if stop_event.is_set():
                break
            # If collection itself took longer than expected, immediately begin the next cycle.
            if time.monotonic() - cycle_started < sample_window:
                stop_event.wait(max(0.0, sample_window - (time.monotonic() - cycle_started)))
    finally:
        activity_stop_event.set()
        if activity_thread is not None:
            activity_thread.join(timeout=5)
            if activity_thread.is_alive():
                LOGGER.error("Endpoint activity monitor did not stop within five seconds.")
            _drain_endpoint_activity(sender, activity_queue)
        LOGGER.info("Sensor stopped; %d unsent events remain in memory.", len(sender.pending))


def _run_endpoint_activity_worker_safely(
    config: dict[str, Any],
    sensor_stop_event: threading.Event,
    worker_stop_event: threading.Event,
    event_queue: queue.Queue,
) -> None:
    try:
        _endpoint_activity_worker(
            config, sensor_stop_event, worker_stop_event, event_queue
        )
    except Exception:
        LOGGER.exception(
            "USB and filesystem monitoring stopped unexpectedly; existing telemetry continues."
        )


def _request_shutdown(signum, _frame) -> None:
    LOGGER.info("Received shutdown signal %s.", signum)
    STOP_EVENT.set()


def main() -> int:
    base_dir = configuration_path().parent
    setup_logging(base_dir, frozen=bool(getattr(sys, "frozen", False)))
    try:
        config = load_config()
    except ConfigurationError as exc:
        LOGGER.error("Configuration error: %s", exc)
        return 2
    for signum in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if signum is not None:
            try:
                signal.signal(signum, _request_shutdown)
            except (OSError, ValueError):
                LOGGER.debug("Could not register handler for signal %s.", signum)
    try:
        run(config)
    except KeyboardInterrupt:
        STOP_EVENT.set()
        LOGGER.info("Sensor stopped by operator.")
    except Exception:
        LOGGER.exception("Sensor stopped after an unrecoverable error.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
