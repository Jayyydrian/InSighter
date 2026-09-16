import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import requests

DB_PATH = tempfile.mktemp(suffix=".db")
os.environ["INSIGHTER_DB_PATH"] = DB_PATH
os.environ["INSIGHTER_DISABLE_BACKGROUND_SIM"] = "1"

import llm_advisor
import app


SAMPLE_ALERT = {
    "id": 42,
    "user": "alice",
    "role": "researcher",
    "login_hour": 23,
    "files_accessed": 55,
    "data_transferred_mb": 480.0,
    "failed_logins": 5,
    "description": "off-hours login, accessed 55 files, transferred 480 MB",
    "severity": "HIGH",
    "flagged_by": "IF + OC-SVM",
    "if_score": 82.0,
    "ocsvm_score": 74.0,
    "final_score": 79.0,
}


def _mock_ollama_response(explanation="Looks unusual for this role.",
                           recommendation="Confirm the login with the user's manager."):
    resp = MagicMock()
    resp.status_code = 200
    resp.ok = True
    resp.json.return_value = {
        "response": json.dumps({"explanation": explanation, "recommendation": recommendation})
    }
    return resp


class LlmAdvisorUnitTest(unittest.TestCase):
    """llm_advisor.explain_alert() must never raise, and must degrade gracefully."""

    def test_successful_response_is_parsed(self):
        with patch("llm_advisor.requests.post", return_value=_mock_ollama_response()):
            result = llm_advisor.explain_alert(SAMPLE_ALERT, "private_school", {"data_categories": ["pii"]})
        self.assertTrue(result["available"])
        self.assertEqual(result["recommendation"], "Confirm the login with the user's manager.")

    def test_connection_error_is_graceful(self):
        with patch("llm_advisor.requests.post", side_effect=requests.exceptions.ConnectionError()):
            result = llm_advisor.explain_alert(SAMPLE_ALERT, "sme_startup", {"data_categories": []})
        self.assertFalse(result["available"])
        self.assertIn("not running", result["reason"])

    def test_timeout_is_graceful(self):
        with patch("llm_advisor.requests.post", side_effect=requests.exceptions.Timeout()):
            result = llm_advisor.explain_alert(SAMPLE_ALERT, "sme_startup", {"data_categories": []})
        self.assertFalse(result["available"])
        self.assertIn("timed out", result["reason"])

    def test_model_not_found_is_graceful(self):
        resp = MagicMock(status_code=404, ok=False)
        with patch("llm_advisor.requests.post", return_value=resp):
            result = llm_advisor.explain_alert(SAMPLE_ALERT, "sme_startup", {"data_categories": []})
        self.assertFalse(result["available"])
        self.assertIn("not found", result["reason"])

    def test_malformed_model_output_is_graceful(self):
        resp = MagicMock(status_code=200, ok=True)
        resp.json.return_value = {"response": "not valid json at all"}
        with patch("llm_advisor.requests.post", return_value=resp):
            result = llm_advisor.explain_alert(SAMPLE_ALERT, "sme_startup", {"data_categories": []})
        self.assertFalse(result["available"])
        self.assertIsNone(result["explanation"])


class ExplainAlertRouteTest(unittest.TestCase):
    """/api/alerts/<id>/explain: admin-only, caches, degrades gracefully."""

    @classmethod
    def setUpClass(cls):
        cls.original_alerts = app.get_recent_alerts
        app.get_recent_alerts = lambda: [SAMPLE_ALERT]

    @classmethod
    def tearDownClass(cls):
        app.get_recent_alerts = cls.original_alerts
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)

    def setUp(self):
        # Fresh cache per test so results don't leak between tests.
        app._alert_explanation_cache.clear()

    def _login(self, username, password):
        client = app.app.test_client()
        client.post("/login", data={"username": username, "password": password})
        return client

    def test_requires_admin(self):
        client = self._login("hr_officer", "hr_officer123")
        r = client.post("/api/alerts/42/explain")
        self.assertNotEqual(r.status_code, 200)

    def test_unknown_alert_id_returns_404(self):
        client = self._login("admin", "admin123")
        r = client.post("/api/alerts/999999/explain")
        self.assertEqual(r.status_code, 404)

    def test_success_is_cached_after_first_call(self):
        client = self._login("admin", "admin123")
        with patch("app.explain_alert", return_value={
            "available": True, "reason": None,
            "explanation": "e", "recommendation": "r",
        }) as mocked:
            r1 = client.post("/api/alerts/42/explain")
            r2 = client.post("/api/alerts/42/explain")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.get_json()["explanation"], "e")
        self.assertEqual(r2.get_json()["explanation"], "e")
        mocked.assert_called_once()  # second call served from cache, not re-queried

    def test_unavailable_result_is_not_cached(self):
        client = self._login("admin", "admin123")
        with patch("app.explain_alert", return_value={
            "available": False, "reason": "Ollama not running at http://localhost:11434",
            "explanation": None, "recommendation": None,
        }) as mocked:
            client.post("/api/alerts/42/explain")
            client.post("/api/alerts/42/explain")
        self.assertEqual(mocked.call_count, 2)  # retried both times, not cached as a permanent failure


if __name__ == "__main__":
    unittest.main()
