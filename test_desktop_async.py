import os
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import Mock, patch

import requests
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

from desktop_app import api_client
from desktop_app.api_client import ApiClient, ApiError
from desktop_app.connection_state import CONNECTED, ConnectionMonitor
from desktop_app.login_window import LoginWindow
from desktop_app.summary_tab import SummaryTab


def wait_for(app, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


class SlowHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(0.5)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        try:
            self.wfile.write(b"{}")
        except OSError:
            pass

    def log_message(self, *_args):
        pass


class ApiClientTransportTest(unittest.TestCase):
    @staticmethod
    def response(status, *, location=None, json_value=None, json_error=None, text=""):
        response = Mock()
        response.status_code = status
        response.headers = {"Location": location} if location is not None else {}
        response.text = text
        if json_error is not None:
            response.json.side_effect = json_error
        else:
            response.json.return_value = json_value
        return response

    def test_timeout_and_connection_errors_are_api_errors(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        server.daemon_threads = True
        Thread(target=server.serve_forever, daemon=True).start()
        client = ApiClient()
        start = time.monotonic()
        try:
            with patch.object(api_client, "BASE_URL", f"http://127.0.0.1:{server.server_port}"), \
                    patch.dict(os.environ, {"INSIGHTER_API_CONNECT_TIMEOUT": "0.2",
                                            "INSIGHTER_API_READ_TIMEOUT": "0.1"}):
                with self.assertRaises(ApiError) as raised:
                    client.get_scores()
            self.assertIsNone(raised.exception.status_code)
            self.assertEqual(raised.exception.message,
                             "The InSighter backend did not respond in time.")
            self.assertLess(time.monotonic() - start, 1.5)
        finally:
            server.shutdown()
            server.server_close()

        with patch.object(client.session, "request",
                          side_effect=requests.ConnectionError("connection refused")):
            with self.assertRaises(ApiError) as raised:
                client.get_scores()
        self.assertIsNone(raised.exception.status_code)
        self.assertEqual(raised.exception.message,
                         "Could not reach the InSighter backend.")

    def test_all_http_methods_use_request_with_timeout(self):
        client = ApiClient()
        response = Mock()
        response.json.return_value = {"role": "admin"}
        response.text = ""

        def make_response(method, url, **_kwargs):
            response.status_code = 302 if url.endswith("/login") else 200
            return response

        with patch.dict(os.environ, {
            "INSIGHTER_API_CONNECT_TIMEOUT": "2",
            "INSIGHTER_API_READ_TIMEOUT": "6",
            "INSIGHTER_AI_TIMEOUT": "60",
            "INSIGHTER_LLM_TIMEOUT": "30",
        }), patch.object(client, "_request", wraps=client._request) as request_spy, \
                patch.object(client.session, "request", side_effect=make_response) as session_spy:
            client.get_scores()
            client.get_active_taxonomy()
            client.get_alerts()
            client.explain_alert(1)
            client.get_compliance_alerts()
            client.get_resources()
            client.get_uav_config()
            client.get_deployment_taxonomy()
            client.get_monitoring_scope()
            client.get_summary()
            client.get_sessions()
            client.get_audit_log()
            client.get_compliance_audit_log()
            client.get_integrations()
            client.ingest_events()
            client.save_uav_config({})
            client.save_deployment_config({})
            client.reseed()
            client.simulate()
            client.login("user", "password")
            client.logout()

        self.assertEqual(request_spy.call_count, session_spy.call_count)
        self.assertEqual(session_spy.call_count, 22)
        for call in session_spy.call_args_list:
            expected = (2.0, 60.0) if call.args[1].endswith(
                "/api/alerts/1/explain"
            ) else (2.0, 6.0)
            self.assertEqual(call.kwargs["timeout"], expected)
        ai_call = next(call for call in session_spy.call_args_list
                       if call.args[1].endswith("/api/alerts/1/explain"))
        self.assertEqual(ai_call.kwargs["timeout"], (2.0, 60.0))
        self.assertTrue(all(
            call.kwargs["allow_redirects"] is False
            for call in session_spy.call_args_list
        ))

    def test_login_redirect_expires_session_once_across_multiple_failed_gets(self):
        client = ApiClient()
        monitor = ConnectionMonitor()
        expiries = []
        monitor.session_expired.connect(lambda: expiries.append(True))
        with patch.object(client.session, "request") as request:
            request.side_effect = [
                self.response(302, location="/login?next=/api/scores")
                for _ in range(3)
            ]
            for _ in range(3):
                with self.assertRaises(ApiError) as raised:
                    client.get_scores()
                self.assertEqual(raised.exception.status_code, 401)
                monitor.record_failure(raised.exception, True, "scores")

        self.assertEqual(expiries, [True])
        self.assertEqual(monitor.state, CONNECTED)

    def test_login_redirect_expires_session_for_ai_post(self):
        client = ApiClient()
        monitor = ConnectionMonitor()
        expiries = []
        monitor.session_expired.connect(lambda: expiries.append(True))
        with patch.object(client.session, "request") as request:
            request.return_value = self.response(
                302, location="/login?next=/api/alerts/3/explain"
            )
            with self.assertRaises(ApiError) as raised:
                client.explain_alert(3)
        self.assertEqual(raised.exception.status_code, 401)
        monitor.record_failure(raised.exception, False, "alert-ai")
        self.assertEqual(expiries, [True])
        self.assertEqual(request.call_args.args[0], "POST")
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    def test_html_200_is_unexpected_logged_and_does_not_mark_offline(self):
        client = ApiClient()
        monitor = ConnectionMonitor()
        with patch.object(client.session, "request") as request:
            request.return_value = self.response(
                200, json_error=ValueError("not json"), text="<html>login</html>"
            )
            with self.assertLogs(api_client.logger, level="WARNING") as captured:
                with self.assertRaises(ApiError) as raised:
                    client.get_scores()
        self.assertEqual(
            raised.exception.message, "The backend returned an unexpected response."
        )
        self.assertTrue(raised.exception.is_unexpected_response)
        self.assertEqual(len(captured.records), 1)
        monitor.record_failure(raised.exception, True, "scores")
        self.assertEqual(monitor.state, CONNECTED)

    def test_non_login_redirect_is_unexpected_response(self):
        client = ApiClient()
        with patch.object(client.session, "request") as request:
            request.return_value = self.response(302, location="/other")
            with self.assertRaises(ApiError) as raised:
                client.get_scores()
        self.assertIsNone(raised.exception.status_code)
        self.assertEqual(
            raised.exception.message, "The backend returned an unexpected response."
        )
        self.assertTrue(raised.exception.is_unexpected_response)

    def test_login_success_and_bad_password_keep_existing_behavior(self):
        client = ApiClient()
        with patch.object(client.session, "request") as request:
            request.side_effect = [
                self.response(302, location="/"),
                self.response(200, json_value={"role": "admin"}),
            ]
            self.assertTrue(client.login("admin", "secret"))
        self.assertEqual(client.role, "admin")

        client = ApiClient()
        with patch.object(client.session, "request") as request:
            request.return_value = self.response(
                401, text='<div class="error">Invalid username or password.</div>'
            )
            with self.assertRaises(ApiError) as raised:
                client.login("admin", "bad")
        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.message, "Invalid username or password.")
        self.assertFalse(raised.exception.is_unexpected_response)

    def test_forbidden_response_does_not_expire_session(self):
        client = ApiClient()
        monitor = ConnectionMonitor()
        expiries = []
        monitor.session_expired.connect(lambda: expiries.append(True))
        with patch.object(client.session, "request") as request:
            request.return_value = self.response(
                403, json_value={"error": "forbidden"}
            )
            with self.assertRaises(ApiError) as raised:
                client.get_scores()
        monitor.record_failure(raised.exception, True, "scores")
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(expiries, [])
        self.assertEqual(monitor.state, CONNECTED)

    def test_logout_login_redirect_is_expected(self):
        client = ApiClient()
        client.username = "admin"
        client.role = "admin"
        with patch.object(client.session, "request") as request:
            request.return_value = self.response(302, location="/login")
            client.logout()
        self.assertIsNone(client.username)
        self.assertIsNone(client.role)


class AsyncUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_refresh_returns_immediately_and_deduplicates_inflight_call(self):
        class SlowClient:
            calls = 0

            def get_summary(self):
                self.calls += 1
                time.sleep(2)
                return {"HIGH": 1, "MEDIUM": 2, "LOW": 3}

        client = SlowClient()
        tab = SummaryTab(client)
        start = time.monotonic()
        tab.refresh()
        elapsed = time.monotonic() - start
        tab.refresh()

        self.assertLess(elapsed, 0.1)
        self.assertTrue(wait_for(self.app, lambda: client.calls == 1))
        self.assertTrue(wait_for(self.app, lambda: tab.card_high.value_label.text() == "1"))
        self.assertEqual(client.calls, 1)

    def test_login_is_async_and_emits_success_on_ui_thread(self):
        window = LoginWindow()
        window.username_input.setText("admin")
        window.password_input.setText("secret")
        callback_threads = []

        def login(_username, _password):
            time.sleep(0.2)
            return True

        window.client.login = login
        window.login_succeeded.connect(
            lambda _client: callback_threads.append(QThread.currentThread())
        )
        start = time.monotonic()
        window._attempt_login()

        self.assertLess(time.monotonic() - start, 0.1)
        self.assertFalse(window.login_btn.isEnabled())
        self.assertEqual(window.login_btn.text(), "Signing in…")
        self.assertTrue(wait_for(self.app, lambda: bool(callback_threads)))
        self.assertTrue(window.login_btn.isEnabled())
        self.assertIs(callback_threads[0], self.app.thread())

    def test_login_displays_api_error_message_without_raw_exception(self):
        window = LoginWindow()
        window.username_input.setText("admin")
        window.password_input.setText("wrong")

        def login(_username, _password):
            time.sleep(0.05)
            raise ApiError(401, "Sign-in rejected by server.")

        window.client.login = login
        window._attempt_login()

        self.assertTrue(wait_for(self.app, lambda: window.login_btn.isEnabled()))
        self.assertEqual(window.error_label.text(), "Sign-in rejected by server.")


if __name__ == "__main__":
    unittest.main()
