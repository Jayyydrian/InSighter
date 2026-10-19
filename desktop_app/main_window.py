from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QStackedWidget, QButtonGroup, QScrollArea, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer, QDateTime
from desktop_app.async_utils import run_async
from desktop_app.connection_state import CONNECTED, ConnectionMonitor
from desktop_app.safe_text import plain_label

from desktop_app.dashboard_tab import DashboardTab
from desktop_app.profiles_tab import ProfilesTab
from desktop_app.role_sessions_tab import RoleSessionsTab
from desktop_app.alerts_tab import AlertsTab
from desktop_app.uav_config_tab import UavConfigTab
from desktop_app.role_baseline_setup_tab import RoleBaselineSetupTab
from desktop_app.summary_tab import SummaryTab
from desktop_app.sessions_tab import SessionsTab
from desktop_app.resources_tab import ResourcesTab
from desktop_app.audit_tab import AuditTab
from desktop_app.integrations_tab import IntegrationsTab
from desktop_app.compliance_tab import ComplianceTab

REFRESH_INTERVAL_MS = 5000

BG = "#0a0e1a"
SURFACE = "#111827"
SURFACE2 = "#1a2235"
BORDER = "#1e2d45"
BORDER2 = "#253550"
TEXT = "#e2e8f0"
MUTED = "#64748b"
MUTED2 = "#94a3b8"
ACCENT = "#3b82f6"
ACCENT2 = "#1d4ed8"
GREEN = "#10b981"
RED = "#ef4444"


class _NavItem(QPushButton):
    """One clickable row in the sidebar, mirroring the web dashboard's .nav-item."""

    def __init__(self, icon, text):
        super().__init__(f"  {icon}   {text}")
        self.setObjectName("navItem")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.badge = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.badge is not None:
            self.badge.adjustSize()
            self.badge.move(
                self.width() - self.badge.width() - 12,
                (self.height() - self.badge.height()) // 2,
            )


class MainWindow(QMainWindow):
    def __init__(self, client, on_logout):
        super().__init__()
        self.client = client
        self.on_logout = on_logout
        self._logged_out = False
        self.connection_monitor = ConnectionMonitor(self)
        self.client.connection_monitor = self.connection_monitor
        self.setWindowTitle("InSighter -- Insider Threat Monitoring Console")
        self.resize(1280, 820)
        self._build_ui()
        if hasattr(self, "uav_tab"):
            self.uav_tab.configuration_saved.connect(
                self.dashboard_tab.refresh_thresholds
            )
        if hasattr(self, "baseline_setup_tab"):
            self.baseline_setup_tab.configuration_changed.connect(
                self._update_baseline_banner
            )
        self.connection_monitor.state_changed.connect(
            self._connection_state_changed
        )
        self.connection_monitor.refreshed.connect(self._connection_refreshed)
        self.connection_monitor.session_expired.connect(self._session_expired)
        self._refresh_all()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_all)
        self.timer.start(REFRESH_INTERVAL_MS)

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._tick_clock)
        self.clock_timer.start(1000)
        self._tick_clock()

    # -- UI construction -------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_topbar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.tab_refs = []

        if self.client.role == "admin":
            body.addWidget(self._build_sidebar_admin())
            body.addWidget(self._build_content_admin(), stretch=1)
        elif self.client.role == "compliance":
            body.addWidget(self._build_sidebar_compliance())
            body.addWidget(self._build_content_compliance(), stretch=1)
        else:
            body.addWidget(self._build_sidebar_management())
            body.addWidget(self._build_content_management(), stretch=1)

        body_widget = QWidget()
        body_widget.setLayout(body)
        outer.addWidget(body_widget, stretch=1)

        self.setCentralWidget(central)

    def _build_topbar(self):
        bar = QFrame()
        bar.setFixedHeight(50)
        bar.setStyleSheet(f"background:{SURFACE}; border-bottom: 1px solid {BORDER};")
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 0, 20, 0)
        h.setSpacing(10)

        logo_icon = QLabel("IS")
        logo_icon.setFixedSize(30, 30)
        logo_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_icon.setStyleSheet(
            f"background:{ACCENT}; color:white; border-radius:6px; font-weight:700; font-size:14px;"
        )
        logo_col = QVBoxLayout()
        logo_col.setSpacing(0)
        name = QLabel("InSighter")
        name.setStyleSheet(f"color:{TEXT}; font-weight:600; font-size:14px;")
        tag = QLabel("threat-monitor v0.1-poc")
        tag.setStyleSheet(f"color:{MUTED}; font-size:9px; font-family:Consolas,monospace;")
        logo_col.addWidget(name)
        logo_col.addWidget(tag)

        h.addWidget(logo_icon)
        h.addLayout(logo_col)
        h.addStretch()

        pulse = QLabel("\u25cf")
        pulse.setStyleSheet(f"color:{GREEN}; font-size:10px;")
        self.live_label = QLabel("CONNECTED")
        self.live_label.setStyleSheet(f"color:{GREEN}; font-size:10px; font-family:Consolas,monospace;")
        self.live_pulse = pulse
        h.addWidget(pulse)
        h.addWidget(self.live_label)
        self.last_updated_label = plain_label("Updated: —")
        self.last_updated_label.setStyleSheet(
            f"color:{MUTED}; font-size:10px; font-family:Consolas,monospace;"
        )
        h.addWidget(self.last_updated_label)
        h.addWidget(self._divider())

        self.clock_label = QLabel("--:--:--")
        self.clock_label.setStyleSheet(f"color:{MUTED}; font-size:11px; font-family:Consolas,monospace;")
        h.addWidget(self.clock_label)

        if self.client.role == "admin":
            h.addWidget(self._divider())
            self.reseed_btn = QPushButton("\u21ba Reseed")
            self.reseed_btn.clicked.connect(self._reseed)
            h.addWidget(self.reseed_btn)
            self.reseed_status = plain_label("")
            self.reseed_status.setStyleSheet(f"color:{MUTED}; font-size:10px;")
            h.addWidget(self.reseed_status)

        h.addWidget(self._divider())
        role_label = plain_label(f"{self.client.username} \u00b7 {self.client.role}")
        role_label.setStyleSheet(f"color:{MUTED2}; font-size:11px; font-family:Consolas,monospace;")
        h.addWidget(role_label)

        logout_btn = QPushButton("Log out")
        logout_btn.clicked.connect(self._logout)
        h.addWidget(logout_btn)

        return bar

    def _divider(self):
        d = QFrame()
        d.setFixedSize(1, 20)
        d.setStyleSheet(f"background:{BORDER};")
        return d

    def _sidebar_frame(self):
        sb = QFrame()
        sb.setFixedWidth(220)
        sb.setStyleSheet(f"background:{SURFACE}; border-right: 1px solid {BORDER};")
        layout = QVBoxLayout(sb)
        layout.setContentsMargins(10, 14, 10, 14)
        layout.setSpacing(2)
        return sb, layout

    def _nav_section(self, layout, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{MUTED}; font-size:10px; font-weight:600; text-transform:uppercase; "
            f"letter-spacing:1px; padding:10px 8px 4px;"
        )
        layout.addWidget(lbl)

    def _build_sidebar_admin(self):
        sb, layout = self._sidebar_frame()

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        self._nav_section(layout, "Monitor")
        overview_item = _NavItem("\u2b21", "Overview")
        overview_item.setChecked(True)
        layout.addWidget(overview_item)

        sessions_item = _NavItem("\u25c8", "User Sessions")
        layout.addWidget(sessions_item)
        profiles_item = _NavItem("\u25c9", "User Profiles")
        layout.addWidget(profiles_item)
        role_item = _NavItem("\u25c7", "Role Behavior")
        layout.addWidget(role_item)
        alerts_item = _NavItem("\u25ce", "Alerts")
        layout.addWidget(alerts_item)

        self._nav_section(layout, "System")
        resources_item = _NavItem("\u25a3", "Resources")
        layout.addWidget(resources_item)
        audit_item = _NavItem("\u25eb", "Audit Log")
        layout.addWidget(audit_item)

        self._nav_section(layout, "Config")
        integrations_item = _NavItem("\u229e", "Integrations")
        layout.addWidget(integrations_item)
        baseline_item = _NavItem("\u25cf", "Baseline Setup")
        baseline_item.badge = plain_label("SETUP", baseline_item)
        baseline_item.badge.setStyleSheet(
            "background:#f59e0b; color:#111827; border-radius:7px; "
            "padding:2px 5px; font-size:9px; font-weight:700;"
        )
        baseline_item.badge.setVisible(False)
        layout.addWidget(baseline_item)
        self.baseline_nav_item = baseline_item
        settings_item = _NavItem("\u25e7", "UAV Configuration")
        layout.addWidget(settings_item)

        self.overview_badge = QLabel("0")
        self.overview_badge.setParent(overview_item)
        self.overview_badge.setFixedHeight(16)
        self.overview_badge.setStyleSheet(
            f"background:{RED}; color:white; font-size:9px; font-weight:700; "
            f"border-radius:8px; padding:1px 6px;"
        )
        self.overview_badge.adjustSize()
        self.overview_badge.move(overview_item.width() - self.overview_badge.width() - 10, 6)
        self.overview_badge.show()

        for item in (overview_item, sessions_item, profiles_item, role_item, alerts_item,
                     resources_item, audit_item, integrations_item, baseline_item, settings_item):
            self.nav_group.addButton(item)

        layout.addStretch()

        bottom = QFrame()
        bottom.setStyleSheet(f"border-top: 1px solid {BORDER};")
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(8, 12, 8, 0)
        bottom_layout.setSpacing(4)

        self.sb_logs = self._sidebar_stat(bottom_layout, "Total Logs")
        self.sb_anom = self._sidebar_stat(bottom_layout, "Anomalies", RED)
        self.sb_users = self._sidebar_stat(bottom_layout, "Users")
        layout.addWidget(bottom)

        self._nav_pages = {
            overview_item: 0,
            sessions_item: 1,
            profiles_item: 2,
            role_item: 3,
            alerts_item: 4,
            resources_item: 5,
            audit_item: 6,
            integrations_item: 7,
            baseline_item: 8,
            settings_item: 9,
        }
        for item in self._nav_pages:
            item.clicked.connect(lambda _checked, it=item: self._go_to(it))

        return sb

    def _build_sidebar_management(self):
        sb, layout = self._sidebar_frame()
        self._nav_section(layout, "Monitor")
        item = _NavItem("\u2b21", "Summary Report")
        item.setChecked(True)
        layout.addWidget(item)
        layout.addStretch()
        return sb

    def _build_sidebar_compliance(self):
        sb, layout = self._sidebar_frame()
        self._nav_section(layout, "Compliance")
        item = _NavItem("\u25eb", "Compliance Audit")
        item.setChecked(True)
        layout.addWidget(item)
        layout.addStretch()
        return sb

    def _sidebar_stat(self, layout, label_text, color=TEXT):
        row = QHBoxLayout()
        row.setContentsMargins(8, 4, 8, 4)
        lbl = QLabel(label_text)
        lbl.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        val = QLabel("\u2014")
        val.setStyleSheet(f"color:{color}; font-size:11px; font-weight:500; font-family:Consolas,monospace;")
        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(val)
        layout.addLayout(row)
        return val

    def _build_content_admin(self):
        scroll_wrap = QFrame()
        outer = QVBoxLayout(scroll_wrap)
        outer.setContentsMargins(18, 18, 18, 18)

        self.baseline_banner = QLabel("Role baselines are not configured. InSighter is using built-in defaults.")
        self.baseline_banner.setVisible(False)
        self.baseline_banner.setStyleSheet(
            "background:#1f2937; color:#f8fafc; border:1px solid #374151; border-radius:6px; padding:10px 12px;"
        )
        outer.addWidget(self.baseline_banner)

        self.stack = QStackedWidget()

        self.dashboard_tab = DashboardTab(self.client)
        self.sessions_tab = SessionsTab(self.client)
        self.profiles_tab = ProfilesTab(self.client)
        self.role_sessions_tab = RoleSessionsTab(self.client)
        self.alerts_tab = AlertsTab(self.client)
        self.resources_tab = ResourcesTab(self.client)
        self.audit_tab = AuditTab(self.client)
        self.integrations_tab = IntegrationsTab(self.client)
        self.baseline_setup_tab = RoleBaselineSetupTab(self.client)
        self.uav_tab = UavConfigTab(self.client)

        pages = (self.dashboard_tab, self.sessions_tab, self.profiles_tab, self.role_sessions_tab, self.alerts_tab,
             self.resources_tab, self.audit_tab,
                 self.integrations_tab, self.baseline_setup_tab, self.uav_tab)

        for page in pages:
            scroller = QScrollArea()
            scroller.setWidgetResizable(True)
            scroller.setStyleSheet("QScrollArea { border: none; }")
            scroller.setWidget(page)
            self.stack.addWidget(scroller)

        self.tab_refs = list(pages)

        outer.addWidget(self.stack)
        return scroll_wrap

    def _build_content_management(self):
        wrap = QFrame()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(24, 24, 24, 24)
        self.summary_tab = SummaryTab(self.client)
        outer.addWidget(self.summary_tab)
        outer.addStretch()
        self.tab_refs = [self.summary_tab]
        return wrap

    def _build_content_compliance(self):
        wrap = QFrame()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(18, 18, 18, 18)
        self.compliance_tab = ComplianceTab(self.client)
        outer.addWidget(self.compliance_tab)
        return wrap

    # -- Behavior ----------------------------------------------------------
    def _go_to(self, nav_item):
        index = self._nav_pages[nav_item]
        self.stack.setCurrentIndex(index)
        self._set_baseline_banner_visibility()
        self._refresh_active()

    def _tick_clock(self):
        self.clock_label.setText(QDateTime.currentDateTime().toString("HH:mm:ss"))

    def _connection_state_changed(self, state, message):
        if state == CONNECTED:
            self.live_label.setText("CONNECTED")
            self.live_label.setStyleSheet(
                f"color:{GREEN}; font-size:10px; font-family:Consolas,monospace;"
            )
            self.live_pulse.setStyleSheet(f"color:{GREEN}; font-size:10px;")
        else:
            self.live_label.setText("OFFLINE")
            self.live_label.setStyleSheet(
                f"color:{RED}; font-size:10px; font-family:Consolas,monospace;"
            )
            self.live_pulse.setStyleSheet(f"color:{RED}; font-size:10px;")
            if message:
                self.last_updated_label.setToolTip(
                    "Backend connection unavailable; retries continue."
                )

    def _connection_refreshed(self):
        self.last_updated_label.setText(
            "Updated: " + QDateTime.currentDateTime().toString("HH:mm:ss")
        )
        self.last_updated_label.setToolTip("")

    def _session_expired(self):
        if self._logged_out:
            return
        QMessageBox.warning(
            self,
            "Session expired",
            "Your InSighter session has expired. Please sign in again.",
        )
        self._logout()

    def _refresh_all(self):
        self._refresh_active()
        if self.client.role == "admin":
            self._update_sidebar_stats()
            self._update_baseline_banner()

    def _refresh_active(self):
        if self.client.role == "management":
            self.summary_tab.refresh()
            return
        if self.client.role == "compliance":
            self.compliance_tab.refresh()
            return
        index = self.stack.currentIndex()
        if 0 <= index < len(self.tab_refs):
            self.tab_refs[index].refresh()

    def _update_baseline_banner(self):
        if self.client.role != "admin":
            return
        run_async(
            self.client.get_baseline_setup,
            self._apply_baseline_status,
            parent=self,
            key="baseline-banner-status",
            monitor_source="baseline-status",
            report_success=False,
        )

    def _apply_baseline_status(self, payload):
        self._baseline_setup_pending = payload.get("status") == "not_configured"
        self._set_baseline_banner_visibility()

    def _set_baseline_banner_visibility(self):
        visible = (
            self.client.role == "admin"
            and getattr(self, "_baseline_setup_pending", False)
        )
        overview = getattr(self, "stack", None) is not None and self.stack.currentIndex() == 0
        self.baseline_banner.setVisible(visible and overview)
        badge = getattr(getattr(self, "baseline_nav_item", None), "badge", None)
        if badge is not None:
            badge.setVisible(visible)
        self.baseline_banner.setText(
            "Role baselines are not configured. InSighter is using built-in defaults."
        )

    def _update_sidebar_stats(self):
        users = getattr(self.dashboard_tab, "last_users", [])
        if not users:
            return
        total_logs = sum(u["total_logs"] for u in users)
        total_anom = sum(u["anomalous_logs"] for u in users)
        high_count = sum(1 for u in users if u["risk_level"] == "HIGH")

        self.sb_logs.setText(str(total_logs))
        self.sb_anom.setText(str(total_anom))
        self.sb_users.setText(str(len(users)))
        self.overview_badge.setText(str(high_count))
        self.overview_badge.adjustSize()
        self.overview_badge.move(
            self.overview_badge.parent().width() - self.overview_badge.width() - 10, 6
        )

    def _reseed(self):
        if hasattr(self, "dashboard_tab"):
            self.dashboard_tab.reset_history()
        self.reseed_btn.setEnabled(False)
        self.reseed_status.setText("Reseeding...")
        run_async(
            self.client.reseed,
            self._reseed_succeeded,
            self._reseed_failed,
            parent=self,
            monitor_source="reseed",
            counts_toward_offline=False,
            report_success=False,
        )

    def _reseed_succeeded(self, _result):
        self.reseed_btn.setEnabled(True)
        self.reseed_status.setText("Reseed complete.")
        self._refresh_all()

    def _reseed_failed(self, exc):
        self.reseed_btn.setEnabled(True)
        self.reseed_status.setText(
            "Reseed failed: " + (getattr(exc, "message", None) or "Request failed.")
        )

    def _logout(self):
        if self._logged_out:
            return
        self._logged_out = True
        self.timer.stop()
        self.clock_timer.stop()
        self.on_logout()
        run_async(
            self.client.logout,
            lambda _result: None,
        )
