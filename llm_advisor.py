"""
- Explain the alert using only facts present in the supplied data. Do not invent \
- Treat anomaly scores as triage signals, not proof of compromise, malicious intent, \
Design constraints:
- Do not refer to cameras, video, drone flights, communications, or other systems \
  - No new pip dependency: talks to Ollama's REST API directly with `requests`
- Give one specific, proportionate next investigative action based on available alert \
    (already in requirements.txt).
  - Never raises out to the caller: any failure (Ollama not running, model not
- Do not recommend suspending or revoking access, locking accounts, resetting \
    pulled, malformed response, timeout) returns a result dict with
    available=False and a human-readable reason, so the API/UI can degrade
    gracefully instead of erroring.
- Keep the explanation to one or two concise sentences and the recommendation to one \
  - Results are meant to be cached by the caller (see app.py) keyed by alert
    id, since the underlying log row never changes once written.
"""

import json
import os
import re
from datetime import datetime, timezone

import requests

OLLAMA_HOST = os.environ.get("INSIGHTER_OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get(
    "INSIGHTER_LLM_MODEL",
    os.environ.get("INSIGHTER_OLLAMA_MODEL", "phi4-mini"),
)
USAGE_LOG_PATH = os.environ.get(
    "INSIGHTER_LLM_USAGE_LOG_PATH",
    os.path.join(os.path.dirname(__file__), "llm_usage_log.jsonl"),
)
# Phi-4-mini's measured maximum was 12.3s; 30s leaves over 2x cold-start margin
# while avoiding a 90s wait on a genuine failure. Override for slower hardware or
# benchmarked models with longer cold starts; AI Analysis runs in a background thread.
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("INSIGHTER_OLLAMA_TIMEOUT_SECONDS", "30"))

ALLOWED_DATA_SOURCES = (
    "the alert's own fields",
    "session/logon events",
    "file access events",
    "device/USB events",
    "email metadata",
    "the InSighter audit trail",
)
UNSUPPORTED_DATA_SOURCE_TERMS = (
    "flight log",
    "gps",
    "camera footage",
    "drone telemetry",
    "drone flight",
    "drone mission log",
    "drone platform access log",
    "mission log",
)
MONITORED_CATEGORY_POLICY = (
    "Sector monitored data-category names describe scope only. They are labels, not "
    "evidence that a specific alert involved that category. The event's data_category "
    "field alone identifies the category for that event; do not infer it from the "
    "sector, role, or sector-wide category list. If the event field is absent or "
    "unknown, use generic file/data wording. A category label is not itself a "
    "checkable log, event stream, or system."
)
RISK_CONCLUSION_POLICY = (
    "Repeat the alert's severity label verbatim and report its computed scores as given. "
    "If severity is CRITICAL, say CRITICAL, not 'high risk' or 'elevated.' Describe risk "
    "only with the alert's literal severity and computed scores. A score "
    "is an anomaly indicator, not proof of intent or wrongdoing. Never conclude or "
    "imply insider threat, malicious intent/activity, compromise, exfiltration, fraud, "
    "theft, sabotage, or unauthorized access/activity. Do not upgrade MEDIUM/LOW "
    "severity to high/critical risk; report the supplied label and number as written. "
    "A specific concern may be framed as something to verify, not as an established fact."
)

SYSTEM_PROMPT = f"""You are a security analyst assistant embedded in an insider-threat \
monitoring dashboard called InSighter. You will be given structured data about one \
flagged alert: the user's role, the organizational sector, which behavioral signals \
triggered the flag, and the anomaly detection scores.

Respond with a short, plain-language explanation an IT administrator (not a data \
scientist) can read in a few seconds, plus one concrete, actionable recommendation \
for what to do next.

Rules:
- {RISK_CONCLUSION_POLICY}
- Correct: "The alert severity is MEDIUM and the final anomaly score is 100." Incorrect: \
"The activity indicates a potential insider threat or malicious intent."
- Severity wording example: Correct: "The alert severity is CRITICAL." Incorrect: \
"The alert is high risk" when the supplied severity is CRITICAL.
- Explain the alert using only facts present in the supplied data. Do not invent \
events, counts, times, intent, policies, or supporting evidence. A role or anomaly \
score alone does not establish what is normal for that user or prove wrongdoing.
- Treat anomaly scores as triage signals, not proof of compromise, malicious intent, \
or data exfiltration. State uncertainty where the alert does not establish a cause.
- Use the supplied severity and computed scores as the only basis for risk language. \
Do not independently label activity as suspicious, misuse, compromise, or unauthorized \
based only on a pattern or low data volume.
- For any claim that login timing is off-hours or unusual, use only the supplied \
off_hours_access classification. If it is false/0, do not call the login hour, timing, \
or session off-hours, unusual, abnormal, or outside normal hours; never infer this from \
login_hour or assumptions about a typical work schedule. A false flag means only that \
the system did not classify the event as off-hours; do not relabel it as regular, \
business, working, daytime, evening, or nighttime activity.
- Do not refer to cameras, video, drone flights, communications, or other systems \
unless the supplied data explicitly mentions them; they may not be available.
- Give one specific, proportionate next investigative action based on available alert \
data, such as checking related authentication or access records and confirming \
unexplained activity with an authorized supervisor.
- Follow-up actions may refer ONLY to these InSighter data sources: {', '.join(ALLOWED_DATA_SOURCES)}. \
Do not name, imply, or request data from any source outside this list. If no listed \
source applies, recommend checking the alert's own fields or confirming the activity \
with an authorized supervisor.
- {MONITORED_CATEGORY_POLICY}
- Do not recommend suspending or revoking access, locking accounts, resetting \
credentials, or taking disciplinary action based on an anomaly score alone. Recommend \
those steps only when the supplied facts establish immediate compromise, and direct \
the administrator to follow incident-response policy.
- Keep the explanation to one or two concise sentences and the recommendation to one \
concise action. Do not repeat the same facts in both fields.
- Respond with ONLY a JSON object, no other text, no markdown code fences, in \
exactly this shape:
{{"explanation": "...", "recommendation": "..."}}
"""


def _build_user_prompt(alert, sector, sector_config):
    classification = str(alert.get("off_hours_access")).strip().casefold()
    login_hour = alert.get("login_hour")
    event_category = str(alert.get("data_category") or "").strip()
    if event_category:
        category_context = (
            f"EVENT DATA CATEGORY: {event_category}. This row-level tag is the only "
            "evidence for naming a specific category in this alert. Do not infer other "
            "categories from the sector list. Correct: 'This event is tagged "
            f"{event_category}.' Incorrect: claim that it involved another sector "
            "category.\n"
        )
    else:
        category_context = (
            "EVENT DATA CATEGORY: unavailable. Do not name or infer a specific data "
            "category for this alert.\n"
        )
    if classification in {"1", "true", "yes"}:
        timing_context = (
            "SYSTEM TIMING DECISION: OFF-HOURS (off_hours_access=1). You may describe "
            f"this login as off-hours. Login hour: {login_hour} on a 24-hour clock.\n"
            "Correct timing example: 'The system classified this login as off-hours.'\n"
        )
    elif classification in {"0", "false", "no"}:
        timing_context = (
            "SYSTEM TIMING DECISION: NOT CLASSIFIED OFF-HOURS (off_hours_access=0). "
            "Do not mention the login time or describe timing as regular, normal, "
            "business-hours, unusual, late, early, or abnormal. Omit timing completely. "
            "You may describe other unusual activity, but not the timing.\n"
            "Correct: 'The alert shows unusual file activity.' Incorrect: 'The login "
            "was at an unusual hour' or 'occurred during regular working hours.'\n"
        )
    else:
        timing_context = (
            "SYSTEM TIMING DECISION: unavailable. Do not mention the login time or "
            "infer or describe a work schedule.\n"
        )

    return (
        f"{timing_context}"
        f"{category_context}"
        f"Sector: {sector}\n"
        f"Sector monitored data categories (scope labels only): "
        f"{', '.join(sector_config.get('data_categories', []))}\n"
        f"User: {alert.get('user')}\n"
        f"Role: {alert.get('role')}\n"
        f"Severity: {alert.get('severity')}\n"
        f"Flagged by: {alert.get('flagged_by')}\n"
        f"Rule-based summary: {alert.get('description')}\n"
        f"Event data category field: {event_category or 'unavailable'}\n"
        f"Files accessed: {alert.get('files_accessed')}\n"
        f"Data transferred (MB): {alert.get('data_transferred_mb')}\n"
        f"Failed logins: {alert.get('failed_logins')}\n"
        f"Isolation Forest score: {alert.get('if_score')}\n"
        f"One-Class SVM score: {alert.get('ocsvm_score')}\n"
        f"Blended risk score: {alert.get('final_score')}\n"
    )


def _unavailable(reason):
    return {"available": False, "reason": reason, "explanation": None, "recommendation": None}


def _log_usage(alert, success, payload=None, error_type=None):
    """Append usage metrics without allowing logging errors to affect callers."""
    try:
        payload = payload or {}

        def duration_ms(field):
            value = payload.get(field)
            return round(value / 1_000_000, 3) if isinstance(value, (int, float)) else None

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": OLLAMA_MODEL,
            "alert_id": alert.get("id"),
            "prompt_eval_count": payload.get("prompt_eval_count"),
            "eval_count": payload.get("eval_count"),
            "total_duration_ms": duration_ms("total_duration"),
            "load_duration_ms": duration_ms("load_duration"),
            "prompt_eval_duration_ms": duration_ms("prompt_eval_duration"),
            "eval_duration_ms": duration_ms("eval_duration"),
            "success": success,
            "error_type": error_type,
        }
        with open(USAGE_LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _unsupported_data_sources(text, alert, sector_config):
    alert_fields = json.dumps(alert, ensure_ascii=False).casefold()
    response_text = str(text).casefold()
    unsupported = [
        term for term in UNSUPPORTED_DATA_SOURCE_TERMS
        if term.casefold() in response_text and term.casefold() not in alert_fields
    ]
    source_cue = re.compile(
        r"\b(?:logs?|events?|records?|history|feeds?|streams?|systems?|consoles?|"
        r"audit trails?|databases?|data sources?)\b"
    )
    category_source_after = re.compile(
        r"^\s*(?:and\s+)?(?:category\s+)?"
        r"(?:logs?|databases?|systems?|feeds?|streams?|consoles?|data sources?)\b"
    )
    action_cue = re.compile(
        r"\b(?:review|check|inspect|search|query|look at|examine|retrieve|pull|audit)\b"
        r"[^.!?;]{0,48}$"
    )
    category_claim_cue = re.compile(
        r"\b(?:access(?:ed|ing)?|view(?:ed|ing)?|open(?:ed|ing)?|contain(?:s|ed)?|"
        r"involv(?:e|es|ed|ing)|expos(?:e|es|ed|ing)|transfer(?:red|ring)?|"
        r"leak(?:s|ed|ing)?)\b"
    )
    event_category = str(alert.get("data_category") or "").strip().casefold()
    description = str(alert.get("description") or "").casefold()
    for category in sector_config.get("data_categories", ()):
        words = [word for word in re.split(r"[^a-z0-9]+", str(category).casefold()) if word]
        if not words:
            continue
        word_patterns = [
            re.escape(word[:-1]) + r"s?" if word.endswith("s") and len(word) > 3
            else re.escape(word)
            for word in words
        ]
        category_pattern = re.compile(
            r"(?<!\w)" + r"[\s_-]+".join(word_patterns) + r"(?!\w)"
        )
        for match in category_pattern.finditer(response_text):
            before = response_text[max(0, match.start() - 64):match.start()]
            after = response_text[match.end():].split(".", 1)[0].split(";", 1)[0]
            context = response_text[max(0, match.start() - 64):match.end() + 64]
            if re.match(r"\s*(?:category|scope|label)\b", after) or re.search(
                r"\b(?:monitored|scope|sector-wide)\s+category\b", after
            ):
                continue
            normalized_category = " ".join(words)
            category_is_supported = (
                " ".join(re.split(r"[^a-z0-9]+", event_category)) == normalized_category
                or category_pattern.search(description) is not None
            )
            if category_is_supported:
                if category_source_after.search(after):
                    unsupported.append(f"monitored category used as a source: {category}")
                    break
                continue
            if source_cue.search(after) or action_cue.search(before) or category_claim_cue.search(context):
                unsupported.append(f"monitored category used as a source: {category}")
                break
    return unsupported


def _unsupported_risk_conclusions(text, alert):
    response_text = str(text).casefold()
    severity = str(alert.get("severity") or "").strip().upper()
    disallowed_conclusions = re.compile(
        r"\b(?:insider\s+threat|malicious(?:\s+(?:intent|activity|behavior))?|"
        r"malice|(?:user|account|system)?\s*compromis(?:e|ed|ing)|"
        r"data\s+exfiltration|exfiltrat(?:e|ed|ing)|fraud(?:ulent)?|"
        r"sabotage|data\s+theft|theft)\b"
    )
    unsupported_unauthorized = re.compile(
        r"\bunauthori[sz]ed\s+(?:access|activity|transfer|changes?|behavior)\b"
    )
    investigative_frame = re.compile(
        r"\b(?:review|check|investigate|verify|confirm|determine|establish)\b"
        r"[^.!?;]{0,64}\b(?:whether|if|for|any)\b[^.!?;]{0,48}$"
    )
    unsupported_risk_level = re.compile(r"\b(?:high|critical)\s+risk\b")

    for sentence in re.split(r"(?<=[.!?])\s+", response_text):
        if disallowed_conclusions.search(sentence):
            return True
        if unsupported_unauthorized.search(sentence) and not investigative_frame.search(sentence):
            return True
        if unsupported_risk_level.search(sentence) and severity not in {"HIGH", "CRITICAL"}:
            return True
    return False


def _mismatched_severity_language(text, alert):
    severity = str(alert.get("severity") or "").strip().upper()
    if not severity:
        return False
    severity_words = {
        "LOW": "LOW",
        "MEDIUM": "MEDIUM",
        "MODERATE": "MEDIUM",
        "HIGH": "HIGH",
        "CRITICAL": "CRITICAL",
    }
    label_pattern = re.compile(
        r"\b(?P<label>low|medium|moderate|high|critical)[\s-]+"
        r"(?:risk|risk level|severity|severity level|alert|alert level)\b",
        re.IGNORECASE,
    )
    for match in label_pattern.finditer(str(text)):
        if severity_words[match.group("label").upper()] != severity:
            return True
    return False


def _claims_false_off_hours(text, alert):
    classification = alert.get("off_hours_access")
    if classification is None or str(classification).strip().casefold() not in {
        "0", "false", "no",
    }:
        return False

    off_hours_claim = re.compile(
        r"\b(?:off[\s-]?hours?|after[\s-]?hours?|outside(?: of)? "
        r"(?:normal|usual|regular|business|working|standard) hours?)\b"
    )
    regular_schedule_claim = re.compile(
        r"\b(?:normal|usual|regular|business|working|standard)"
        r"(?:\s+(?:business|working|standard))*\s+(?:login\s+)?hours\b"
    )
    unusual_time_claim = re.compile(
        r"\b(?:unusual|odd|abnormal|unexpected|late|early)\s+(?:login\s+)?"
        r"(?:hours?|times?|timings?)\b|"
        r"\b(?:hours?|times?|timings?)\b[^.!?;]{0,30}"
        r"\b(?:unusual|odd|abnormal|unexpected|late|early)\b"
    )
    negated_claim = re.compile(
        r"\b(?:no|not|never|isn't|wasn't|doesn't|didn't)\b[^.!?;]{0,24}"
        r"\b(?:off[\s-]?hours?|after[\s-]?hours?|unusual|odd|abnormal|unexpected)\b"
    )
    negated_after_claim = re.compile(
        r"\b(?:off[\s-]?hours?|after[\s-]?hours?|unusual|odd|abnormal|unexpected)\b"
        r"[^.!?;]{0,32}\b(?:not flagged|not classified|not detected|not identified|not established)\b"
    )
    inferred_schedule_claim = re.compile(
        r"\b(?:during|within|in)\s+(?:normal|usual|regular|business|working|standard)"
        r"(?:\s+(?:business|working|standard))*\s+hours\b|"
        r"\b(?:during the day|daytime|during the evening|in the evening|at night|overnight)\b"
    )
    positive_timing_context = re.compile(
        r"\b(?:during|within|outside(?: of)?|after|at)\s+(?:the\s+)?"
        r"(?:off[\s-]?hours?|after[\s-]?hours?|normal|usual|regular|business|working|standard)\b"
    )
    try:
        login_hour = int(alert.get("login_hour"))
    except (TypeError, ValueError):
        login_hour = None
    specific_hour_claim = None
    if login_hour is not None and 0 <= login_hour <= 23:
        hour_token = rf"(?<![\w.])0?{login_hour}(?::\d{{2}})?\s*(?:a\.?m\.?|p\.?m\.?)?(?!\w)"
        timing_descriptor = r"(?:unusual(?:ly)?|odd(?:ly)?|abnormal(?:ly)?|unexpected(?:ly)?|late|early|off[\s-]?hours?)"
        specific_hour_claim = re.compile(
            rf"{timing_descriptor}[^.!?;]{{0,20}}{hour_token}|"
            rf"{hour_token}[^.!?;]{{0,20}}{timing_descriptor}"
        )

    for sentence in re.split(r"(?<=[.!?])\s+", str(text).casefold()):
        negated_before = negated_claim.search(sentence)
        negated_after = negated_after_claim.search(sentence)
        if negated_before:
            continue
        if negated_after:
            if positive_timing_context.search(sentence):
                return True
            continue
        if (
            off_hours_claim.search(sentence)
            or regular_schedule_claim.search(sentence)
            or unusual_time_claim.search(sentence)
            or inferred_schedule_claim.search(sentence)
            or (specific_hour_claim and specific_hour_claim.search(sentence))
        ):
            return True
    return False


def explain_alert(alert, sector, sector_config):
    """Call the local Ollama model to explain one alert. Never raises."""
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "system": SYSTEM_PROMPT,
                "prompt": _build_user_prompt(alert, sector, sector_config),
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.ConnectionError:
        _log_usage(alert, success=False, error_type="ConnectionError")
        return _unavailable(f"Ollama not running at {OLLAMA_HOST}")
    except requests.exceptions.Timeout:
        _log_usage(alert, success=False, error_type="Timeout")
        return _unavailable("Ollama request timed out")
    except requests.exceptions.RequestException as exc:
        _log_usage(alert, success=False, error_type=type(exc).__name__)
        return _unavailable(f"Ollama request failed: {exc}")

    if response.status_code == 404:
        _log_usage(alert, success=False, error_type="HTTPStatusError")
        return _unavailable(f"Model '{OLLAMA_MODEL}' not found — run `ollama pull {OLLAMA_MODEL}`")
    if not response.ok:
        _log_usage(alert, success=False, error_type="HTTPStatusError")
        return _unavailable(f"Ollama returned HTTP {response.status_code}")

    try:
        payload = response.json()
        raw_text = payload["response"]
        parsed = json.loads(raw_text)
        explanation = parsed["explanation"]
        recommendation = parsed["recommendation"]
    except (KeyError, ValueError, TypeError):
        _log_usage(alert, success=False, payload=payload if "payload" in locals() else None,
                   error_type="InvalidResponseError")
        return _unavailable("Could not parse a response from the model")

    if _claims_false_off_hours(f"{explanation} {recommendation}", alert):
        _log_usage(alert, success=False, payload=payload, error_type="OffHoursClassificationMismatch")
        return _unavailable("Model response contradicted the system off-hours classification")

    unsupported_sources = _unsupported_data_sources(
        f"{explanation} {recommendation}", alert, sector_config
    )
    if unsupported_sources:
        _log_usage(alert, success=False, payload=payload, error_type="UnsupportedDataSourceError")
        return _unavailable("Model response referenced an unsupported data source")

    response_text = f"{explanation} {recommendation}"
    if (
        _unsupported_risk_conclusions(response_text, alert)
        or _mismatched_severity_language(response_text, alert)
    ):
        _log_usage(alert, success=False, payload=payload, error_type="UnsupportedRiskConclusionError")
        return _unavailable("Model response made an unsupported risk or severity claim")

    _log_usage(alert, success=True, payload=payload)
    return {
        "available": True,
        "reason": None,
        "explanation": explanation,
        "recommendation": recommendation,
    }
