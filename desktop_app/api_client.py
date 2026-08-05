"""
Thin HTTP client the desktop app uses to talk to the InSighter Flask backend
(the Logic + Authentication tiers). The desktop app itself is purely the
Presentation tier, per the Chapter 3 five-tier architecture.
"""

import requests

from desktop_app.server_launcher import BASE_URL


class ApiError(Exception):
    def __init__(self, status_code, message):
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class ApiClient:
    def __init__(self):
        self.session = requests.Session()
        self.username = None
        self.role = None

    # ── Auth ─────────────────────────────────────────────────────────────
    def login(self, username, password):
        resp = self.session.post(
            f"{BASE_URL}/login",
            data={"username": username, "password": password},
            allow_redirects=False,
        )
        if resp.status_code == 302:
            # success -> Flask redirects; fetch role via a lightweight probe
            self.username = username
            probe = self.session.get(f"{BASE_URL}/api/whoami")
            self.role = probe.json().get("role")
            return True
        return False

    def logout(self):
        self.session.get(f"{BASE_URL}/logout")
        self.username = None
        self.role = None

    # ── Data endpoints ───────────────────────────────────────────────────
    def get_scores(self):
        return self._get("/api/scores")

    def get_alerts(self):
        return self._get("/api/alerts")

    def get_resources(self):
        return self._get("/api/resources")

    def get_uav_config(self):
        return self._get("/api/uav-config")

    def get_summary(self):
        return self._get("/api/summary")

    def get_sessions(self):
        return self._get("/api/sessions")

    def get_audit_log(self):
        return self._get("/api/audit-log")

    def get_integrations(self):
        return self._get("/api/integrations")

    def save_uav_config(self, payload):
        resp = self.session.post(f"{BASE_URL}/api/uav-config", json=payload)
        if resp.status_code != 200:
            raise ApiError(resp.status_code, "Failed to save UAV configuration.")
        return resp.json()

    def reseed(self):
        return self._get("/api/reseed")

    def simulate(self):
        return self._get("/api/simulate")

    # ── Internal ─────────────────────────────────────────────────────────
    def _get(self, path):
        resp = self.session.get(f"{BASE_URL}{path}")
        if resp.status_code == 401:
            raise ApiError(401, "Session expired. Please log in again.")
        if resp.status_code == 403:
            raise ApiError(403, "Access restricted to administrator accounts.")
        if resp.status_code != 200:
            raise ApiError(resp.status_code, f"Request to {path} failed.")
        return resp.json()
