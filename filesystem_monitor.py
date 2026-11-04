"""Count file create/modify/delete/rename activity on monitored USB volumes."""

from __future__ import annotations

from collections import Counter
import logging
from threading import Lock

LOGGER = logging.getLogger("insighter.sensor.filesystem")
OPERATIONS = {
    "created": "create",
    "modified": "modify",
    "deleted": "delete",
    "moved": "rename",
}


class FileActivityHandler:
    """Watchdog-compatible handler that retains operation counts, never paths."""

    def __init__(self, drive_letter: str):
        self.drive_letter = drive_letter
        self._counts: Counter[str] = Counter()
        self._lock = Lock()

    def dispatch(self, event) -> None:
        if getattr(event, "is_directory", True):
            return
        operation = OPERATIONS.get(getattr(event, "event_type", ""))
        if operation is None:
            return
        with self._lock:
            self._counts[operation] += 1

    def drain(self) -> dict[str, int]:
        with self._lock:
            counts = dict(self._counts)
            self._counts.clear()
        return counts


class FilesystemMonitor:
    """Manage recursive file-system watches on the currently mounted USB volumes."""

    def __init__(self):
        self._observer = None
        self._handlers: dict[str, FileActivityHandler] = {}
        self._watches: dict[str, object] = {}

    def start(self) -> None:
        if self._observer is not None:
            raise RuntimeError("Filesystem monitor is already running.")
        from watchdog.observers import Observer

        self._observer = Observer()
        self._observer.start()

    def sync_volumes(self, volumes: set[str]) -> list[dict[str, object]]:
        if self._observer is None:
            raise RuntimeError("Filesystem monitor must be started before adding volumes.")
        pending_events = self.drain_events()
        for root in sorted(volumes - self._handlers.keys(), key=str.casefold):
            handler = FileActivityHandler(root)
            try:
                watch = self._observer.schedule(handler, root, recursive=True)
            except (OSError, RuntimeError):
                LOGGER.exception("Could not watch removable volume %s.", root)
                continue
            self._handlers[root] = handler
            self._watches[root] = watch

        for root in sorted(self._handlers.keys() - volumes, key=str.casefold):
            watch = self._watches.pop(root)
            self._handlers.pop(root)
            try:
                self._observer.unschedule(watch)
            except (KeyError, OSError, RuntimeError):
                LOGGER.exception("Could not stop watching removed volume %s.", root)
        return pending_events

    def drain_events(self) -> list[dict[str, object]]:
        events = []
        for root, handler in tuple(self._handlers.items()):
            counts = handler.drain()
            if counts:
                events.append({
                    "event_type": "filesystem_activity",
                    "drive_letter": root,
                    "operation_counts": counts,
                })
        return events

    def stop(self) -> None:
        observer = self._observer
        self._observer = None
        if observer is None:
            return
        observer.stop()
        observer.join(timeout=5)
        if observer.is_alive():
            LOGGER.error("Filesystem observer did not stop within five seconds.")
