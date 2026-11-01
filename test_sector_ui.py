import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from desktop_app.dashboard_tab import DashboardTab
from desktop_app.role_sessions_tab import RoleSessionsTab


def wait_for(app, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


class SectorViewClient:
    def get_scores(self):
        return [
            {"user": "researcher_01", "role": "researcher", "risk_score": 10, "risk_level": "LOW", "role_baseline_score": 1, "role_baseline": {"files_accessed": 4, "data_transferred_mb": 10, "failed_logins": 0.2, "off_hours_access": 0.1}, "avg_files": 2, "avg_transfer": 3, "avg_failed_logins": 0.1, "off_hours": 0, "total_logs": 10, "if_score": 8, "ocsvm_score": 9},
            {"user": "drone_operator_01", "role": "drone_operator", "risk_score": 70, "risk_level": "HIGH", "role_baseline_score": 20, "role_baseline": {"files_accessed": 12, "data_transferred_mb": 35, "failed_logins": 0.5, "off_hours_access": 0.2}, "avg_files": 50, "avg_transfer": 100, "avg_failed_logins": 3, "off_hours": 3, "total_logs": 10, "if_score": 60, "ocsvm_score": 65},
        ]

    def get_active_taxonomy(self):
        return {"sector": "uav_disaster_response", "roles": ["drone_operator"]}


class SectorViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_role_behavior_view_omits_stale_sector_roles(self):
        tab = RoleSessionsTab(SectorViewClient())
        tab.refresh()

        self.assertTrue(self._wait_for_role_view(tab))
        self.assertEqual(tab.table.columnCount(), 2)
        self.assertEqual(
            [tab.table.horizontalHeaderItem(column).text() for column in range(2)],
            ["User", "Risk Level"],
        )
        self.assertEqual(tab.table.rowCount(), 1)
        self.assertEqual(tab.table.item(0, 0).text(), "drone_operator_01")
        self.assertEqual(tab.count_label.text(), "1 user")
        self.assertTrue(tab.details_widget.isHidden())

    def test_clicking_risk_level_displays_user_details(self):
        tab = RoleSessionsTab(SectorViewClient())
        tab.refresh()
        self.assertTrue(self._wait_for_role_view(tab))

        tab.table.cellClicked.emit(0, 1)

        self.assertFalse(tab.details_widget.isHidden())
        self.assertIn("Overall risk score: 70.0 / 100", tab.details_summary.text())
        self.assertIn("not a percentage chance of a problem", tab.details_summary.text())
        self.assertEqual(tab.details_role.text(), "Role: Drone Operator")
        self.assertIn("#3b82f6", tab.details_role.styleSheet())
        self.assertIn(
            "Difference from this role's usual pattern: 20.0 / 100",
            tab.details_analysis.text(),
        )
        self.assertIn(
            "Isolation Forest: 60.0 · One-Class SVM: 65.0",
            tab.details_analysis.text(),
        )
        self.assertLess(
            tab.details_widget.layout().indexOf(tab.details_role),
            tab.details_widget.layout().indexOf(tab.details_analysis),
        )
        self.assertTrue(tab.details_analysis.text().endswith("One-Class SVM: 65.0"))
        self.assertEqual(
            tab.details_behavior_values["files_accessed"]["baseline"].text(),
            "12.0 files per event",
        )
        self.assertEqual(
            tab.details_behavior_values["files_accessed"]["user"].text(),
            "50.0 files per event",
        )
        self.assertEqual(
            tab.details_behavior_values["off_hours_access"]["baseline"].text(),
            "20.0% of events",
        )
        self.assertEqual(
            tab.details_behavior_values["off_hours_access"]["user"].text(),
            "30.0% of events",
        )

    def test_missing_role_baseline_uses_peer_average_with_plain_language_note(self):
        class MissingBaselineClient(SectorViewClient):
            def get_scores(self):
                scores = super().get_scores()
                scores.extend([
                    {
                        "user": "researcher_02",
                        "role": "researcher",
                        "risk_score": 12,
                        "risk_level": "LOW",
                        "avg_files": 8,
                        "avg_transfer": 14,
                        "avg_failed_logins": 0.4,
                        "off_hours": 2,
                        "total_logs": 10,
                    },
                ])
                for score in scores:
                    score.pop("role_baseline", None)
                return scores

            def get_active_taxonomy(self):
                return {"roles": ["researcher", "drone_operator"]}

        tab = RoleSessionsTab(MissingBaselineClient())
        tab.refresh()
        self.assertTrue(self._wait_for_role_view(tab, expected_rows=3))
        researcher = next(
            score for score in tab.scores if score["user"] == "researcher_01"
        )
        tab._show_details(researcher)

        self.assertEqual(
            tab.details_behavior_values["files_accessed"]["baseline"].text(),
            "8.0 files per event",
        )
        self.assertIn(
            "estimated from other monitored users in this role",
            tab.details_behavior_note.text(),
        )
        self.assertIn(
            "Average number of files accessed",
            tab.details_behavior.itemAtPosition(1, 0).widget().toolTip(),
        )
        self.assertIn(
            "not a total",
            tab.details_behavior_note.text(),
        )

    def test_role_behavior_filters_and_risk_score_sorting(self):
        class AllRolesClient(SectorViewClient):
            def get_active_taxonomy(self):
                return {"roles": ["researcher", "drone_operator"]}

        tab = RoleSessionsTab(AllRolesClient())
        tab.refresh()
        self.assertTrue(self._wait_for_role_view(tab, expected_rows=2))

        tab.risk_filter.setCurrentIndex(tab.risk_filter.findData("HIGH"))
        self.assertEqual(tab.table.rowCount(), 1)
        self.assertEqual(tab.table.item(0, 0).text(), "drone_operator_01")

        tab.risk_filter.setCurrentIndex(0)
        tab.user_filter.setCurrentIndex(tab.user_filter.findData("researcher_01"))
        self.assertEqual(tab.table.rowCount(), 1)
        self.assertEqual(tab.table.item(0, 0).text(), "researcher_01")

        tab.user_filter.setCurrentIndex(0)
        tab.search_input.setText("drone")
        self.assertEqual(tab.table.rowCount(), 1)
        self.assertEqual(tab.table.item(0, 0).text(), "drone_operator_01")

        tab.search_input.clear()
        tab.sort_order.setCurrentIndex(tab.sort_order.findData("descending"))
        self.assertEqual(tab.table.item(0, 0).text(), "drone_operator_01")
        tab.sort_order.setCurrentIndex(tab.sort_order.findData("ascending"))
        self.assertEqual(tab.table.item(0, 0).text(), "researcher_01")

    def _wait_for_role_view(self, tab, expected_rows=1):
        return wait_for(self.app, lambda: tab.table.rowCount() == expected_rows)
