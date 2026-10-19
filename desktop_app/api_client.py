"""
Thin HTTP client the desktop app uses to talk to the InSighter Flask backend
(the Logic + Authentication tiers). The desktop app itself is purely the
Presentation tier, per the Chapter 3 five-tier architecture.
"""

import html
import logging
import os
import re
from urllib.parse import urlsplit

import requests

from desktop_app.server_launcher import BASE_URL

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status_code, message):
        self.status_code = status_code
        self.message = message
        self.is_unexpected_response = False
        super().__init__(message)


class ApiClient:
    def __init__(self):
        self.session = requests.Session()
        self.username = None
        self.role = None

    def _request(self, method, path, **kwargs):
        login_request = method.upper() == "POST" and path == "/login"
        kwargs["allow_redirects"] = False if not login_request else kwargs.get(
            "allow_redirects", False
        )
        timeout = kwargs.pop("timeout", None)
        if timeout is None:
            timeout = (
                float(os.environ.get("INSIGHTER_API_CONNECT_TIMEOUT", "3")),
                float(os.environ.get("INSIGHTER_API_READ_TIMEOUT", "5")),
            )
        try:
            response = self.session.request(
                method, f"{BASE_URL}{path}", timeout=timeout, **kwargs
            )
            if not login_request and response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location", "")
                redirect_path = urlsplit(location).path
                if path == "/logout" and redirect_path.startswith("/login"):
                    return response
                if redirect_path.startswith("/login"):
                    raise ApiError(401, "Session expired. Please log in again.")
                raise self._unexpected_response(
                    f"Unexpected redirect while requesting {path}."
                )
            return response
        except requests.Timeout as exc:
            raise ApiError(None, "The InSighter backend did not respond in time.") from exc
        except requests.ConnectionError as exc:
            raise ApiError(None, "Could not reach the InSighter backend.") from exc

    @staticmethod
    def _unexpected_response(log_message, *args):
        logger.warning(log_message, *args)
        error = ApiError(None, "The backend returned an unexpected response.")
        error.is_unexpected_response = True
        return error

    @classmethod
    def _json(cls, response, path):
        try:
            return response.json()
        except ValueError as exc:
            raise cls._unexpected_response(
                "The backend returned a non-JSON response for %s.", path
            ) from exc

    # ── Auth ─────────────────────────────────────────────────────────────
    def login(self, username, password):
        resp = self._request(
            "POST", "/login",
            data={"username": username, "password": password},
            allow_redirects=False,
        )
        if resp.status_code == 302:
            # success -> Flask redirects; fetch role via a lightweight probe
            self.username = username
            probe = self._request("GET", "/api/whoami")
            self.role = self._json(probe, "/api/whoami").get("role")
            return True
        if resp.status_code in (401, 429):
            match = re.search(r'<div class="error">(.*?)</div>', resp.text, re.DOTALL)
            message = html.unescape(match.group(1)).strip() if match else (
                "Too many unsuccessful sign-in attempts. Please try again later."
                if resp.status_code == 429 else "Invalid username or password."
            )
            raise ApiError(resp.status_code, message)
        return False

    def logout(self):
        response = self._request("GET", "/logout")
        if response.status_code == 200:
            self._json(response, "/logout")
        self.username = None
        self.role = None

    # ── Data endpoints ───────────────────────────────────────────────────
    def get_scores(self):
        return self._get("/api/scores")

    def get_active_taxonomy(self):
        return self._get("/api/deployment-config/taxonomy")

    def get_alerts(self):
        return self._get("/api/alerts")

    def explain_alert(self, alert_id):
        server_timeout = float(os.environ.get(
            "INSIGHTER_LLM_TIMEOUT",
            os.environ.get("INSIGHTER_OLLAMA_TIMEOUT_SECONDS", "90"),
        ))
        configured_ai_timeout = float(os.environ.get("INSIGHTER_AI_TIMEOUT", "120"))
        # A configured client value may be raised to preserve the server margin.
        timeout = (
            float(os.environ.get("INSIGHTER_API_CONNECT_TIMEOUT", "3")),
            max(configured_ai_timeout, server_timeout + 30),
        )
        return self._post(f"/api/alerts/{int(alert_id)}/explain", timeout=timeout)

    def get_compliance_alerts(self):
        return self._get("/api/compliance/alerts")

    def get_resources(self):
        return self._get("/api/resources")

    def get_uav_config(self):
        return self._get("/api/uav-config")

    def get_deployment_taxonomy(self):
        return self._get("/api/deployment-config/taxonomy")

    def get_monitoring_scope(self):
        return self._get("/api/deployment-config/monitoring-scope")

    def get_summary(self):
        return self._get("/api/summary")

    def get_sessions(self):
        return self._get("/api/sessions")

    def get_audit_log(self):
        return self._get("/api/audit-log")

    def get_compliance_audit_log(self):
        return self._get("/api/compliance/audit-log")

    def get_integrations(self):
        return self._get("/api/integrations")

    def get_baseline_setup(self):
        return self._baseline_request("GET", "/api/baseline-setup")

    def save_baseline_setup(self, payload):
        return self._baseline_request("PUT", "/api/baseline-setup", json=payload)

    def reset_baseline_setup(self):
        return self._baseline_request("POST", "/api/baseline-setup/reset", json={})

    def ingest_events(self):
        return self._post("/api/ingest")

    def save_uav_config(self, payload):
        resp = self._request("POST", "/api/uav-config", json=payload)
        if resp.status_code != 200:
            raise ApiError(resp.status_code, "Failed to save UAV configuration.")
        return self._json(resp, "/api/uav-config")

    def save_deployment_config(self, payload):
        resp = self._request("POST", "/api/deployment-config", json=payload)
        if resp.status_code != 200:
            raise ApiError(resp.status_code, "Failed to save deployment configuration.")
        return self._json(resp, "/api/deployment-config")

    def reseed(self):
        return self._get("/api/reseed")

    def simulate(self):
        return self._get("/api/simulate")

    # ── Internal ─────────────────────────────────────────────────────────
    def _baseline_request(self, method, path, **kwargs):
        response = self._request(method, path, **kwargs)
        if response.status_code == 401:
            raise ApiError(401, "Session expired. Please log in again.")
        if response.status_code == 403:
            raise ApiError(403, "Access restricted to administrator accounts.")
        if response.status_code != 200:
            try:
                message = response.json().get("error", f"Request to {path} failed.")
            except ValueError:
                message = f"Request to {path} failed."
            raise ApiError(response.status_code, message)
        return self._json(response, path)

    def _get(self, path, timeout=None):
        kwargs = {} if timeout is None else {"timeout": timeout}
        resp = self._request("GET", path, **kwargs)
        if resp.status_code == 401:
            raise ApiError(401, "Session expired. Please log in again.")
        if resp.status_code == 403:
            raise ApiError(403, "Access restricted to administrator accounts.")
        if resp.status_code != 200:
            raise ApiError(resp.status_code, f"Request to {path} failed.")
        return self._json(resp, path)

    def _put(self, path, payload=None, timeout=None):
        kwargs = {} if timeout is None else {"timeout": timeout}
        resp = self._request("PUT", path, json=payload, **kwargs)
        if resp.status_code == 401:
            raise ApiError(401, "Session expired. Please log in again.")
        if resp.status_code == 403:
            raise ApiError(403, "Access restricted to administrator accounts.")
        if resp.status_code != 200:
            try:
                message = resp.json().get("error", f"Request to {path} failed.")
            except ValueError:
                message = f"Request to {path} failed."
            raise ApiError(resp.status_code, message)
        return self._json(resp, path)

    def _post(self, path, payload=None, timeout=None):
        kwargs = {} if timeout is None else {"timeout": timeout}
        resp = self._request("POST", path, json=payload, **kwargs)
        if resp.status_code == 401:
            raise ApiError(401, "Session expired. Please log in again.")
        if resp.status_code == 403:
            raise ApiError(403, "Access restricted to administrator accounts.")
        if resp.status_code != 200:
            try:
                message = resp.json().get("error", f"Request to {path} failed.")
            except ValueError:
                message = f"Request to {path} failed."
            raise ApiError(resp.status_code, message)
        return self._json(resp, path)
