import os
import tempfile
import unittest

DB_PATH = tempfile.mktemp(suffix=".db")
os.environ["INSIGHTER_DB_PATH"] = DB_PATH

import app
from privacy import hash_identifier


class ComplianceRbacTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_alerts = app.get_recent_alerts
        app.get_recent_alerts = lambda: [{
            "user": "alice",
            "login_hour": 23,
            "files_accessed": 10,
            "data_transferred_mb": 210,
            "failed_logins": 4,
            "description": "off-hours login, transferred 210 MB",
            "severity": "HIGH",
            "flagged_by": "IF",
            "if_score": 80.0,
            "ocsvm_score": 70.0,
            "final_score": 76.0,
        }]
        app.log_action("admin", "VIEWED_ALERTS", "alice incident reviewed")

    @classmethod
    def tearDownClass(cls):
        app.get_recent_alerts = cls.original_alerts
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)

    def _login(self, username, password):
        client = app.app.test_client()
        self.assertEqual(
            client.post("/login", data={"username": username, "password": password}).status_code,
            302,
        )
        return client

    def test_admin_sees_real_identity(self):
        client = self._login("admin", "admin123")
        self.assertEqual(client.get("/api/alerts").get_json()[0]["user"], "alice")
        self.assertTrue(any(
            row["username"] == "admin"
            for row in client.get("/api/audit-log").get_json()
        ))

    def test_compliance_sees_incident_with_pseudonyms(self):
        client = self._login("hr_officer", "hr_officer123")
        alerts = client.get("/api/compliance/alerts")
        self.assertEqual(alerts.status_code, 200)
        self.assertEqual(alerts.get_json()[0]["user"], hash_identifier("alice"))
        self.assertIn("off-hours login, transferred 210 MB", alerts.get_data(as_text=True))
        self.assertNotIn("alice", alerts.get_data(as_text=True))

        audit = client.get("/api/compliance/audit-log")
        self.assertEqual(audit.status_code, 200)
        entries = audit.get_json()
        self.assertTrue(any(row["username"] == hash_identifier("admin") for row in entries))
        self.assertTrue(any(hash_identifier("alice") in row["detail"] for row in entries))
        self.assertNotIn("alice", audit.get_data(as_text=True))
        self.assertNotIn("admin", [row["username"] for row in entries])

        self.assertEqual(client.get("/api/alerts").status_code, 403)
        self.assertEqual(client.get("/api/audit-log").status_code, 403)

    def test_management_remains_aggregate_only(self):
        client = self._login("manager", "manager123")
        summary = client.get("/api/summary")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(set(summary.get_json()), {"HIGH", "MEDIUM", "LOW"})
        for path in (
            "/api/alerts",
            "/api/compliance/alerts",
            "/api/audit-log",
            "/api/compliance/audit-log",
        ):
            self.assertEqual(client.get(path).status_code, 403)


if __name__ == "__main__":
    unittest.main()
