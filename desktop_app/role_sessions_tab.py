from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QTableWidget,
    QTableWidgetItem, QHeaderView, QLineEdit, QComboBox, QGridLayout
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor


class RoleSessionsTab(QWidget):
    """Compact user risk list with filters and click-through behavior details."""

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.scores = []
        self.filtered_users = []
        self.selected_user = None
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Role-Based User Risk")
        title.setObjectName("h1")
        header.addWidget(title)
        header.addStretch()
        self.count_label = QLabel("")
        self.count_label.setObjectName("muted")
        header.addWidget(self.count_label)
        outer.addLayout(header)

        subtitle = QLabel("Click a risk level to inspect that user's score and activity.")
        subtitle.setObjectName("muted")
        outer.addWidget(subtitle)

        filters = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search users")
        self.search_input.textChanged.connect(self._apply_filters)
        filters.addWidget(self.search_input, stretch=2)

        self.user_filter = QComboBox()
        self.user_filter.addItem("All users", None)
        self.user_filter.currentIndexChanged.connect(self._apply_filters)
        filters.addWidget(self.user_filter)

        self.risk_filter = QComboBox()
        self.risk_filter.addItem("All risk levels", None)
        for level in ("HIGH", "MEDIUM", "LOW"):
            self.risk_filter.addItem(level.title(), level)
        self.risk_filter.currentIndexChanged.connect(self._apply_filters)
        filters.addWidget(self.risk_filter)

        self.sort_order = QComboBox()
        self.sort_order.addItem("Ascending", "ascending")
        self.sort_order.addItem("Descending", "descending")
        self.sort_order.currentIndexChanged.connect(self._apply_filters)
        filters.addWidget(self.sort_order)
        outer.addLayout(filters)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["User", "Risk Level"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.cellClicked.connect(self._on_cell_clicked)
        outer.addWidget(self.table)

        self.details_widget = QFrame()
        self.details_widget.setObjectName("card")
        details_layout = QVBoxLayout(self.details_widget)
        self.details_title = QLabel()
        self.details_title.setObjectName("h2")
        details_layout.addWidget(self.details_title)
        self.details_summary = QLabel()
        self.details_summary.setWordWrap(True)
        self.details_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self.details_summary)
        self.details_widget.hide()
        outer.addWidget(self.details_widget)

    def refresh(self):
        try:
            scores = self.client.get_scores()
            allowed_roles = set(self.client.get_active_taxonomy().get("roles", []))
        except Exception:
            return

        self.scores = [
            user for user in scores
            if (user.get("role", "unknown") or "unknown") in allowed_roles
        ]
        self.scores.sort(key=lambda user: str(user.get("user", "")).casefold())

        selected_user = self.user_filter.currentData()
        self.user_filter.blockSignals(True)
        self.user_filter.clear()
        self.user_filter.addItem("All users", None)
        for username in sorted(
            (str(user.get("user", "")) for user in self.scores),
            key=str.casefold,
        ):
            self.user_filter.addItem(username, username)
        selected_index = self.user_filter.findData(selected_user)
        self.user_filter.setCurrentIndex(max(0, selected_index))
        self.user_filter.blockSignals(False)
        self._apply_filters()

    def _apply_filters(self, *_args):
        query = self.search_input.text().strip().casefold()
        selected_user = self.user_filter.currentData()
        selected_level = self.risk_filter.currentData()
        rows = [
            user for user in self.scores
            if (not query or query in str(user.get("user", "")).casefold())
            and (selected_user is None or user.get("user") == selected_user)
            and (selected_level is None or user.get("risk_level") == selected_level)
        ]
        descending = self.sort_order.currentData() == "descending"
        rows.sort(
            key=lambda user: (float(user.get("risk_score", 0)), str(user.get("user", "")).casefold()),
            reverse=descending,
        )

        self.filtered_users = rows
        self.count_label.setText(f"{len(rows)} user" + ("s" if len(rows) != 1 else ""))
        self.table.setRowCount(len(rows))
        risk_colors = {"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#10b981"}
        for row_index, user in enumerate(rows):
            username_item = QTableWidgetItem(str(user.get("user", "")))
            level = str(user.get("risk_level", "UNKNOWN"))
            level_item = QTableWidgetItem(level)
            level_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            level_item.setToolTip("Click to view risk score and behavioral details")
            if level in risk_colors:
                level_item.setForeground(QColor(risk_colors[level]))
            self.table.setItem(row_index, 0, username_item)
            self.table.setItem(row_index, 1, level_item)

        if self.selected_user:
            selected = next(
                (user for user in rows if user.get("user") == self.selected_user),
                None,
            )
            if selected:
                self._show_details(selected)
            else:
                self.selected_user = None
                self.details_widget.hide()

    def _on_cell_clicked(self, row, column):
        if column == 1 and 0 <= row < len(self.filtered_users):
            self._show_details(self.filtered_users[row])

    def _show_details(self, user):
        self.selected_user = user.get("user")
        username = str(user.get("user", "Unknown user"))
        level = str(user.get("risk_level", "UNKNOWN"))
        role = str(user.get("role", "unknown")).replace("_", " ").title()
        self.details_title.setText(f"{username} · {level}")
        self.details_summary.setText(
            f"Risk score: {user.get('risk_score', 0)}%\n"
            f"Role: {role}\n"
            f"Role baseline deviation: {user.get('role_baseline_score', 0)}\n"
            f"Avg files accessed: {user.get('avg_files', 0)} · "
            f"Avg transfer: {user.get('avg_transfer', 0)} MB · "
            f"Avg failed logins: {user.get('avg_failed_logins', 0)}\n"
            f"Off-hours events: {user.get('off_hours', 0)} · "
            f"Isolation Forest: {user.get('if_score', 0)} · "
            f"One-Class SVM: {user.get('ocsvm_score', 0)}"
        )
        self.details_widget.show()
