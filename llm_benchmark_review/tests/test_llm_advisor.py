import json
import os
import subprocess
import sys
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
UAV_ALERT = {
    **SAMPLE_ALERT,
    "id": 5186,
    "user": "drone_operator_01",
    "role": "drone_operator",
    "description": "Off-hours session with unusual data transfer.",
}
ALERT_5186 = {
    **UAV_ALERT,
    "id": 5186,
    "user": "drone_operator_04",
    "severity": "MEDIUM",
    "description": "unusual activity pattern",
    "login_hour": 6,
    "files_accessed": 0,
    "data_transferred_mb": 16.75,
    "failed_logins": 1,
    "if_score": 88.2,
    "ocsvm_score": 98.8,
    "final_score": 96.3,
    "off_hours_access": 0,
}
ALERT_5140 = {
    **ALERT_5186,
    "id": 5140,
    "user": "drone_operator_01",
    "login_hour": 10,
    "data_transferred_mb": 7.38,
    "if_score": 100.0,
    "off_hours_access": 0,
}
ALERT_1365 = {
    **ALERT_5140,
    "id": 1365,
    "user": "admin_staff_01",
    "role": "admin_staff",
    "description": "unusual activity pattern",
    "data_category": "projects",
    "files_accessed": 15,
    "data_transferred_mb": 7.27,
    "failed_logins": 1,
}
ALERT_2393 = {
    **ALERT_1365,
    "id": 2393,
    "user": "hr_05",
    "role": "hr",
    "data_category": "financial_transactions",
    "files_accessed": 15,
    "data_transferred_mb": 31.13,
    "failed_logins": 0,
}
ALERT_1394 = {
    **ALERT_1365,
    "id": 1394,
    "user": "researcher_02",
    "role": "researcher",
    "severity": "MEDIUM",
    "data_category": "projects",
    "login_hour": 18,
    "files_accessed": 4,
    "data_transferred_mb": 2.11,
    "failed_logins": 1,
    "if_score": 100.0,
    "ocsvm_score": 95.3,
    "final_score": 100.0,
}
ALERT_2329 = {
    **ALERT_2393,
    "id": 2329,
    "user": "hr_01",
    "role": "hr",
    "severity": "CRITICAL",
    "description": "off-hours login, accessed 103 files, transferred 793 MB, 9 failed logins",
    "login_hour": 0,
    "off_hours_access": 1,
    "data_category": "employee_records",
    "files_accessed": 103,
    "data_transferred_mb": 793.26,
    "failed_logins": 9,
    "if_score": 100.0,
    "ocsvm_score": 100.0,
    "final_score": 100.0,
}


def _mock_ollama_response(explanation="Looks unusual for this role.",
                           recommendation="Confirm the login with the user's manager."):
    resp = MagicMock()
    resp.status_code = 200
    resp.ok = True
    resp.json.return_value = {
        "response": json.dumps({"explanation": explanation, "recommendation": recommendation}),
        "prompt_eval_count": 123,
        "eval_count": 45,
        "total_duration": 2_000_000_000,
        "load_duration": 300_000_000,
        "prompt_eval_duration": 500_000_000,
        "eval_duration": 1_200_000_000,
    }
    return resp


class LlmAdvisorUnitTest(unittest.TestCase):
    """llm_advisor.explain_alert() must never raise, and must degrade gracefully."""

    def setUp(self):
        self._usage_log_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._usage_log_dir.cleanup)
        self.usage_log_path = os.path.join(self._usage_log_dir.name, "usage.jsonl")
        log_path_patch = patch.object(llm_advisor, "USAGE_LOG_PATH", self.usage_log_path)
        log_path_patch.start()
        self.addCleanup(log_path_patch.stop)

    def test_default_model_is_phi4_mini_when_model_variables_are_unset(self):
        child_environment = os.environ.copy()
        child_environment.pop("INSIGHTER_LLM_MODEL", None)
        child_environment.pop("INSIGHTER_OLLAMA_MODEL", None)
        model_name = subprocess.check_output(
            [
                sys.executable,
                "-c",
                "import llm_advisor; print(llm_advisor.OLLAMA_MODEL)",
            ],
            env=child_environment,
            text=True,
        ).strip()

        self.assertEqual(model_name, "phi4-mini")

    def test_successful_response_is_parsed(self):
        with patch("llm_advisor.requests.post", return_value=_mock_ollama_response()):
            result = llm_advisor.explain_alert(SAMPLE_ALERT, "private_school", {"data_categories": ["pii"]})
        self.assertTrue(result["available"])
        self.assertEqual(result["recommendation"], "Confirm the login with the user's manager.")

    def test_request_uses_grounded_and_proportionate_prompt_rules(self):
        with patch("llm_advisor.requests.post", return_value=_mock_ollama_response()) as mocked_post:
            llm_advisor.explain_alert(SAMPLE_ALERT, "private_school", {"data_categories": ["pii"]})

        system_prompt = mocked_post.call_args.kwargs["json"]["system"]
        self.assertIn("Do not invent", system_prompt)
        self.assertIn("Treat anomaly scores as triage signals, not proof", system_prompt)
        self.assertIn(llm_advisor.RISK_CONCLUSION_POLICY, system_prompt)
        self.assertIn("Use the supplied severity and computed scores", system_prompt)
        self.assertIn("For any claim that login timing is off-hours or unusual", system_prompt)
        for source in llm_advisor.ALLOWED_DATA_SOURCES:
            self.assertIn(source, system_prompt)
        self.assertIn(llm_advisor.MONITORED_CATEGORY_POLICY, system_prompt)
        self.assertIn("Follow-up actions may refer ONLY", system_prompt)
        self.assertIn("Do not recommend suspending or revoking access", system_prompt)

    def test_alert_1394_rejects_potential_insider_threat_conclusion(self):
        response = _mock_ollama_response(
            explanation=(
                "A researcher flagged unusual activity involving project files, "
                "with a medium severity alert indicating a potential insider threat."
            )
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                ALERT_1394,
                "private_school",
                {"data_categories": ["projects", "private_documents", "intellectual_property", "pii"]},
            )

        self.assertFalse(result["available"])
        self.assertIn("unsupported risk", result["reason"])

    def test_alert_severity_and_score_language_is_allowed(self):
        response = _mock_ollama_response(
            explanation="The alert severity is MEDIUM and the final anomaly score is 100."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                ALERT_1394,
                "private_school",
                {"data_categories": ["projects"]},
            )

        self.assertTrue(result["available"])

    def test_high_risk_label_requires_high_or_critical_severity(self):
        response = _mock_ollama_response(explanation="This is a high risk alert.")
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                ALERT_1394,
                "private_school",
                {"data_categories": ["projects"]},
            )

        self.assertFalse(result["available"])
        self.assertIn("unsupported risk", result["reason"])

    def test_verbatim_critical_label_is_allowed_for_critical_severity(self):
        critical_alert = {**ALERT_1394, "severity": "CRITICAL"}
        response = _mock_ollama_response(explanation="This is a CRITICAL severity alert.")
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                critical_alert,
                "private_school",
                {"data_categories": ["projects"]},
            )

        self.assertTrue(result["available"])

    def test_alert_2329_requires_verbatim_critical_severity(self):
        downgraded = _mock_ollama_response(
            explanation=(
                "The alert indicates CRITICAL severity. Anomaly scores are 100%, "
                "indicating a high risk."
            )
        )
        with patch("llm_advisor.requests.post", return_value=downgraded):
            result = llm_advisor.explain_alert(
                ALERT_2329,
                "sme_startup",
                {"data_categories": ["financial_transactions", "client_data", "employee_records"]},
            )

        self.assertFalse(result["available"])
        self.assertIn("severity claim", result["reason"])

        verbatim = _mock_ollama_response(
            explanation=(
                "The alert severity is CRITICAL and the final anomaly score is 100.0."
            )
        )
        with patch("llm_advisor.requests.post", return_value=verbatim):
            corrected_result = llm_advisor.explain_alert(
                ALERT_2329,
                "sme_startup",
                {"data_categories": ["financial_transactions", "client_data", "employee_records"]},
            )

        self.assertTrue(corrected_result["available"])

    def test_uav_response_rejects_unlisted_source_terms(self):
        generated_response = {
            "explanation": "The alert shows unusual access.",
            "recommendation": (
                "Review flight logs, GPS, camera footage, drone telemetry, mission logs, "
                "and drone platform access logs."
            ),
        }
        response = _mock_ollama_response(
            explanation=generated_response["explanation"],
            recommendation=generated_response["recommendation"],
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                UAV_ALERT, "uav_disaster_response", {"data_categories": ["disaster_data"]}
            )

        alert_fields = json.dumps(UAV_ALERT).casefold()
        for term in llm_advisor.UNSUPPORTED_DATA_SOURCE_TERMS:
            if term.casefold() not in alert_fields:
                self.assertNotIn(term.casefold(), (result["explanation"] or "").casefold())
                self.assertNotIn(term.casefold(), (result["recommendation"] or "").casefold())
        self.assertFalse(result["available"])
        self.assertIn("unsupported data source", result["reason"])

    def test_uav_source_term_present_in_alert_is_not_rejected(self):
        alert_with_gps = {
            **UAV_ALERT,
            "description": "The alert explicitly includes GPS metadata.",
        }
        response = _mock_ollama_response(
            recommendation="Review the GPS metadata included in this alert."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                alert_with_gps,
                "uav_disaster_response",
                {"data_categories": ["disaster_data"]},
            )

        self.assertTrue(result["available"])
        self.assertIn("GPS", result["recommendation"])

    def test_prompt_includes_explicit_off_hours_classification(self):
        with patch("llm_advisor.requests.post", return_value=_mock_ollama_response()) as mocked_post:
            llm_advisor.explain_alert(ALERT_5186, "uav_disaster_response", {"data_categories": []})

        user_prompt = mocked_post.call_args.kwargs["json"]["prompt"]
        self.assertTrue(user_prompt.startswith("SYSTEM TIMING DECISION: NOT CLASSIFIED OFF-HOURS"))
        self.assertIn("Omit timing completely", user_prompt)
        self.assertNotIn("Login hour: 6", user_prompt)

    def test_prompt_grounds_category_in_event_field_before_sector_list(self):
        with patch("llm_advisor.requests.post", return_value=_mock_ollama_response()) as mocked_post:
            llm_advisor.explain_alert(
                ALERT_1365,
                "private_school",
                {"data_categories": ["projects", "private_documents", "intellectual_property", "pii"]},
            )

        user_prompt = mocked_post.call_args.kwargs["json"]["prompt"]
        self.assertLess(user_prompt.index("EVENT DATA CATEGORY: projects"), user_prompt.index("Sector monitored data categories"))
        self.assertIn("Do not infer other categories from the sector list", user_prompt)

    def test_alert_1365_rejects_unreported_sector_categories(self):
        response = _mock_ollama_response(
            recommendation=(
                "Review the admin_staff_01's access to private_documents, "
                "intellectual_property, and pii for the past week."
            )
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                ALERT_1365,
                "private_school",
                {"data_categories": ["projects", "private_documents", "intellectual_property", "pii"]},
            )

        self.assertFalse(result["available"])
        self.assertIn("unsupported data source", result["reason"])

    def test_alert_1365_rejects_singular_mismatch_for_unreported_category(self):
        response = _mock_ollama_response(
            explanation="The alert involved access to a private document."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                ALERT_1365,
                "private_school",
                {"data_categories": ["projects", "private_documents", "intellectual_property", "pii"]},
            )

        self.assertFalse(result["available"])
        self.assertIn("unsupported data source", result["reason"])

    def test_event_category_singular_form_is_allowed_when_tag_matches(self):
        alert = {**ALERT_1365, "data_category": "projects"}
        response = _mock_ollama_response(
            explanation="The alert involved access to a project file."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                alert,
                "private_school",
                {"data_categories": ["projects", "private_documents", "intellectual_property", "pii"]},
            )

        self.assertTrue(result["available"])

    def test_matching_event_category_allows_review_with_access_records(self):
        alert = {**ALERT_2393, "data_category": "financial_transactions"}
        response = _mock_ollama_response(
            explanation="The event is tagged as financial transactions.",
            recommendation="Review the financial transactions and access records for this event.",
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                alert,
                "sme_startup",
                {"data_categories": ["financial_transactions", "client_data", "employee_records"]},
            )

        self.assertTrue(result["available"])

    def test_matching_event_category_cannot_become_a_category_specific_log_source(self):
        alert = {**ALERT_2393, "data_category": "financial_transactions"}
        response = _mock_ollama_response(
            recommendation="Review the financial transactions logs for this user."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                alert,
                "sme_startup",
                {"data_categories": ["financial_transactions", "client_data", "employee_records"]},
            )

        self.assertFalse(result["available"])

    def test_alert_2393_rejects_unreported_sector_categories(self):
        response = _mock_ollama_response(
            recommendation=(
                "Review the HR employee's access to client_data and employee_records."
            )
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                ALERT_2393,
                "sme_startup",
                {"data_categories": ["financial_transactions", "client_data", "employee_records"]},
            )

        self.assertFalse(result["available"])
        self.assertIn("unsupported data source", result["reason"])

    def test_event_category_claim_matching_the_row_tag_is_allowed(self):
        alert = {**ALERT_1365, "data_category": "pii"}
        response = _mock_ollama_response(
            explanation="This event is tagged with the pii data category.",
            recommendation="Review the alert's own fields and related file access events.",
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                alert,
                "private_school",
                {"data_categories": ["projects", "private_documents", "intellectual_property", "pii"]},
            )

        self.assertTrue(result["available"])

    def test_true_off_hours_prompt_includes_hour_and_system_classification(self):
        alert = {**ALERT_5186, "login_hour": 4, "off_hours_access": 1}
        with patch("llm_advisor.requests.post", return_value=_mock_ollama_response()) as mocked_post:
            llm_advisor.explain_alert(alert, "uav_disaster_response", {"data_categories": []})

        user_prompt = mocked_post.call_args.kwargs["json"]["prompt"]
        self.assertTrue(user_prompt.startswith("SYSTEM TIMING DECISION: OFF-HOURS"))
        self.assertIn("Login hour: 4 on a 24-hour clock", user_prompt)

    def test_generation_uses_deterministic_sampling(self):
        with patch("llm_advisor.requests.post", return_value=_mock_ollama_response()) as mocked_post:
            llm_advisor.explain_alert(ALERT_5186, "uav_disaster_response", {"data_categories": []})

        self.assertEqual(mocked_post.call_args.kwargs["json"]["options"]["temperature"], 0)

    def test_false_off_hours_flag_rejects_6am_claim_for_alert_5186(self):
        response = _mock_ollama_response(
            explanation="The 6 AM login was at an unusual hour."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                ALERT_5186, "uav_disaster_response", {"data_categories": []}
            )

        self.assertFalse(result["available"])
        self.assertIn("off-hours classification", result["reason"])

    def test_false_off_hours_flag_rejects_plural_unusual_login_hours(self):
        response = _mock_ollama_response(
            explanation="The researcher displayed unusual login hours."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                ALERT_5140, "uav_disaster_response", {"data_categories": []}
            )

        self.assertFalse(result["available"])
        self.assertIn("off-hours classification", result["reason"])

    def test_false_off_hours_flag_rejects_10am_claim_for_alert_5140(self):
        response = _mock_ollama_response(
            explanation="The login at 10 AM occurred at an unusual hour."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                ALERT_5140, "uav_disaster_response", {"data_categories": []}
            )

        self.assertFalse(result["available"])
        self.assertIn("off-hours classification", result["reason"])

    def test_false_off_hours_flag_rejects_unqualified_off_hours_claim(self):
        response = _mock_ollama_response(
            explanation="The data transfer occurred during off-hours."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                {**ALERT_5140, "login_hour": 14},
                "sme_startup",
                {"data_categories": ["financial_transactions"]},
            )

        self.assertFalse(result["available"])
        self.assertIn("off-hours classification", result["reason"])

    def test_false_off_hours_flag_allows_explicit_denial(self):
        response = _mock_ollama_response(
            explanation="No off-hours login was detected for this alert."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                ALERT_5140, "uav_disaster_response", {"data_categories": []}
            )

        self.assertTrue(result["available"])

    def test_false_off_hours_flag_allows_classification_denial_after_phrase(self):
        response = _mock_ollama_response(
            explanation="Off-hours access was not flagged by the system."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                ALERT_5140, "uav_disaster_response", {"data_categories": []}
            )

        self.assertTrue(result["available"])

    def test_false_off_hours_flag_rejects_positive_claim_with_later_denial(self):
        response = _mock_ollama_response(
            explanation=(
                "The event occurred during off-hours, though it was not classified as such."
            )
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                ALERT_5140, "uav_disaster_response", {"data_categories": []}
            )

        self.assertFalse(result["available"])
        self.assertIn("off-hours classification", result["reason"])

    def test_false_off_hours_flag_rejects_regular_hours_inference(self):
        response = _mock_ollama_response(
            explanation="The login at 10 AM occurred during regular hours."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                ALERT_5140, "uav_disaster_response", {"data_categories": []}
            )

        self.assertFalse(result["available"])
        self.assertIn("off-hours classification", result["reason"])

    def test_false_off_hours_flag_rejects_regular_working_hours_inference(self):
        response = _mock_ollama_response(
            explanation="The activity occurred during regular working hours."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                ALERT_5140, "uav_disaster_response", {"data_categories": []}
            )

        self.assertFalse(result["available"])
        self.assertIn("off-hours classification", result["reason"])

    def test_false_off_hours_flag_rejects_late_regular_hours_label(self):
        response = _mock_ollama_response(
            explanation="The event occurred at a time classified as regular hours."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                {**ALERT_5140, "login_hour": 21},
                "sme_startup",
                {"data_categories": ["employee_records"]},
            )

        self.assertFalse(result["available"])
        self.assertIn("off-hours classification", result["reason"])

    def test_false_off_hours_flag_rejects_late_hour_inference(self):
        response = _mock_ollama_response(
            explanation="The login occurred during a late hour."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                {**ALERT_5140, "login_hour": 21},
                "private_school",
                {"data_categories": ["private_documents"]},
            )

        self.assertFalse(result["available"])
        self.assertIn("off-hours classification", result["reason"])

    def test_false_off_hours_flag_rejects_regular_login_hours_label(self):
        response = _mock_ollama_response(
            explanation="The activity happened despite regular login hours."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                {**ALERT_5140, "login_hour": 9},
                "private_school",
                {"data_categories": ["private_documents"]},
            )

        self.assertFalse(result["available"])
        self.assertIn("off-hours classification", result["reason"])

    def test_false_off_hours_flag_rejects_unusual_adverb_tied_to_hour(self):
        response = _mock_ollama_response(
            explanation="The intern accessed files unusually at 17:00."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                {**ALERT_5140, "login_hour": 17},
                "private_school",
                {"data_categories": ["private_documents"]},
            )

        self.assertFalse(result["available"])
        self.assertIn("off-hours classification", result["reason"])

    def test_unusual_activity_with_a_time_is_not_automatically_a_timing_claim(self):
        response = _mock_ollama_response(
            explanation="Unusual activity was detected, including a login at 21 hours."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                {**ALERT_5140, "login_hour": 21},
                "uav_disaster_response",
                {"data_categories": []},
            )

        self.assertTrue(result["available"])

    def test_true_off_hours_flag_allows_correct_timing_claim(self):
        off_hours_alert = {**ALERT_5186, "login_hour": 4, "off_hours_access": 1}
        response = _mock_ollama_response(
            explanation="The 4 AM login is classified as off-hours."
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                off_hours_alert, "uav_disaster_response", {"data_categories": []}
            )

        self.assertTrue(result["available"])

    def test_monitored_category_is_rejected_when_framed_as_inspectable_logs(self):
        response = _mock_ollama_response(
            recommendation=(
                "Review the drone platform access and disaster data transfer logs."
            )
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                UAV_ALERT,
                "uav_disaster_response",
                {"data_categories": ["drone_platform_access", "disaster_data"]},
            )

        self.assertFalse(result["available"])
        self.assertIn("unsupported data source", result["reason"])

    def test_monitored_category_mention_as_label_is_allowed(self):
        response = _mock_ollama_response(
            explanation="Drone platform access is a monitored category in this sector.",
            recommendation="Review the alert's own fields and session/logon events.",
        )
        with patch("llm_advisor.requests.post", return_value=response):
            result = llm_advisor.explain_alert(
                UAV_ALERT,
                "uav_disaster_response",
                {"data_categories": ["drone_platform_access", "disaster_data"]},
            )

        self.assertTrue(result["available"])

    def test_successful_response_logs_usage_metrics(self):
        with patch("llm_advisor.requests.post", return_value=_mock_ollama_response()):
            result = llm_advisor.explain_alert(
                SAMPLE_ALERT, "private_school", {"data_categories": ["pii"]}
            )

        self.assertTrue(result["available"])
        with open(self.usage_log_path, encoding="utf-8") as log_file:
            entry = json.loads(log_file.readline())

        self.assertEqual(entry["model"], llm_advisor.OLLAMA_MODEL)
        self.assertEqual(entry["alert_id"], 42)
        self.assertEqual(entry["prompt_eval_count"], 123)
        self.assertEqual(entry["eval_count"], 45)
        self.assertEqual(entry["total_duration_ms"], 2000.0)
        self.assertEqual(entry["load_duration_ms"], 300.0)
        self.assertEqual(entry["prompt_eval_duration_ms"], 500.0)
        self.assertEqual(entry["eval_duration_ms"], 1200.0)
        self.assertTrue(entry["success"])
        self.assertIsNone(entry["error_type"])
        self.assertIn("timestamp", entry)

    def test_timeout_logs_failure_without_raising(self):
        with patch("llm_advisor.requests.post", side_effect=requests.exceptions.Timeout()):
            result = llm_advisor.explain_alert(
                SAMPLE_ALERT, "sme_startup", {"data_categories": []}
            )

        with open(self.usage_log_path, encoding="utf-8") as log_file:
            entry = json.loads(log_file.readline())

        self.assertFalse(result["available"])
        self.assertFalse(entry["success"])
        self.assertEqual(entry["error_type"], "Timeout")
        self.assertEqual(entry["alert_id"], 42)

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
