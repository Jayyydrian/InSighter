"""Small Qt worker helper for running blocking operations off the UI thread."""

import logging

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QObject, QThread, Qt, pyqtSignal, pyqtSlot


_workers = {}
_active_keys = set()
_shutdown_connected = False
logger = logging.getLogger(__name__)


class _Worker(QThread):
    completed = pyqtSignal(object, object)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.completed.emit(self.fn(), None)
        except Exception as exc:
            self.completed.emit(None, exc)


class _ResultBridge(QObject):
    def __init__(
        self, parent, on_success, on_error, monitor, monitor_source,
        counts_toward_offline, report_success,
    ):
        super().__init__()
        self.parent_widget = parent
        self.parent_id = id(parent) if parent is not None else None
        self.on_success = on_success
        self.on_error = on_error
        self.monitor = monitor
        self.monitor_source = monitor_source
        self.counts_toward_offline = counts_toward_offline
        self.report_success = report_success
        self.key = None
        self.task = None

    @pyqtSlot(object, object)
    def deliver(self, result, error):
        self._release_key()
        if self.monitor is not None:
            if error is not None:
                self.monitor.record_failure(
                    error, self.counts_toward_offline, self.monitor_source
                )
            elif self.report_success:
                self.monitor.record_success(self.monitor_source)
        elif error is not None:
            logger.warning("Background request failed in %s: %s", self.monitor_source, error)
        if self.parent_widget is not None and sip.isdeleted(self.parent_widget):
            return
        if error is None:
            self.on_success(result)
        elif self.on_error is not None:
            self.on_error(error)

    @pyqtSlot()
    def cleanup(self):
        _workers.pop(self.task, None)
        self._release_key()

    def _release_key(self):
        if self.key is not None:
            _active_keys.discard((self.parent_id, self.key))
            self.key = None


def _wait_for_workers():
    for worker in list(_workers):
        if worker.isRunning():
            worker.wait()


def run_async(
    fn, on_success, on_error=None, parent=None, key=None, monitor_client=None,
    monitor_source=None, counts_toward_offline=True, report_success=True,
):
    """Run ``fn`` in a QThread and deliver callbacks on the Qt UI thread.

    Supplying the same ``key`` for a parent suppresses duplicate in-flight
    work, which is useful for timer-driven refreshes.
    """
    global _shutdown_connected

    active_key = (id(parent), key) if parent is not None and key is not None else None
    if active_key is not None and active_key in _active_keys:
        return None

    app = QCoreApplication.instance()
    if app is not None and not _shutdown_connected:
        app.aboutToQuit.connect(_wait_for_workers)
        _shutdown_connected = True

    if monitor_client is None and parent is not None:
        monitor_client = getattr(parent, "client", None)
    if monitor_source is None:
        monitor_source = type(parent).__name__ if parent is not None else "request"

    worker = _Worker(fn)
    bridge = _ResultBridge(
        parent,
        on_success,
        on_error,
        getattr(monitor_client, "connection_monitor", None),
        monitor_source,
        counts_toward_offline,
        report_success,
    )
    bridge.key = key
    bridge.task = worker
    worker.completed.connect(bridge.deliver, Qt.ConnectionType.QueuedConnection)
    worker.finished.connect(bridge.cleanup, Qt.ConnectionType.QueuedConnection)
    _workers[worker] = bridge
    if active_key is not None:
        _active_keys.add(active_key)
    worker.start()
    return worker
