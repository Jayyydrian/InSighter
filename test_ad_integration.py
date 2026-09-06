import os
import tempfile
import unittest

from ldap3 import Connection, MOCK_SYNC, OFFLINE_AD_2012_R2, Server

import database
import ingestion


class ActiveDirectoryIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.database_path = tempfile.mktemp(suffix=".db")
        self.previous_database_path = database.DB_PATH
        database.DB_PATH = self.database_path
        os.environ.update({
            "INSIGHTER_AD_SERVER": "mock-ad",
            "INSIGHTER_AD_USER": "cn=admin,dc=example,dc=com",
            "INSIGHTER_AD_PASSWORD": "secret",
            "INSIGHTER_AD_SEARCH_BASE": "dc=example,dc=com",
            "INSIGHTER_AD_USE_SSL": "false",
        })
        conn = database.connect()
        conn.execute(
            """CREATE TABLE logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user TEXT,
                login_hour INTEGER,
                files_accessed INTEGER,
                data_transferred_mb REAL,
                failed_logins INTEGER,
                off_hours_access INTEGER,
                role TEXT,
                source TEXT,
                payload_encrypted TEXT,
                ingested_at TEXT DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        database.DB_PATH = self.previous_database_path
        if os.path.exists(self.database_path):
            os.remove(self.database_path)

    def test_mock_ad_events_are_normalized_and_sanitized(self):
        connection = Connection(
            Server("mock-ad", get_info=OFFLINE_AD_2012_R2),
            user="cn=admin,dc=example,dc=com",
            password="secret",
            client_strategy=MOCK_SYNC,
        )
        connection.bind()
        connection.strategy.add_entry(
            "cn=Alice,dc=example,dc=com",
            {
                "objectClass": ["top", "person", "user"],
                "objectCategory": "person",
                "sAMAccountName": "alice",
                "lastLogon": 134000000000000000,
                "badPwdCount": 3,
                "title": "Developer",
            },
        )

        self.assertEqual(ingestion.ingest_from_active_directory(connection), 1)
        conn = database.connect()
        row = conn.execute(
            "SELECT user, source, failed_logins, role, payload_encrypted FROM logs"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "alice")
        self.assertEqual(row[1], "active_directory")
        self.assertEqual(row[2], 3)
        self.assertEqual(row[3], "developer")
        self.assertTrue(row[4])


if __name__ == "__main__":
    unittest.main()
