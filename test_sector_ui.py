import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QScrollArea

from desktop_app.dashboard_tab import DashboardTab
from desktop_app.role_sessions_tab import RoleSessionsTab


class SectorViewClient:
    def get_scores(self):
        return [
            {"user": "researcher_01", "role": "researcher", "risk_score": 10, "risk_level": "LOW", "role_baseline_score": 1, "avg_files": 2, "avg_transfer": 3, "if_score": 8, "ocsvm_score": 9, "off_hours": 0},
            {"user": "drone_operator_01", "role": "drone_operator", "risk_score": 70, "risk_level": "HIGH", "role_baseline_score": 20, "avg_files": 50, "avg_transfer": 100, "if_score": 60, "ocsvm_score": 65, "off_hours": 3},
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

        tab.table.cellClicked.emit(0, 1)

        self.assertFalse(tab.details_widget.isHidden())
        self.assertIn("Risk score: 70%", tab.details_summary.text())
        self.assertIn("Role: Drone Operator", tab.details_summary.text())
        self.assertIn("Role baseline deviation: 20", tab.details_summary.text())
        self.assertIn("Avg files accessed: 50", tab.details_summary.text())
        self.assertIn("Avg transfer: 100 MB", tab.details_summary.text())

    def test_role_behavior_filters_and_risk_score_sorting(self):
        class AllRolesClient(SectorViewClient):
            def get_active_taxonomy(self):
                return {"roles": ["researcher", "drone_operator"]}

        tab = RoleSessionsTab(AllRolesClient())
        tab.refresh()

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

    def test_dashboard_history_scrolls_with_twenty_users(self):
        tab = DashboardTab(SectorViewClient())
        tab.history = {f"operator_{index:02d}": [index, index + 1] for index in range(1, 21)}
        tab._update_sparklines()
        tab.resize(1100, 760)
        tab.show()
        self.app.processEvents()

        history_scroll = next(
            area for area in tab.findChildren(QScrollArea)
            if area.widget() is tab.sparkline_container
        )
        self.assertEqual(len(tab._sparkline_rows), 20)
        self.assertGreater(history_scroll.verticalScrollBar().maximum(), 0)
        self.assertLessEqual(history_scroll.height(), 260)
        tab.close()

    def test_dashboard_drops_old_sector_history_rows(self):
        class ActiveSectorClient(SectorViewClient):
            def get_scores(self):
                return [super().get_scores()[1]]

        tab = DashboardTab(ActiveSectorClient())
        tab.history = {"researcher_01": [12, 13], "drone_operator_01": [40, 50]}
        tab._update_sparklines()

        tab._refresh_scores()

        self.assertEqual(set(tab.history), {"drone_operator_01"})
        self.assertEqual(set(tab._sparkline_rows), {"drone_operator_01"})
        tab.close()


if __name__ == "__main__":
    unittest.main()