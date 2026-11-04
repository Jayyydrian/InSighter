"""Optional, read-only Windows Security log collector for failed logons."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import io
import json
import logging
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time
from typing import Callable

LOGGER = logging.getLogger("insighter.sensor.failed_login")
LOGON_AUDIT_SUBCATEGORY = "{0CCE9215-69AE-11D9-BED3-505054503030}"
POWERSHELL_TIMEOUT_SECONDS = 10

_LATEST_RECORD_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$event = Get-WinEvent -LogName Security -MaxEvents 1 -ErrorAction Stop
[Console]::Out.Write([string]$event.RecordId)
"""

_NEW_FAILURES_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$cursor = __CURSOR__
$latest = Get-WinEvent -LogName Security -MaxEvents 1 -ErrorAction Stop
$latestId = [long]$latest.RecordId
$records = @()
if ($latestId -gt $cursor) {
    try {
        $events = @(Get-WinEvent -LogName Security -FilterXPath `
            "*[System[(EventID=4625) and (EventRecordID > $cursor) and (EventRecordID <= $latestId)]]" `
            -ErrorAction Stop)
    } catch {
        if ($_.FullyQualifiedErrorId -notlike '*NoMatchingEventsFound*') { throw }
        $events = @()
    }
    foreach ($event in $events) {
        $document = [xml]$event.ToXml()
        $targetNode = $document.SelectSingleNode(
            "//*[local-name()='Data' and @Name='TargetUserName']"
        )
        $targetUser = if ($targetNode) { [string]$targetNode.InnerText } else { '' }
        $records += [pscustomobject]@{
            record_id = [long]$event.RecordId
            target_user = $targetUser
        }
    }
}
$result = [pscustomobject]@{
    latest_record_id = $latestId
    events = @($records)
}
ConvertTo-Json -InputObject $result -Depth 3 -Compress
"""


@dataclass(frozen=True)
class FailedLoginObservation:
    count: int | None
    status: str
    observation_window_seconds: float | None = None


def _powershell_command(script: str) -> list[str]:
    return [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        script,
    ]


def _run_command(command: list[str], timeout: int = POWERSHELL_TIMEOUT_SECONDS):
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _audit_failure_enabled(output: str) -> bool | None:
    """Return whether Logon failure auditing is enabled in auditpol CSV output."""
    for row in csv.reader(io.StringIO(output)):
        if any(LOGON_AUDIT_SUBCATEGORY.casefold() == value.strip().casefold() for value in row):
            setting = row[-1].strip().casefold() if row else ""
            if "no auditing" in setting:
                return False
            if "failure" in setting:
                return True
            if setting:
                return False
    return None


def _account_name_matches(target_user: str, process_user: str) -> bool:
    target = target_user.strip().casefold()
    process = process_user.strip().casefold()
    if not target or not process:
        return False
    process_names = {process, process.rsplit("\\", 1)[-1], process.rsplit("@", 1)[0]}
    return target in process_names


class FailedLoginMonitor:
    """Count Event ID 4625 records for the sensor's process account only.

    The collector never changes Windows audit policy and does not retain event
    XML, source addresses, logon details, or authentication secrets.
    """

    def __init__(
        self,
        enabled: bool,
        target_user: str,
        command_runner: Callable = _run_command,
        monotonic: Callable[[], float] = time.monotonic,
        windows: bool | None = None,
    ):
        self.enabled = enabled
        self.target_user = target_user
        self.command_runner = command_runner
        self.monotonic = monotonic
        self.windows = os.name == "nt" if windows is None else windows
        self.status = "disabled" if not enabled else "not_configured"
        self.last_record_id: int | None = None
        self.last_observed_at: float | None = None

    def _set_status(self, status: str) -> None:
        if status != self.status:
            if status == "available":
                LOGGER.info("Optional Windows failed-login collection is available.")
            else:
                LOGGER.warning(
                    "Optional Windows failed-login collection status: %s.",
                    status,
                )
        self.status = status

    def _auditpol_path(self) -> str | None:
        located = shutil.which("auditpol.exe")
        if located:
            return located
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        candidate = str(Path(system_root) / "System32" / "auditpol.exe")
        return candidate if Path(candidate).is_file() else None

    def _check_audit_policy(self) -> str:
        executable = self._auditpol_path()
        if executable is None:
            return "not_configured"
        try:
            result = self.command_runner(
                [executable, "/get", "/subcategory:" + LOGON_AUDIT_SUBCATEGORY, "/r"]
            )
        except (OSError, subprocess.TimeoutExpired):
            return "unavailable"
        if result.returncode != 0:
            return "unavailable"
        enabled = _audit_failure_enabled(result.stdout)
        if enabled is None or not enabled:
            return "not_configured"
        return "available"

    def _read_latest_record_id(self) -> int:
        result = self.command_runner(_powershell_command(_LATEST_RECORD_SCRIPT))
        if result.returncode != 0:
            raise OSError("Security event log query failed.")
        return int(result.stdout.strip())

    def start(self) -> str:
        """Check configuration/access and set a cursor without replaying old events."""
        if not self.enabled:
            self._set_status("disabled")
            return self.status
        if not self.windows:
            self._set_status("unavailable")
            return self.status

        self._set_status(self._check_audit_policy())
        if self.status != "available":
            return self.status
        try:
            self.last_record_id = self._read_latest_record_id()
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self._set_status("unavailable")
            return self.status
        self.last_observed_at = self.monotonic()
        return self.status

    def _read_new_events(self, cursor: int) -> tuple[int, list[dict]]:
        script = _NEW_FAILURES_SCRIPT.replace("__CURSOR__", str(cursor))
        result = self.command_runner(_powershell_command(script))
        if result.returncode != 0:
            raise OSError("Security event log query failed.")
        try:
            payload = json.loads(result.stdout)
            latest_record_id = payload["latest_record_id"]
            events = payload["events"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError("Security event log response was malformed.") from exc
        if (
            isinstance(latest_record_id, bool)
            or not isinstance(latest_record_id, int)
            or latest_record_id < 0
            or not isinstance(events, list)
        ):
            raise ValueError("Security event log response was malformed.")
        for event in events:
            if (
                not isinstance(event, dict)
                or isinstance(event.get("record_id"), bool)
                or not isinstance(event.get("record_id"), int)
                or not isinstance(event.get("target_user"), str)
            ):
                raise ValueError("Security event log response was malformed.")
        return latest_record_id, events

    def observe(self) -> FailedLoginObservation:
        """Read new failures; failures return NULL status and do not stop the sensor."""
        if not self.enabled:
            self._set_status("disabled")
            return FailedLoginObservation(None, self.status)
        if not self.windows:
            self._set_status("unavailable")
            return FailedLoginObservation(None, self.status)
        if self.last_record_id is None:
            status = self.start()
            return FailedLoginObservation(
                None, "error" if status == "available" else status
            )

        observed_at = self.monotonic()
        window = (
            max(0.0, observed_at - self.last_observed_at)
            if self.last_observed_at is not None else None
        )
        try:
            latest_record_id, events = self._read_new_events(self.last_record_id)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self._set_status("error")
            return FailedLoginObservation(None, self.status, window)

        if latest_record_id < self.last_record_id:
            self.last_record_id = latest_record_id
            self.last_observed_at = observed_at
            self._set_status("error")
            return FailedLoginObservation(None, self.status, window)

        self.last_record_id = latest_record_id
        self.last_observed_at = observed_at
        count = sum(
            1 for event in events
            if event["record_id"] <= latest_record_id
            and _account_name_matches(event["target_user"], self.target_user)
        )
        self._set_status("available")
        return FailedLoginObservation(count, self.status, window)
