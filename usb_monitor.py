"""Detect mounted Windows removable volumes without collecting device serials."""

from __future__ import annotations

import ctypes
import logging
import os

import psutil

LOGGER = logging.getLogger("insighter.sensor.usb")
DRIVE_REMOVABLE = 2


def _is_removable_drive(root: str) -> bool:
    if os.name != "nt":
        return False
    try:
        return ctypes.windll.kernel32.GetDriveTypeW(root) == DRIVE_REMOVABLE
    except (AttributeError, OSError):
        LOGGER.exception("Could not determine whether volume %s is removable.", root)
        return False


def discover_removable_volumes(partitions=None, is_removable=None) -> list[str] | None:
    """Return mounted volumes Windows classifies as removable drives."""
    try:
        mounted = psutil.disk_partitions(all=False) if partitions is None else partitions
    except (OSError, psutil.Error):
        LOGGER.exception("Could not enumerate mounted volumes.")
        return None

    detector = _is_removable_drive if is_removable is None else is_removable
    volumes = set()
    for partition in mounted:
        root = getattr(partition, "mountpoint", "")
        if root and detector(root):
            volumes.add(root)
    return sorted(volumes, key=str.casefold)


def volume_changes(previous: set[str], current: set[str]) -> list[dict[str, str]]:
    """Describe newly mounted and removed volumes, including an initial snapshot."""
    events = [
        {"event_type": "usb_connected", "drive_letter": root}
        for root in sorted(current - previous, key=str.casefold)
    ]
    events.extend(
        {"event_type": "usb_disconnected", "drive_letter": root}
        for root in sorted(previous - current, key=str.casefold)
    )
    return events
