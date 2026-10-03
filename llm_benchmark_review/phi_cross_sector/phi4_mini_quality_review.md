# Phi-4-mini Manual Quality Review

> Historical benchmark: captured before `phi4-mini` became the default; these results document the model comparison that informed that decision, not current default behavior.

## Scope and Rubric

This review covers all 15 `phi4-mini` responses in the most recent `llm_benchmark_outputs.json` run, paired by alert ID with the actual records returned by `model.get_recent_alerts()` from the configured database. All 15 records are from the active `uav_disaster_response` sector. The allow-list standard is the advisor's named InSighter sources: the alert's own fields, session/logon events, file access events, device/USB events, email metadata, and the InSighter audit trail. An asserted source outside that list is treated as unsupported unless it appears in that alert's input fields.

Scores are manual judgments, not ground-truth labels: 1 = materially wrong or unusable; 2 = major factual or practical problems; 3 = mixed, with useful content but a notable gap; 4 = strong with minor omissions; 5 = accurate, clear, specifically useful, and well-matched to the criterion.

## Aggregate Scores

| Criterion | Mean / 5 | Median / 5 |
|---|---:|---:|
| Accuracy | 3.60 | 4 |
| Clarity | 3.87 | 4 |
| Actionability | 3.73 | 4 |
| Consistency | 4.00 | 4 |

Consistency is assessed against Phi's other 14 responses in this same run. The repeated short explanation/recommendation structure is stable; scores are lower where that consistency does not compensate for a response-specific factual or action problem.

## Alert-by-Alert Review

### Alert 5186 - Total 15/20

**Actual alert:** Sector `uav_disaster_response`; user `drone_operator_04`; role `drone_operator`; severity `MEDIUM`; description `unusual activity pattern`; login hour 6; 0 files; 16.75 MB transferred; 1 failed login; flagged by `IF + OC-SVM`; IF 88.2, OC-SVM 98.8, final score 96.3.

**Generated response:**

```json
{
  "explanation": "A medium-severity alert for drone operator 04 was triggered by unusual activity patterns including a single failed login, no file access, and 16.75 MB of data transfer during an off-hour session at 6 AM.",
  "recommendation": "Confirm the failed login with drone operator 04's supervisor to investigate the anomaly."
}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 3 | The user, severity, failed login, zero files, and transfer amount match, but 6 AM is not identified as off-hours by the alert's rule-derived description. |
| Clarity | 4 | The short sentences use familiar terms and make the event understandable without explaining model scores. |
| Actionability | 4 | Confirming the one failed login with the user's supervisor is a concrete check tied to this alert. |
| Consistency | 4 | It keeps Phi's usual concise explanation-plus-recommendation format, with slightly more field detail than several peers. |

### Alert 5180 - Total 15/20

**Actual alert:** Sector `uav_disaster_response`; user `drone_operator_05`; role `drone_operator`; severity `CRITICAL`; description `off-hours mission-console access, accessed 50 disaster survivor records, transferred 697 MB of triage data`; login hour 4; 50 files; 696.71 MB transferred; 3 failed logins; flagged by `IF`; IF 100.0, OC-SVM 98.7, final score 100.0.

**Generated response:**

```json
{
  "explanation": "A drone operator performed a critical off-hours operation, accessed a large number of disaster survivor records, and transferred a significant amount of data.",
  "recommendation": "Confirm the off-hours mission with the drone operator's supervisor."
}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 4 | The critical severity, off-hours access, records, and transfer are represented correctly, though the user and exact values are omitted. |
| Clarity | 4 | The wording is plain and brief, and its one main concern is easy to identify. |
| Actionability | 3 | Supervisor confirmation is relevant, but the response does not specify which records or transfer details to verify. |
| Consistency | 4 | Its neutral two-field structure and short length match Phi's other responses. |

### Alert 5177 - Total 15/20

**Actual alert:** Sector `uav_disaster_response`; user `drone_operator_01`; role `drone_operator`; severity `CRITICAL`; description `accessed 91 disaster survivor records, transferred 605 MB of triage data`; login hour 21; 91 files; 605.0 MB transferred; 3 failed logins; flagged by `IF + OC-SVM`; IF 90.5, OC-SVM 100.0, final score 100.0.

**Generated response:**

```json
{
  "explanation": "Drone operator 01 logged in at an unusual hour (21) and accessed a high number of disaster survivor records, with a critical risk score indicating a potential security concern.",
  "recommendation": "Confirm the unusual login time with drone operator 01's supervisor."
}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 4 | The user, 21:00 login, high record access, critical alert, and high score are supported, though the large transfer and failed logins are omitted. |
| Clarity | 4 | The explanation is direct and readable, with no specialist terminology beyond the familiar alert concept. |
| Actionability | 3 | It asks about a specific login time, but does not address the alert's most consequential facts: 91 records and 605 MB. |
| Consistency | 4 | The response follows the same concise, neutral pattern as Phi's other alert analyses. |

### Alert 5169 - Total 16/20

**Actual alert:** Sector `uav_disaster_response`; user `drone_operator_04`; role `drone_operator`; severity `HIGH`; description `off-hours mission-console access, accessed 83 disaster survivor records, transferred 374 MB of triage data, 4 mission-control authentication attempts`; login hour 0; 83 files; 373.54 MB transferred; 4 failed logins; flagged by `IF`; IF 100.0, OC-SVM 97.2, final score 100.0.

**Generated response:**

```json
{
  "explanation": "A drone operator accessed mission-console and disaster data off-hours, transferred 374 MB of data, and had multiple failed login attempts.",
  "recommendation": "Confirm the activity by reviewing the InSighter audit trail for the drone operator's session/logon events and file access events."
}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 4 | The off-hours activity, rounded transfer, and multiple failures match, although severity, exact user, and record count are left out. |
| Clarity | 4 | The explanation is understandable, and the named log sources give the recommendation a clear meaning. |
| Actionability | 5 | It directs the analyst to the alert-relevant audit, session/logon, and file-access sources explicitly present in the allow-list. |
| Consistency | 4 | It retains Phi's short two-part format while being somewhat more operationally specific than average. |

### Alert 5168 - Total 15/20

**Actual alert:** Sector `uav_disaster_response`; user `drone_operator_03`; role `drone_operator`; severity `MEDIUM`; description `accessed 41 disaster survivor records, transferred 381 MB of triage data, 4 mission-control authentication attempts`; login hour 21; 41 files; 381.04 MB transferred; 4 failed logins; flagged by `IF`; IF 100.0, OC-SVM 72.5, final score 97.8.

**Generated response:**

```json
{
  "explanation": "The drone operator accessed numerous disaster survivor records and transferred a large amount of data, coupled with multiple failed login attempts.",
  "recommendation": "Confirm the activity and check related device/USB events and session/logon records with the drone operator."
}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 4 | The record access, transfer, and repeated authentication failures are accurate, though the severity and exact quantities are omitted. |
| Clarity | 4 | The explanation and follow-up are short and understandable to IT staff. |
| Actionability | 3 | Session/logon review is relevant, but the USB check is not prompted by any device activity in this alert. |
| Consistency | 4 | The two-field response and calm tone are consistent with Phi's other outputs. |

### Alert 5162 - Total 15/20

**Actual alert:** Sector `uav_disaster_response`; user `drone_operator_06`; role `drone_operator`; severity `CRITICAL`; description `accessed 89 disaster survivor records, transferred 633 MB of triage data`; login hour 21; 89 files; 632.88 MB transferred; 3 failed logins; flagged by `IF + OC-SVM`; IF 94.8, OC-SVM 100.0, final score 100.0.

**Generated response:**

```json
{
  "explanation": "The drone operator accessed a large number of disaster survivor records and transferred a significant amount of triage data, which is unusual for their role.",
  "recommendation": "Review the drone operator's session logs for unusual activity patterns and consult with an authorized supervisor to confirm the necessity of the data accessed and transferred."
}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 3 | The records and transfer are accurately summarized, but claiming the behavior is unusual for this role is not established by the alert fields. |
| Clarity | 4 | The sentences are plain and clearly distinguish the observed activity from the suggested review. |
| Actionability | 4 | Reviewing session logs and confirming the specific data transfer with a supervisor are useful next checks. |
| Consistency | 4 | The answer matches Phi's usual neutral tone and compact explanation/recommendation structure. |

### Alert 5160 - Total 17/20

**Actual alert:** Sector `uav_disaster_response`; user `drone_operator_03`; role `drone_operator`; severity `MEDIUM`; description `off-hours mission-console access, accessed 46 disaster survivor records, transferred 282 MB of triage data, 8 mission-control authentication attempts`; login hour 23; 46 files; 281.87 MB transferred; 8 failed logins; flagged by `IF + OC-SVM`; IF 100.0, OC-SVM 100.0, final score 100.0.

**Generated response:**

```json
{
  "explanation": "A medium-severity alert was triggered for a drone operator who accessed the mission-console off-hours and transferred large amounts of triage data.",
  "recommendation": "Review the drone operator's access and transfer logs for the specified hours and verify the necessity and authorization of the actions."
}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 4 | The severity, off-hours access, and data transfer are right, though the 46 records and 8 failed logins are omitted. |
| Clarity | 4 | The explanation uses straightforward language and identifies the core concern. |
| Actionability | 5 | It names the access/transfer records, time window, and authorization question specific to this alert. |
| Consistency | 4 | Its length, tone, and two-part format align with the rest of Phi's set. |

### Alert 5155 - Total 13/20

**Actual alert:** Sector `uav_disaster_response`; user `drone_operator_05`; role `drone_operator`; severity `MEDIUM`; description `unusual activity pattern`; login hour 10; 7 files; 5.36 MB transferred; 1 failed login; flagged by `IF + OC-SVM`; IF 63.9, OC-SVM 100.0, final score 81.8.

**Generated response:**

```json
{
  "explanation": "A medium-severity alert was triggered for drone_operator_05 due to unusual activity patterns, including accessing multiple files and transferring significant data during an off-hour login.",
  "recommendation": "Confirm the unusual activity with drone_operator_05's supervisor and check related authentication or access records for discrepancies."
}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 2 | Seven files and the transfer are real, but 10 AM is mislabeled off-hours and 5.36 MB is overstated as significant. |
| Clarity | 3 | The sentence is readable, but the incorrect off-hours label makes the event harder to interpret correctly. |
| Actionability | 4 | Supervisor confirmation and checking authentication/access records are concrete and relevant to the flagged pattern. |
| Consistency | 4 | Despite its factual mistakes, it uses the same concise, neutral structure as the other Phi responses. |

### Alert 5140 - Total 12/20 - Lowest-Scoring Response

**Actual alert:** Sector `uav_disaster_response`; user `drone_operator_01`; role `drone_operator`; severity `MEDIUM`; description `unusual activity pattern`; login hour 10; 0 files; 7.38 MB transferred; 1 failed login; flagged by `IF + OC-SVM`; IF 100.0, OC-SVM 98.8, final score 100.0.

**Generated response:**

```json
{
  "explanation": "A drone operator accessed disaster response data at an unusual hour with one failed login and transferred a significant amount of data, suggesting potential misuse.",
  "recommendation": "Review the drone platform access and disaster data transfer logs for this user to confirm the context and necessity of the data transfer."
}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 2 | It calls 10 AM unusual, says data was accessed despite zero files, overstates 7.38 MB, and implies potential misuse without evidence. |
| Clarity | 4 | The prose is easy to read, even though its claims are not dependable. |
| Actionability | 2 | The log review is specific but depends on an unlisted drone-platform source and is partly aimed at a claim the alert does not support. |
| Consistency | 4 | Its two-field format and neutral analyst voice are in line with the other 14 responses. |

### Alert 5137 - Total 14/20

**Actual alert:** Sector `uav_disaster_response`; user `drone_operator_02`; role `drone_operator`; severity `MEDIUM`; description `accessed 54 disaster survivor records, transferred 291 MB of triage data`; login hour 21; 54 files; 290.62 MB transferred; 3 failed logins; flagged by `IF`; IF 96.1, OC-SVM 86.2, final score 100.0.

**Generated response:**

```json
{
  "explanation": "Drone operator 02 accessed sensitive survivor records and transferred large volumes of triage data outside normal hours. Multiple failed logins and high anomaly scores indicate unusual behavior.",
  "recommendation": "Confirm the user's credentials with an authorized supervisor and investigate the purpose of accessing and transferring the data."
}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 3 | The user, records, transfer, failures, and scores are supported, but the alert does not classify 21:00 as outside normal hours. |
| Clarity | 4 | The wording is plain and the alert's main concern is easy to follow. |
| Actionability | 3 | Supervisor confirmation is relevant, but "investigate the purpose" does not specify a record or event to check. |
| Consistency | 4 | It follows the same brief explanation/recommendation form and measured tone as Phi's other outputs. |

### Alert 5136 - Total 15/20

**Actual alert:** Sector `uav_disaster_response`; user `drone_operator_03`; role `drone_operator`; severity `MEDIUM`; description `unusual activity pattern`; login hour 6; 11 files; 77.0 MB transferred; 1 failed login; flagged by `IF + OC-SVM`; IF 36.4, OC-SVM 98.8, final score 63.6.

**Generated response:**

```json
{
  "explanation": "A medium-severity alert was triggered due to unusual login patterns, including an unusual login hour, multiple files accessed, and data transfer beyond the norm for drone_operator_03.",
  "recommendation": "Confirm the unusual login hour with authorized supervisor and verify recent file access and data transfer."
}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 3 | The severity and event types match, but 6 AM is not identified as off-hours here and a personal "norm" is not included in the alert. |
| Clarity | 4 | The explanation is compact and its next step is readily understandable. |
| Actionability | 4 | It directs the analyst to verify the login time, file access, and transfer associated with this alert. |
| Consistency | 4 | Its length and neutral two-field structure are similar to the other Phi responses. |

### Alert 5130 - Total 17/20

**Actual alert:** Sector `uav_disaster_response`; user `drone_operator_02`; role `drone_operator`; severity `HIGH`; description `off-hours mission-console access, accessed 62 disaster survivor records, transferred 522 MB of triage data`; login hour 23; 62 files; 521.94 MB transferred; 3 failed logins; flagged by `IF + OC-SVM`; IF 90.2, OC-SVM 98.8, final score 100.0.

**Generated response:**

```json
{
  "explanation": "High-severity alert triggered for an operator working off-hours on a disaster response drone platform, accessing 62 records and transferring large amounts of sensitive data.",
  "recommendation": "Confirm the operator's activity with a supervisor and review the corresponding session/logon and device/USB events for anomalies."
}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 5 | The severity, off-hours timing, 62 records, and substantial transfer are all supported by the alert, with no unsupported incident claim. |
| Clarity | 4 | It is concise and understandable, though session/logon and device/USB are somewhat technical labels. |
| Actionability | 4 | Supervisor confirmation plus review of named event sources gives a specific follow-up, although the USB check is not indicated by this alert. |
| Consistency | 4 | The response matches the stable short, neutral format Phi uses throughout the set. |

### Alert 5105 - Total 16/20

**Actual alert:** Sector `uav_disaster_response`; user `drone_operator_06`; role `drone_operator`; severity `CRITICAL`; description `off-hours mission-console access, accessed 83 disaster survivor records, transferred 810 MB of triage data, 8 mission-control authentication attempts`; login hour 22; 83 files; 810.37 MB transferred; 8 failed logins; flagged by `IF + OC-SVM`; IF 93.6, OC-SVM 100.0, final score 100.0.

**Generated response:**

```json
{
  "explanation": "The drone operator accessed sensitive disaster survivor records and large amounts of data during off-hours, with a high number of failed logins.",
  "recommendation": "Confirm the mission-console access and data transfer with an authorized supervisor."
}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 4 | The off-hours access, records, large transfer, and many failures are correct, though severity and exact counts are omitted. |
| Clarity | 4 | The short explanation and instruction are easy for an IT staff member to understand. |
| Actionability | 4 | It asks the analyst to verify the particular mission-console access and transfer with an authorized supervisor. |
| Consistency | 4 | It has the same concise, restrained tone and structure as Phi's other responses. |

### Alert 5101 - Total 14/20

**Actual alert:** Sector `uav_disaster_response`; user `drone_operator_03`; role `drone_operator`; severity `MEDIUM`; description `4 mission-control authentication attempts`; login hour 21; 35 files; 184.84 MB transferred; 4 failed logins; flagged by `IF`; IF 100.0, OC-SVM 86.3, final score 100.0.

**Generated response:**

```json
{
  "explanation": "Medium-severity alert triggered by unusual login attempts and data transfer from a drone operator, possibly indicative of anomalous behavior or unauthorized activity.",
  "recommendation": "Confirm the drone operator's login credentials and access records."
}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 4 | The medium severity, authentication attempts, and transfer are supported, and unauthorized activity is presented as a possibility rather than a fact. |
| Clarity | 3 | The wording is understandable but relies on abstract phrases like "anomalous behavior" instead of naming the event details. |
| Actionability | 3 | Confirming credentials and access records is relevant but does not specify the four attempts or the files/transfer to review. |
| Consistency | 4 | Its short explanation and one-step recommendation match the other Phi responses in tone and structure. |

### Alert 5093 - Total 18/20 - Highest-Scoring Response

**Actual alert:** Sector `uav_disaster_response`; user `drone_operator_01`; role `drone_operator`; severity `CRITICAL`; description `off-hours mission-console access, accessed 99 disaster survivor records, transferred 282 MB of triage data, 8 mission-control authentication attempts`; login hour 2; 99 files; 282.21 MB transferred; 8 failed logins; flagged by `IF`; IF 97.3, OC-SVM 97.9, final score 100.0.

**Generated response:**

```json
{
  "explanation": "The drone operator accessed mission-console during off-hours, viewed numerous disaster survivor records, and transferred large amounts of triage data. Multiple failed login attempts were detected.",
  "recommendation": "Confirm the purpose and authorization of the off-hours access and data transfer with the drone operator and an authorized supervisor."
}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 5 | Off-hours access, record activity, transfer, and repeated failed attempts all match the alert, and no additional data source or cause is invented. |
| Clarity | 4 | The explanation is concise and understandable, with only the domain term "mission-console" requiring familiarity. |
| Actionability | 5 | It asks the user and an authorized supervisor to confirm the purpose and authorization of the exact off-hours access and transfer. |
| Consistency | 4 | It uses the same restrained two-field format and level of detail as the rest of Phi's set. |

## Conclusion

Phi's responses are consistently concise and usually provide a usable first investigative step, but the 3.60 accuracy mean reflects meaningful field-level mistakes: alert `5155` calls a 10 AM login off-hours and overstates 5.36 MB, while alert `5140` calls 10 AM unusual, says data was accessed despite zero files, overstates 7.38 MB, and recommends an unlisted drone-platform log source. The validator did not reject that alert 5140 wording, so its source-term checks are not a complete semantic enforcement of the allow-list. Phi is the strongest response in this set for clarity and consistent style, but these 15 UAV-only examples are insufficient to establish general accuracy or cross-sector behavior.
