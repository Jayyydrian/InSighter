import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from flask import session

os.environ.setdefault("INSIGHTER_DISABLE_BACKGROUND_SIM", "1")

from test_support import isolate_database

isolate_database()

import llm_advisor
import app
import requests
from desktop_app.api_client import ApiClient


SAMPLE_ALERT = {
    "id": 42,
    "user": "alice",
    "role": "researcher",
    "severity": "MEDIUM",
    "flagged_by": "Isolation Forest",
    "description": "Unusual file activity detected.",
    "off_hours_access": 0,
    "login_hour": 12,
    "files_accessed": 5,
    "data_transferred_mb": 10,
    "failed_logins": 0,
    "if_score": 80,
    "ocsvm_score": 70,
    "final_score": 75,
}


def ollama_response():
    response = MagicMock()
    response.ok = True
    response.status_code = 200
    response.json.return_value = {
        "response": json.dumps({
            "explanation": "The alert severity is MEDIUM with a final anomaly score of 75.",
            "recommendation": "Confirm the file activity with the user's manager.",
        }),
        "eval_count": 50,
        "eval_duration": 2_000_000_000,
    }
    return response


class LlmPerformanceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.log_path_patch = patch.object(
            llm_advisor,
            "USAGE_LOG_PATH",
            os.path.join(self.temp_dir.name, "usage.jsonl"),
        )
        self.log_path_patch.start()
        self.addCleanup(self.log_path_patch.stop)

    def test_request_body_has_configurable_generation_options(self):
        self.assertEqual(llm_advisor.LLM_NUM_CTX, 2048)
        self.assertEqual(llm_advisor.LLM_MAX_TOKENS, 256)
        self.assertEqual(llm_advisor.LLM_KEEP_ALIVE, "30m")
        with patch.dict(os.environ, {
            "INSIGHTER_LLM_NUM_CTX": "1792",
            "INSIGHTER_LLM_MAX_TOKENS": "128",
            "INSIGHTER_LLM_KEEP_ALIVE": "45m",
        }), patch("llm_advisor.requests.post", return_value=ollama_response()) as post:
            result = llm_advisor.explain_alert(
                SAMPLE_ALERT, "private_school", {"data_categories": ["pii"]}
            )

        body = post.call_args.kwargs["json"]
        self.assertEqual(body["options"], {
            "temperature": 0,
            "num_ctx": 1792,
            "num_predict": 128,
        })
        self.assertEqual(body["keep_alive"], "45m")
        self.assertTrue(result["available"])
        self.assertEqual(post.call_args.kwargs["timeout"], (3, 90))

        with patch("llm_advisor.requests.post", return_value=ollama_response()) as post:
            llm_advisor.explain_alert(
                SAMPLE_ALERT, "private_school", {"data_categories": ["pii"]}
            )
        self.assertEqual(post.call_args.kwargs["json"]["options"]["num_ctx"], 2048)

    def test_warmup_posts_model_without_prompt_and_handles_failures(self):
        response = MagicMock()
        with patch("llm_advisor.requests.post", return_value=response) as post:
            self.assertTrue(llm_advisor.warm_up_model())
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["model"], llm_advisor.OLLAMA_MODEL)
        self.assertEqual(body["keep_alive"], "30m")
        self.assertNotIn("prompt", body)
        self.assertEqual(post.call_args.kwargs["timeout"], (3, 120))

        with patch.dict(os.environ, {"INSIGHTER_LLM_WARMUP_TIMEOUT": "75"}), \
                patch("llm_advisor.requests.post", return_value=response) as post:
            self.assertTrue(llm_advisor.warm_up_model())
        self.assertEqual(post.call_args.kwargs["timeout"], (3, 75))

        for exc in (requests.Timeout("slow"), requests.ConnectionError("offline")):
            with patch("llm_advisor.requests.post", side_effect=exc):
                self.assertFalse(llm_advisor.warm_up_model())

    def test_failures_have_specific_reasons_and_usage_timings(self):
        scenarios = (
            (
                requests.ConnectionError("refused"),
                None,
                "Ollama is not running. Start it and try again.",
                "ConnectionError",
            ),
            (
                None,
                MagicMock(status_code=404, ok=False, **{
                    "json.return_value": {"error": "model not found"},
                }),
                f"Model '{llm_advisor.OLLAMA_MODEL}' is not installed. "
                f"Run: ollama pull {llm_advisor.OLLAMA_MODEL}",
                "ModelNotFound",
            ),
            (
                requests.Timeout("slow"),
                None,
                "The local model did not answer within 90 s. It may still be loading "
                "or running slowly; try again, or raise INSIGHTER_LLM_TIMEOUT.",
                "Timeout",
            ),
        )
        for exception, response, expected_reason, failure_kind in scenarios:
            with open(llm_advisor.USAGE_LOG_PATH, "w", encoding="utf-8"):
                pass
            with patch("llm_advisor.requests.post",
                       side_effect=exception if exception else None,
                       return_value=response):
                result = llm_advisor.explain_alert(
                    SAMPLE_ALERT, "private_school", {"data_categories": ["pii"]}
                )
            self.assertFalse(result["available"])
            self.assertEqual(result["reason"], expected_reason)
            with open(llm_advisor.USAGE_LOG_PATH, encoding="utf-8") as log_file:
                entry = json.loads(log_file.readline())
            self.assertEqual(entry["failure_kind"], failure_kind)
            self.assertIsNotNone(entry["elapsed_seconds"])
            self.assertIsNotNone(entry["error_type"])

    def test_success_logs_elapsed_and_ollama_token_metrics(self):
        with patch("llm_advisor.requests.post", return_value=ollama_response()):
            llm_advisor.explain_alert(
                SAMPLE_ALERT, "private_school", {"data_categories": ["pii"]}
            )
        with open(llm_advisor.USAGE_LOG_PATH, encoding="utf-8") as log_file:
            entry = json.loads(log_file.readline())
        self.assertTrue(entry["success"])
        self.assertGreaterEqual(entry["elapsed_seconds"], 0)
        self.assertEqual(entry["eval_count"], 50)
        self.assertEqual(entry["eval_duration_ms"], 2000.0)

    def test_default_desktop_ai_timeout_exceeds_server_timeout(self):
        client = ApiClient()
        with patch.dict(os.environ, {
            "INSIGHTER_AI_TIMEOUT": "120",
            "INSIGHTER_LLM_TIMEOUT": "90",
        }), \
                patch.object(client, "_request", return_value=MagicMock(
                    status_code=200, json=MagicMock(return_value={})
                )) as request:
            client.explain_alert(42)
        desktop_timeout = request.call_args.kwargs["timeout"]
        self.assertEqual(llm_advisor.REQUEST_TIMEOUT_SECONDS, 90)
        self.assertGreater(desktop_timeout[1], llm_advisor.REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(desktop_timeout[1], 120)

        with patch.dict(os.environ, {
            "INSIGHTER_AI_TIMEOUT": "120",
            "INSIGHTER_LLM_TIMEOUT": "150",
        }), patch.object(client, "_request", return_value=MagicMock(
            status_code=200, json=MagicMock(return_value={})
        )) as request:
            client.explain_alert(42)
        self.assertEqual(request.call_args.kwargs["timeout"][1], 180)

    def test_warmup_hook_obeys_both_disable_flags_and_uses_daemon_thread(self):
        with patch.dict(os.environ, {
            "INSIGHTER_DISABLE_BACKGROUND_SIM": "1",
            "INSIGHTER_LLM_WARMUP": "1",
        }), patch("app.threading.Thread") as thread:
            self.assertIsNone(app._start_llm_warmup())
            thread.assert_not_called()

        with patch.dict(os.environ, {
            "INSIGHTER_DISABLE_BACKGROUND_SIM": "0",
            "INSIGHTER_LLM_WARMUP": "0",
        }), patch("app.threading.Thread") as thread:
            self.assertIsNone(app._start_llm_warmup())
            thread.assert_not_called()

        with patch.dict(os.environ, {
            "INSIGHTER_DISABLE_BACKGROUND_SIM": "0",
            "INSIGHTER_LLM_WARMUP": "1",
        }), patch("app.threading.Thread") as thread:
            worker = app._start_llm_warmup()
        self.assertIs(worker, thread.return_value)
        self.assertTrue(thread.call_args.kwargs["daemon"])
        thread.return_value.start.assert_called_once()

    def test_disabled_background_import_still_serves_login_quickly(self):
        with patch.dict(os.environ, {
            "INSIGHTER_DISABLE_BACKGROUND_SIM": "1",
            "INSIGHTER_LLM_WARMUP": "1",
        }), patch("llm_advisor.requests.post",
                  side_effect=AssertionError("warm-up must be skipped")):
            response = app.app.test_client().get("/login")
        self.assertEqual(response.status_code, 200)

    def test_route_retries_failure_then_caches_later_success(self):
        app._alert_explanation_cache.clear()
        failed = {
            "available": False, "reason": "Ollama is not running.",
            "explanation": None, "recommendation": None,
        }
        succeeded = {
            "available": True, "reason": None,
            "explanation": "A grounded explanation.", "recommendation": "Review the alert.",
        }
        with app.app.test_request_context("/api/alerts/42/explain", method="POST"):
            session["username"] = "admin"
            session["role"] = "admin"
            with patch("app.get_recent_alerts", return_value=[SAMPLE_ALERT]), \
                    patch("app.get_active_sector", return_value="private_school"), \
                    patch("app.get_sector_config", return_value={"data_categories": []}), \
                    patch("app.explain_alert", side_effect=[failed, succeeded]) as explain:
                first = app.explain_alert_route(42)
                second = app.explain_alert_route(42)
                third = app.explain_alert_route(42)
        self.assertFalse(first.get_json()["available"])
        self.assertTrue(second.get_json()["available"])
        self.assertEqual(third.get_json(), succeeded)
        self.assertEqual(explain.call_count, 2)


if __name__ == "__main__":
    unittest.main()
