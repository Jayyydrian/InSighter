from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import Qt

from desktop_app.async_utils import run_async
from desktop_app.safe_text import plain_label

RISK_COLORS = {"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#22c55e"}
HISTORY_COLUMNS = (
    ("Timestamp (UTC)", "timestamp"),
    ("Accessed", "accessed"),
    ("Login hour", "login_hour"),
    ("Files", "files_accessed"),
    ("Data (MB)", "data_transferred_mb"),
    ("Failed logins", "failed_logins"),
    ("Off-hours", "off_hours_access"),
    ("Source", "source"),
)


class ProfilesTab(QWidget):
    """Show each user's current risk score and recent recorded activity."""

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.users = []
        self._selected_username = None
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        left = QVBoxLayout()
        title = QLabel("Personal History")
        title.setObjectName("h1")
        left.addWidget(title)

        self.user_list = QListWidget()
        self.user_list.setFixedWidth(220)
        self.user_list.currentRowChanged.connect(self._on_select)
        left.addWidget(self.user_list)

        left_wrap = QWidget()
        left_wrap.setLayout(left)
        left_wrap.setFixedWidth(240)
        layout.addWidget(left_wrap)

        self.detail_card = QFrame()
        self.detail_card.setObjectName("card")
        self.detail_layout = QVBoxLayout(self.detail_card)
        self.detail_layout.setContentsMargins(24, 24, 24, 24)

        self.detail_status = plain_label("Select a user to view recent activity.")
        self.detail_status.setObjectName("muted")
        self.detail_layout.addWidget(self.detail_status)
        layout.addWidget(self.detail_card, stretch=1)

    def refresh(self):
        run_async(
            self.client.get_scores,
            self._apply_refresh,
            self._show_refresh_error,
            parent=self,
            key="refresh",
        )

    def _apply_refresh(self, users):
        self.users = users
        selected_username = self._selected_username
        self.user_list.blockSignals(True)
        self.user_list.clear()
        for user in users:
            self.user_list.addItem(QListWidgetItem(user["user"]))

        selected_row = next(
            (
                index for index, user in enumerate(users)
                if user["user"] == selected_username
            ),
            0 if users else -1,
        )
        self.user_list.setCurrentRow(selected_row)
        self.user_list.blockSignals(False)

        if selected_row >= 0:
            user = users[selected_row]
            if (
                user["user"] == self._selected_username
                and hasattr(self, "history_table")
            ):
                self._update_current_risk(user)
                self._load_history(user["user"])
            else:
                self._on_select(selected_row)
        else:
            self._selected_username = None
            self._show_message("No users are currently available.")

    def _on_select(self, row):
        if row < 0 or row >= len(self.users):
            self._selected_username = None
            self._show_message("Select a user to view recent activity.")
            return

        user = self.users[row]
        username = user["user"]
        self._selected_username = username
        self._clear_layout()

        risk_level = str(user.get("risk_level", "UNKNOWN")).upper()
        risk_color = RISK_COLORS.get(risk_level, "#f5f5f5")
        header = QHBoxLayout()
        name = plain_label(username)
        name.setObjectName("h1")
        role = plain_label(str(user.get("role", "unknown")).replace("_", " ").title())
        role.setObjectName("muted")
        risk = plain_label(
            f"Current risk: {user.get('risk_score', 0)}% · {risk_level}"
        )
        risk.setStyleSheet(f"color:{risk_color}; font-weight:700;")
        self.risk_label = risk
        header.addWidget(name)
        header.addWidget(role)
        header.addStretch()
        header.addWidget(risk)
        self.detail_layout.addLayout(header)

        title = QLabel("RECENT ACTIVITY")
        title.setObjectName("h2")
        self.detail_layout.addWidget(title)
        note = plain_label(
            "Latest 100 recorded events · timestamps are stored in UTC."
        )
        note.setObjectName("muted")
        self.detail_layout.addWidget(note)

        self.history_table = QTableWidget(0, len(HISTORY_COLUMNS))
        self.history_table.setHorizontalHeaderLabels(
            [title for title, _key in HISTORY_COLUMNS]
        )
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.history_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.history_table.setAlternatingRowColors(True)
        self.history_table.verticalHeader().setVisible(False)
        table_header = self.history_table.horizontalHeader()
        table_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.detail_layout.addWidget(self.history_table, stretch=1)

        self.detail_status = plain_label("Loading recent activity…")
        self.detail_status.setObjectName("muted")
        self.detail_layout.addWidget(self.detail_status)

        self._load_history(username)

    def _update_current_risk(self, user):
        risk_level = str(user.get("risk_level", "UNKNOWN")).upper()
        risk_color = RISK_COLORS.get(risk_level, "#f5f5f5")
        self.risk_label.setText(
            f"Current risk: {user.get('risk_score', 0)}% · {risk_level}"
        )
        self.risk_label.setStyleSheet(f"color:{risk_color}; font-weight:700;")

    def _load_history(self, username):
        run_async(
            lambda: self.client.get_user_history(username),
            lambda history, selected=username: self._apply_history(selected, history),
            lambda error, selected=username: self._show_history_error(selected, error),
            parent=self,
            key=f"history:{username}",
        )

    def _apply_history(self, username, history):
        if username != self._selected_username:
            return
        scroll_position = self.history_table.verticalScrollBar().value()
        self.history_table.setUpdatesEnabled(False)
        self.history_table.setRowCount(len(history))
        for row, event in enumerate(history):
            for column, (_title, key) in enumerate(HISTORY_COLUMNS):
                value = event.get(key)
                if key == "data_transferred_mb":
                    text = self._format_number(value, 1)
                elif key in ("files_accessed", "failed_logins", "off_hours_access", "login_hour"):
                    text = self._format_number(value, 0)
                else:
                    text = str(value) if value not in (None, "") else "—"
                item = QTableWidgetItem(text)
                if column not in (0, 1, 6):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.history_table.setItem(row, column, item)
            self.history_table.setRowHeight(row, 34)
        self.history_table.setUpdatesEnabled(True)
        self.history_table.verticalScrollBar().setValue(scroll_position)

        self.detail_status.setText(
            f"{len(history)} recent event{'s' if len(history) != 1 else ''}"
            if history else "No recorded activity for this user."
        )

    def _show_history_error(self, username, error):
        if username != self._selected_username:
            return
        self.history_table.setRowCount(0)
        self.detail_status.setText(
            f"Could not load activity history: "
            f"{getattr(error, 'message', str(error))}"
        )

    def _show_refresh_error(self, error):
        self._show_message(
            f"Could not load users: {getattr(error, 'message', str(error))}"
        )

    def _show_message(self, message):
        self._clear_layout()
        self.detail_status = plain_label(message)
        self.detail_status.setObjectName("muted")
        self.detail_layout.addWidget(self.detail_status)

    def _clear_layout(self):
        self._clear_layout_items(self.detail_layout)

    @classmethod
    def _clear_layout_items(cls, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                cls._clear_layout_items(item.layout())
                item.layout().deleteLater()

    @staticmethod
    def _format_number(value, precision):
        try:
            return f"{float(value):.{precision}f}"
        except (TypeError, ValueError):
            return "—"
