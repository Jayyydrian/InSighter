from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QTableWidget,
    QTableWidgetItem, QHeaderView, QLineEdit, QComboBox, QGridLayout
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from desktop_app.async_utils import run_async
from desktop_app.safe_text import plain_label


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
        self.details_title = plain_label()
        self.details_title.setObjectName("h2")
        details_layout.addWidget(self.details_title)
        self.details_summary = plain_label()
        self.details_summary.setWordWrap(True)
        self.details_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self.details_summary)
        self.details_role = plain_label()
        self.details_role.setStyleSheet(
            "color:#93c5fd; background:rgba(59,130,246,28); "
            "border:1px solid #3b82f6; border-radius:4px; "
            "padding:3px 8px; font-weight:700;"
        )
        details_layout.addWidget(self.details_role, alignment=Qt.AlignmentFlag.AlignLeft)
        self.details_analysis = plain_label()
        self.details_analysis.setWordWrap(True)
        self.details_analysis.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        details_layout.addWidget(self.details_analysis)
        self.details_behavior_note = plain_label()
        self.details_behavior_note.setObjectName("muted")
        self.details_behavior_note.setWordWrap(True)
        details_layout.addWidget(self.details_behavior_note)
        self.details_behavior = QGridLayout()
        self.details_behavior_values = {}
        for row, (label, feature) in enumerate((
            ("Files accessed", "files_accessed"),
            ("Data transferred", "data_transferred_mb"),
            ("Unsuccessful sign-ins", "failed_logins"),
            ("Activity outside usual hours", "off_hours_access"),
        ), start=1):
            feature_label = plain_label(label)
            feature_label.setToolTip({
                "files_accessed": "Average number of files accessed in each recorded activity entry.",
                "data_transferred_mb": "Average data transferred in each recorded activity entry. 1,024 MB is about 1 GB.",
                "failed_logins": "Average number of unsuccessful sign-in attempts in each recorded activity entry.",
                "off_hours_access": "Percentage of recorded activity entries that happened outside usual working hours.",
            }[feature])
            self.details_behavior.addWidget(feature_label, row, 0)
            baseline_value = plain_label()
            user_value = plain_label()
            self.details_behavior.addWidget(baseline_value, row, 1)
            self.details_behavior.addWidget(user_value, row, 2)
            self.details_behavior_values[feature] = {
                "baseline": baseline_value,
                "user": user_value,
            }
        self.details_behavior.addWidget(plain_label("Behavior"), 0, 0)
        self.details_behavior.addWidget(plain_label("Typical for role"), 0, 1)
        self.details_behavior.addWidget(plain_label("This user (average)"), 0, 2)
        details_layout.addLayout(self.details_behavior)
        self.details_widget.hide()
        outer.addWidget(self.details_widget)

    def refresh(self):
        def fetch():
            scores = self.client.get_scores()
            allowed_roles = set(self.client.get_active_taxonomy().get("roles", []))
            return scores, allowed_roles

        run_async(
            fetch,
            self._apply_refresh,
            parent=self,
            key="refresh",
        )

    def _apply_refresh(self, result):
        scores, allowed_roles = result
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
        rows.sort(key=lambda user: str(user.get("user", "")).casefold())
        rows.sort(
            key=lambda user: float(user.get("risk_score", 0)),
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
            f"Overall risk score: {self._format_score(user.get('risk_score'))} / 100\n"
            "This summarizes how unusual the recorded activity looks; it is not a percentage chance of a problem."
        )
        self.details_role.setText(f"Role: {role}")
        self.details_analysis.setText(
            f"Difference from this role's usual pattern: "
            f"{self._format_score(user.get('role_baseline_score'))} / 100 "
            "(higher means more different; this is not a percentage).\n"
            f"Isolation Forest: {self._format_score(user.get('if_score'))} · "
            f"One-Class SVM: {self._format_score(user.get('ocsvm_score'))}"
        )
        baseline, estimated = self._role_reference(user)
        behavior = {
            "files_accessed": user.get("avg_files"),
            "data_transferred_mb": user.get("avg_transfer"),
            "failed_logins": user.get("avg_failed_logins"),
            "off_hours_access": (
                float(user["off_hours"]) / float(user["total_logs"])
                if user.get("total_logs")
                else None
            ),
        }
        self.details_behavior_note.setText(
            "Each number is an average per recorded activity entry, not a total. "
            "The role value is the typical level for this role; the user value is "
            "this person's average. Data is shown in megabytes (MB)."
            + (
                " The role reference is estimated from other monitored users in this "
                "role because a baseline was not supplied."
                if estimated else ""
            )
            + (
                " A peer comparison is unavailable because no role baseline or other "
                "monitored user in this role was supplied."
                if not estimated and not any(
                    isinstance(value, (int, float))
                    for value in baseline.values()
                ) else ""
            )
        )
        for feature, values in self.details_behavior_values.items():
            values["baseline"].setText(self._format_behavior_value(feature, baseline.get(feature)))
            values["user"].setText(self._format_behavior_value(feature, behavior[feature]))
        self.details_widget.show()

    @staticmethod
    def _format_behavior_value(feature, value):
        if not isinstance(value, (int, float)):
            return "Not available"
        value = float(value)
        if feature == "off_hours_access":
            return f"{value * 100:.1f}% of events"
        units = {
            "files_accessed": "files per event",
            "data_transferred_mb": "MB per event",
            "failed_logins": "attempts per event",
        }
        return f"{value:.1f} {units[feature]}"

    @staticmethod
    def _format_score(value):
        if not isinstance(value, (int, float)):
            return "Not available"
        return f"{float(value):.1f}"

    def _role_reference(self, user):
        supplied = user.get("role_baseline")
        reference = dict(supplied) if isinstance(supplied, dict) else {}
        estimated = False
        role = user.get("role")
        peers = [
            score for score in self.scores
            if score.get("role") == role and score.get("user") != user.get("user")
        ]
        for feature, score_key in (
            ("files_accessed", "avg_files"),
            ("data_transferred_mb", "avg_transfer"),
            ("failed_logins", "avg_failed_logins"),
        ):
            if not isinstance(reference.get(feature), (int, float)):
                values = [
                    float(score[score_key])
                    for score in peers
                    if isinstance(score.get(score_key), (int, float))
                ]
                if values:
                    reference[feature] = sum(values) / len(values)
                    estimated = True

        if not isinstance(reference.get("off_hours_access"), (int, float)):
            values = [
                float(score["off_hours"]) / float(score["total_logs"])
                for score in peers
                if isinstance(score.get("off_hours"), (int, float))
                and isinstance(score.get("total_logs"), (int, float))
                and float(score["total_logs"]) > 0
            ]
            if values:
                reference["off_hours_access"] = sum(values) / len(values)
                estimated = True
        return reference, estimated
