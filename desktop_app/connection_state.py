"""Connection and authenticated-session state for one desktop login."""

import logging
import time

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot

from desktop_app.api_client import ApiError

logger = logging.getLogger(__name__)

CONNECTED = "CONNECTED"
OFFLINE = "OFFLINE"


class ConnectionMonitor(QObject):
    state_changed = pyqtSignal(str, str)
    session_expired = pyqtSignal()
    refreshed = pyqtSignal()
    _success_requested = pyqtSignal(str)
    _failure_requested = pyqtSignal(object, bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = CONNECTED
        self.last_message = ""
        self.consecutive_failures = 0
        self._expired = False
        self._source_log_state = {}
        self._success_requested.connect(
            self._record_success, Qt.ConnectionType.QueuedConnection
        )
        self._failure_requested.connect(
            self._record_failure, Qt.ConnectionType.QueuedConnection
        )

    def record_success(self, source="refresh"):
        if QThread.currentThread() is not self.thread():
            self._success_requested.emit(source)
            return
        self._record_success(source)

    def record_failure(self, exc, counts_toward_offline=True, source="refresh"):
        if QThread.currentThread() is not self.thread():
            self._failure_requested.emit(exc, counts_toward_offline, source)
            return
        self._record_failure(exc, counts_toward_offline, source)

    @pyqtSlot(str)
    def _record_success(self, source):
        self.refreshed.emit()
        previous_failures = self.consecutive_failures
        self.consecutive_failures = 0
        if self.state == OFFLINE:
            self.state = CONNECTED
            self.last_message = ""
            self.state_changed.emit(self.state, "")
        source_state = self._source_log_state.pop(source, None)
        if previous_failures or source_state:
            logger.info("Connection recovered after refresh failure (%s).", source)

    @pyqtSlot(object, bool, str)
    def _record_failure(self, exc, counts_toward_offline, source):
        if isinstance(exc, ApiError) and exc.status_code == 401:
            if not self._expired:
                self._expired = True
                self.session_expired.emit()
            return
        if isinstance(exc, ApiError) and exc.is_unexpected_response:
            return

        status_code = getattr(exc, "status_code", None)
        offline_failure = counts_toward_offline and isinstance(exc, ApiError) and (
            status_code is None or (isinstance(status_code, int) and status_code >= 500)
        )
        message = str(getattr(exc, "message", None) or exc)
        self._rate_limited_warning(source, message)

        if not offline_failure:
            return
        self.consecutive_failures += 1
        self.last_message = message
        if self.consecutive_failures >= 2 and self.state != OFFLINE:
            self.state = OFFLINE
            self.state_changed.emit(self.state, self.last_message)

    def _rate_limited_warning(self, source, message):
        now = time.monotonic()
        previous = self._source_log_state.get(source)
        if previous is None or now - previous >= 60:
            logger.warning("Refresh failed in %s: %s", source, message)
            self._source_log_state[source] = now
