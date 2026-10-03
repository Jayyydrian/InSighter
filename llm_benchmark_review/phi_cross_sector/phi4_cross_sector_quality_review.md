# Phi-4-mini Cross-Sector Manual Quality Review

> Historical benchmark: captured before `phi4-mini` became the default; these results document the model comparison that informed that decision, not current default behavior.

## Scope and Rubric

This review covers all 20 Phi-4-mini raw responses in `phi4_cross_sector_outputs.json`: 10 `private_school` alerts and 10 `sme_startup` alerts. The existing active database was UAV-only, so these two sets were generated in isolated temporary databases with InSighter's `generate_logs.py`, then processed using `model.get_recent_alerts()` and `explain_alert()`. The configured application database was not modified. These are actual model generations from real application code paths, but their source events are simulated rather than production incidents.

Scores are manual judgments, not ground-truth labels: 1 = materially wrong or unusable; 2 = major factual or practical problems; 3 = mixed, with a notable gap; 4 = strong with minor omissions; 5 = accurate, clear, specifically useful, and well-matched to the criterion. Consistency is relative to the other nine Phi responses in the same sector. Validator outcomes below are re-evaluated against the final validator; alert 1370's earlier saved `success` flag did not reflect the last validator extension, so the rechecked result is used.

## Aggregate Scores

| Sector / Sample | Accuracy mean / median | Clarity mean / median | Actionability mean / median | Consistency mean / median | Final validator accepted |
|---|---:|---:|---:|---:|---:|
| `private_school` (10) | 3.20 / 3 | 3.90 / 4 | 3.60 / 4 | 3.90 / 4 | 3/10 |
| `sme_startup` (10) | 4.10 / 4.5 | 4.00 / 4 | 3.80 / 4 | 3.60 / 4 | 6/10 |
| Combined (20) | 3.65 / 3.5 | 3.95 / 4 | 3.70 / 4 | 3.75 / 4 | 9/20 |

The validator recheck rejected 7 private-school and 4 SME outputs for timing claims when `off_hours_access=0`. It suppresses these outputs from normal advisor results, but raw generations still make the claims. Alert 1370's saved run flag was `success=True`, but replay through the final validator rejects its “despite regular login hours” claim; the acceptance aggregate above uses the replay result.

## Private School

### Alert 1440 - Total 17/20 - Validator accepted

**Actual alert:** Sector `private_school`; user `it_staff_02`; role `it_staff`; severity `CRITICAL`; description `off-hours login, accessed 118 files, transferred 791 MB, 5 failed logins`; login hour 5; `off_hours_access=1`; 118 files; 790.77 MB transferred; 5 failed logins; IF/OC-SVM/final scores 100.0/99.9/100.0.

**Generated response:**

```json
{"explanation":"Critical alert triggered for IT Staff 02 due to an off-hours login, access to 118 files, transfer of 791 MB, and 5 failed logins.","recommendation":"Confirm the unusual activity with IT Staff 02's supervisor."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 5 | It accurately reports the true off-hours classification and the file, transfer, and failed-login counts. |
| Clarity | 4 | The short explanation is easy to understand, though “critical alert” is not connected to its scoring basis. |
| Actionability | 4 | It asks for supervisor confirmation of this user's activity, though it does not name a particular record to inspect. |
| Consistency | 4 | It follows the same concise explanation-and-recommendation structure as the other Phi outputs. |

### Alert 1435 - Total 14/20 - Validator rejected

**Actual alert:** Sector `private_school`; user `intern_02`; role `intern`; severity `MEDIUM`; description `unusual activity pattern`; login hour 17; `off_hours_access=0`; 14 files; 39.64 MB transferred; 0 failed logins; IF/OC-SVM/final scores 46.0/100.0/72.1.

**Generated response:**

```json
{"explanation":"The intern's session shows unusual activity patterns such as high data transfer and access to multiple files. The system detected unusual behavior during regular working hours.","recommendation":"Confirm the activity with an authorized supervisor."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 3 | The qualitative file and transfer descriptions fit, but “regular working hours” is not an explicit schedule classification in the alert. |
| Clarity | 4 | The explanation is plain and concise. |
| Actionability | 3 | Supervisor confirmation is relevant but generic and does not specify what to verify. |
| Consistency | 4 | Its structure and restrained tone match Phi's usual response pattern. |

### Alert 1431 - Total 15/20 - Validator accepted

**Actual alert:** Sector `private_school`; user `researcher_03`; role `researcher`; severity `MEDIUM`; description `unusual activity pattern`; login hour 13; `off_hours_access=0`; 3 files; 1.50 MB transferred; 1 failed login; IF/OC-SVM/final scores 100.0/95.3/100.0.

**Generated response:**

```json
{"explanation":"Researcher_03's login at 13:00 was not flagged as off-hours, but there are unusual activity patterns detected with high risk scores.","recommendation":"Confirm Researcher_03's activity with an authorized supervisor to investigate the unusual activity."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 4 | The user, time classification, unusual-pattern alert, and high scores are supported, though event counts are omitted. |
| Clarity | 4 | The explanation is clear and distinguishes timing from the separate anomaly flag. |
| Actionability | 3 | Supervisor confirmation is appropriate but does not identify the one failed login or three files to review. |
| Consistency | 4 | It matches the stable two-field format and measured tone. |

### Alert 1418 - Total 15/20 - Validator accepted

**Actual alert:** Sector `private_school`; user `researcher_02`; role `researcher`; severity `MEDIUM`; description `unusual activity pattern`; login hour 13; `off_hours_access=0`; 1 file; 4.54 MB transferred; 1 failed login; IF/OC-SVM/final scores 99.5/89.0/100.0.

**Generated response:**

```json
{"explanation":"The researcher logged in at 13:00 (which is not classified as off-hours) and accessed one file, transferring 4.54 MB of data, with one failed login attempt. Anomaly scores are high, indicating unusual activity.","recommendation":"Confirm the researcher's login with an authorized supervisor to ensure legitimate activity."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 4 | The user activity, quantities, failed login, score characterization, and explicit timing caveat match the alert. |
| Clarity | 4 | The response lays out the event details in plain language. |
| Actionability | 3 | Supervisor confirmation is relevant but not as specific as checking the recorded file and authentication event. |
| Consistency | 4 | Its concise explanation and single recommendation fit the rest of the set. |

### Alert 1416 - Total 14/20 - Validator rejected

**Actual alert:** Sector `private_school`; user `it_staff_02`; role `it_staff`; severity `MEDIUM`; description `unusual activity pattern`; login hour 12; `off_hours_access=0`; 1 file; 3.58 MB transferred; 0 failed logins; IF/OC-SVM/final scores 95.9/100.0/100.0.

**Generated response:**

```json
{"explanation":"An unusual activity pattern was detected for an IT staff member during regular hours. Accessed private documents and transferred data in a private school setting.","recommendation":"Confirm the unusual activity with an authorized supervisor and review related access records."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 3 | It invents a “private documents” file type and labels the event regular-hours even though only the false off-hours flag is supplied. |
| Clarity | 4 | The sentences are understandable, although the fragmentary second sentence is awkward. |
| Actionability | 3 | Reviewing access records is relevant but does not identify the one file or 3.58 MB transfer. |
| Consistency | 4 | It retains the same short, neutral format as Phi's other responses. |

### Alert 1394 - Total 13/20 - Validator rejected

**Actual alert:** Sector `private_school`; user `researcher_02`; role `researcher`; severity `MEDIUM`; description `unusual activity pattern`; login hour 18; `off_hours_access=0`; 4 files; 2.11 MB transferred; 1 failed login; IF/OC-SVM/final scores 100.0/95.3/100.0.

**Generated response:**

```json
{"explanation":"Researcher_02 accessed files and transferred data during regular hours, with an unusual activity pattern flagged by the system. The researcher had one failed login attempt.","recommendation":"Verify the details of the failed login attempt and the files accessed during this session with an authorized supervisor."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 3 | The failed login and file activity match, but “regular hours” is unsupported by the false off-hours flag alone. |
| Clarity | 4 | The explanation is concise and easy to follow. |
| Actionability | 4 | It directs the reviewer to the specific failed login and file-access activity. |
| Consistency | 4 | Its tone and two-field structure are consistent with Phi's other answers. |

### Alert 1376 - Total 13/20 - Validator rejected

**Actual alert:** Sector `private_school`; user `intern_03`; role `intern`; severity `CRITICAL`; description `accessed 100 files, transferred 410 MB, 4 failed logins`; login hour 21; `off_hours_access=0`; 100 files; 410.11 MB transferred; 4 failed logins; IF/OC-SVM/final scores 100.0/100.0/100.0.

**Generated response:**

```json
{"explanation":"An intern with a critical alert flagged for accessing 100 files, transferring 410 MB, 4 failed logins during regular business hours.","recommendation":"Review the intern's access logs for the accessed files and transferred data to verify the activity."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 2 | Counts and severity match, but it labels 21:00 as regular business hours without that schedule being supplied. |
| Clarity | 3 | The explanation is understandable but grammatically awkward. |
| Actionability | 4 | It names the 100-file access and 410 MB transfer records to review. |
| Consistency | 4 | Despite awkward grammar, it uses the same compact explanation/recommendation pattern. |

### Alert 1370 - Total 13/20 - Validator rejected on final replay

**Actual alert:** Sector `private_school`; user `researcher_02`; role `researcher`; severity `MEDIUM`; description `unusual activity pattern`; login hour 9; `off_hours_access=0`; 15 files; 1.97 MB transferred; 1 failed login; IF/OC-SVM/final scores 100.0/100.0/100.0.

**Generated response:**

```json
{"explanation":"Researcher_02 has been flagged for unusual activity, including multiple file accesses, a large data transfer, and a failed login, despite regular login hours.","recommendation":"Review Researcher_02's access logs and recent file activities for any unusual patterns or potential data exfiltration."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 2 | It overstates 1.97 MB as “large” and adds a regular-hours characterization not established by the alert. |
| Clarity | 4 | The main event and proposed follow-up are easy to understand. |
| Actionability | 4 | It asks for review of this user's file/access records, although “potential data exfiltration” is more severe than the evidence. |
| Consistency | 3 | The exfiltration language is more escalatory than Phi's otherwise neutral style. |

### Alert 1367 - Total 15/20 - Validator rejected

**Actual alert:** Sector `private_school`; user `it_staff_01`; role `it_staff`; severity `MEDIUM`; description `unusual activity pattern`; login hour 9; `off_hours_access=0`; 1 file; 39.76 MB transferred; 0 failed logins; IF/OC-SVM/final scores 84.4/99.9/95.2.

**Generated response:**

```json
{"explanation":"An IT staff member accessed a private school's files during regular hours, transferred a substantial amount of data, and exhibited an unusual activity pattern with high anomaly scores.","recommendation":"Confirm the purpose of the file transfer with the IT staff member it_staff_01."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 3 | The role and transfer are reflected, but “regular hours” is unsupported by the classification and the specific values are omitted. |
| Clarity | 4 | Both the stated concern and follow-up are understandable. |
| Actionability | 4 | Asking about this user's 39.76 MB transfer is a concrete, alert-specific check. |
| Consistency | 4 | It maintains the set's concise, restrained style. |

### Alert 1365 - Total 15/20 - Validator rejected

**Actual alert:** Sector `private_school`; user `admin_staff_01`; role `admin_staff`; severity `MEDIUM`; description `unusual activity pattern`; login hour 17; `off_hours_access=0`; 15 files; 7.27 MB transferred; 1 failed login; IF/OC-SVM/final scores 49.7/100.0/75.5.

**Generated response:**

```json
{"explanation":"The admin_staff_01 user accessed multiple files, transferred a notable amount of data, and had one failed login while working during regular hours. Unusual activity was flagged based on this behavior and a high blended risk score.","recommendation":"Confirm the unusual activity with the user and check related authentication and access records."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 3 | File count and failed login are compatible with the alert, but “notable” overstates 7.27 MB and “regular hours” is not established. |
| Clarity | 4 | The explanation and recommendation are straightforward. |
| Actionability | 4 | It identifies authentication and access records relevant to this user's alert. |
| Consistency | 4 | It follows Phi's normal neutral, concise format. |

## SME & Startup

### Alert 2393 - Total 14/20 - Validator rejected

**Actual alert:** Sector `sme_startup`; user `hr_05`; role `hr`; severity `MEDIUM`; description `unusual activity pattern`; login hour 18; `off_hours_access=0`; 15 files; 31.13 MB transferred; 0 failed logins; IF/OC-SVM/final scores 11.3/100.0/50.8.

**Generated response:**

```json
{"explanation":"An HR employee logged into the system at unusual hours and accessed 15 files, transferring 31.13 MB of data. Anomalies were detected with a medium severity score.","recommendation":"Confirm the unusual login hour with HR supervisor and review the accessed files."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 3 | Counts match, but calling 18:00 an “unusual login hour” contradicts `off_hours_access=0`. |
| Clarity | 4 | The response is clear and identifies the observed data activity. |
| Actionability | 4 | It points to the 15 files and supervisor confirmation. |
| Consistency | 3 | The unsupported timing claim makes it less consistent with the flag-grounded style requested. |

### Alert 2378 - Total 16/20 - Validator accepted

**Actual alert:** Sector `sme_startup`; user `it_admin_04`; role `it_admin`; severity `CRITICAL`; description `off-hours login, accessed 68 files, transferred 746 MB, 10 failed logins`; login hour 1; `off_hours_access=1`; 68 files; 745.87 MB transferred; 10 failed logins; IF/OC-SVM/final scores 100.0/100.0/100.0.

**Generated response:**

```json
{"explanation":"An IT administrator's account accessed a large number of files and transferred significant data outside regular working hours, with multiple failed login attempts.","recommendation":"Confirm the activity with the IT administrator's authorized supervisor and check related authentication or access records."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 4 | Off-hours timing and all major activity types match, though exact quantities and severity are omitted. |
| Clarity | 4 | The risk pattern is expressed simply and coherently. |
| Actionability | 4 | It asks for confirmation and review of the relevant authentication/access records. |
| Consistency | 4 | Its concise, neutral format matches Phi's other responses. |

### Alert 2371 - Total 16/20 - Validator accepted

**Actual alert:** Sector `sme_startup`; user `hr_03`; role `hr`; severity `MEDIUM`; description `unusual activity pattern`; login hour 12; `off_hours_access=0`; 5 files; 1.63 MB transferred; 1 failed login; IF/OC-SVM/final scores 49.6/83.3/67.1.

**Generated response:**

```json
{"explanation":"An HR employee accessed 5 files, transferred 1.63 MB of data, had one failed login, and unusual activity was detected based on system scoring.","recommendation":"Confirm the HR employee's unusual activity with an authorized supervisor."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 5 | It correctly reports the user role, file/transfer/failure details, and describes anomaly detection without inventing timing or cause. |
| Clarity | 4 | It is plain and concise, although it does not explain why the event stands out. |
| Actionability | 3 | Supervisor confirmation is relevant but does not specify which record to verify. |
| Consistency | 4 | It follows Phi's usual explanation/recommendation structure. |

### Alert 2346 - Total 15/20 - Validator accepted

**Actual alert:** Sector `sme_startup`; user `finance_06`; role `finance`; severity `MEDIUM`; description `unusual activity pattern`; login hour 15; `off_hours_access=0`; 2 files; 1.83 MB transferred; 0 failed logins; IF/OC-SVM/final scores 87.3/96.5/95.4.

**Generated response:**

```json
{"explanation":"The finance department employee logged in at 15:00, accessed 2 files and transferred 1.83 MB of data. There were no failed login attempts. Anomaly detection scores indicate unusual activity, but no immediate compromise is evident.","recommendation":"Confirm the unusual activity with an authorized supervisor."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 5 | User role, hour, file/transfer amounts, absence of failed logins, and cautious risk wording align with the alert. |
| Clarity | 4 | It is easy to understand, though “anomaly detection” is mildly technical. |
| Actionability | 3 | It asks for confirmation but gives no alert-specific record or event to inspect. |
| Consistency | 3 | It is more explanatory and includes a compromise caveat unlike most of Phi's shorter answers. |

### Alert 2345 - Total 15/20 - Validator accepted

**Actual alert:** Sector `sme_startup`; user `finance_05`; role `finance`; severity `MEDIUM`; description `unusual activity pattern`; login hour 10; `off_hours_access=0`; 1 file; 39.14 MB transferred; 1 failed login; IF/OC-SVM/final scores 75.9/100.0/90.1.

**Generated response:**

```json
{"explanation":"The finance role user (finance_05) logged in at 10 AM, which is not classified as off-hours. The system flagged unusual activity patterns based on an anomaly detection score. The user accessed one file and transferred 39.14 MB, which could indicate unusual data transfer. Additionally, there was one failed login attempt.","recommendation":"Confirm the unusual activity with an authorized supervisor and review related authentication or access records."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 5 | It accurately states the explicit timing classification and the user, file, transfer, and failed-login facts without asserting wrongdoing. |
| Clarity | 4 | The explanation is clear but somewhat repetitive. |
| Actionability | 4 | It names authentication/access records and supervisor confirmation for the specific event. |
| Consistency | 3 | Its longer, repetitive explanation is less concise than most Phi responses. |

### Alert 2330 - Total 14/20 - Validator rejected

**Actual alert:** Sector `sme_startup`; user `hr_02`; role `hr`; severity `CRITICAL`; description `accessed 103 files, transferred 802 MB, 7 failed logins`; login hour 21; `off_hours_access=0`; 103 files; 802.04 MB transferred; 7 failed logins; IF/OC-SVM/final scores 100.0/100.0/100.0.

**Generated response:**

```json
{"explanation":"The HR employee accessed a large number of files, transferred significant data, and had multiple failed login attempts during working hours, as per InSighter's anomaly detection.","recommendation":"Verify the HR staff's login attempts and file access records against their work tasks for this period."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 3 | The activity is summarized correctly, but “during working hours” is not established by the false off-hours flag. |
| Clarity | 4 | The main event pattern is simple and readable. |
| Actionability | 4 | It directs attention to login attempts and file-access records for this alert. |
| Consistency | 3 | The timing assertion despite missing schedule evidence makes it less aligned with the grounded style. |

### Alert 2329 - Total 17/20 - Validator accepted

**Actual alert:** Sector `sme_startup`; user `hr_01`; role `hr`; severity `CRITICAL`; description `off-hours login, accessed 103 files, transferred 793 MB, 9 failed logins`; login hour 0; `off_hours_access=1`; 103 files; 793.26 MB transferred; 9 failed logins; IF/OC-SVM/final scores 100.0/100.0/100.0.

**Generated response:**

```json
{"explanation":"The HR employee logged in off-hours, accessed numerous files, transferred significant data, and experienced multiple failed logins, indicating a high-risk anomaly.","recommendation":"Confirm the off-hours login with HR department supervisor."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 5 | It correctly reflects the true off-hours flag and main event types without inventing a cause. |
| Clarity | 4 | The short sentences are easy to understand. |
| Actionability | 4 | It requests confirmation of this user's off-hours login, though it could name the records to review. |
| Consistency | 4 | It matches Phi's standard concise, neutral structure. |

### Alert 2310 - Total 15/20 - Validator accepted

**Actual alert:** Sector `sme_startup`; user `hr_02`; role `hr`; severity `MEDIUM`; description `unusual activity pattern`; login hour 10; `off_hours_access=0`; 15 files; 1.20 MB transferred; 1 failed login; IF/OC-SVM/final scores 46.4/100.0/72.8.

**Generated response:**

```json
{"explanation":"An HR user with a medium severity flag exhibited unusual activity patterns by accessing 15 files, transferring 1.2MB, and having one failed login.","recommendation":"Confirm the unusual activity with an authorized supervisor and check related authentication or access records."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 5 | It reports the severity, file count, transfer, and failed login without making an unsupported timing claim. |
| Clarity | 4 | The explanation is direct and understandable. |
| Actionability | 4 | Authentication/access-record checks are tied to the specific flagged events. |
| Consistency | 4 | It has Phi's usual concise format and measured tone. |

### Alert 2299 - Total 14/20 - Validator rejected

**Actual alert:** Sector `sme_startup`; user `it_admin_05`; role `it_admin`; severity `MEDIUM`; description `unusual activity pattern`; login hour 14; `off_hours_access=0`; 2 files; 2.75 MB transferred; 0 failed logins; IF/OC-SVM/final scores 87.5/92.4/93.8.

**Generated response:**

```json
{"explanation":"An IT admin (it_admin_05) with a medium severity alert flagged unusual activity patterns in a fintech startup sector, involving data transfers and access to sensitive files during regular business hours.","recommendation":"Review the InSighter audit trail for the session/logon events, file access events, and device/USB events related to it_admin_05 to confirm the anomaly."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 2 | It invents a fintech sector and sensitive-file context, and calls the timing regular business hours without that classification. |
| Clarity | 4 | The recommendation is readable and names concrete record types. |
| Actionability | 4 | Reviewing the audit trail's session and file-access events is specific to this user, though the USB check is unprompted. |
| Consistency | 4 | It keeps the established two-field format and neutral tone. |

### Alert 2292 - Total 17/20 - Validator accepted

**Actual alert:** Sector `sme_startup`; user `hr_04`; role `hr`; severity `CRITICAL`; description `off-hours login, accessed 99 files, transferred 937 MB, 8 failed logins`; login hour 1; `off_hours_access=1`; 99 files; 936.66 MB transferred; 8 failed logins; IF/OC-SVM/final scores 98.6/90.3/100.0.

**Generated response:**

```json
{"explanation":"HR employee logged in during system off-hours and accessed 99 files while transferring a large amount of data, with multiple failed login attempts, raising a critical alert in a sensitive startup sector.","recommendation":"Verify the legitimacy of the off-hours activity and investigate the 99 files accessed and 937 MB transferred."}
```

| Criterion | Score | Justification |
|---|---:|---|
| Accuracy | 5 | The role, true off-hours flag, file and transfer volumes, failures, and critical severity all align with the alert. |
| Clarity | 4 | The explanation is clear, though “sensitive startup sector” is an unnecessary characterization. |
| Actionability | 4 | It names the exact 99 files and roughly 937 MB transfer for review. |
| Consistency | 4 | It matches the concise risk-summary and specific-action structure of most responses. |

## Conclusion

The off-hours issue is not UAV-specific: the raw output still made false timing claims in both non-UAV sectors. Final validator replay rejects 7/10 private-school and 4/10 SME responses on timing; the model itself continues to generate unsupported schedule language. Accuracy averages 3.20 in private school and 3.70 in SME, compared with 3.60 in the earlier UAV-only review. This cross-sector evaluation used simulated seeded activity, and the small samples are not a ground-truth accuracy study. Phi-4-mini should remain an analyst-assistive draft generator, not an unsupervised decision-maker.
