# Risk Grounding and SQLite Teardown Review

> Historical benchmark: captured before `phi4-mini` became the default; these results document the model comparison that informed that decision, not current default behavior.

## Intent-Language Diagnosis

**Alert 1394 input:** user `researcher_02`, role `researcher`; description `unusual activity pattern`; severity `MEDIUM`; IF score 100.0; OC-SVM score 95.3; final score 100.0; event category `projects`; 4 files, 2.11 MB transferred, 1 failed login.

**Prior generated sentence:** “A researcher flagged unusual activity involving project files, with a medium severity alert indicating a potential insider threat.”

The alert establishes anomaly indicators, not insider intent. This was an unsupported conclusion. Scanning the prior 20 final responses found four risk/conclusion phrases:

| Alert | Phrase | Assessment against alert |
|---:|---|---|
| 1394 | “potential insider threat” | Unsupported intent conclusion; MEDIUM severity and anomaly scores do not establish insider intent. |
| 1431 | “potential issue” | Generic uncertainty, not an accusation or specific intent claim; attached to MEDIUM severity and score 100. |
| 2378 | “review ... for any unauthorized changes or data transfers” | Conditional investigation request, not a statement that unauthorized activity occurred; CRITICAL severity, score 100, large transfer and repeated failed logins. |
| 2329 | “high risk” | Risk characterization consistent with CRITICAL severity and final score 100. |

The final prompt now permits risk language only when grounded in the alert's literal severity and computed scores; it bars intent/culpability findings such as insider threat, malicious intent, compromise, exfiltration, fraud, theft, or sabotage. The validator rejects those conclusions, prevents HIGH/CRITICAL risk labels on lower severity, and allows “unauthorized” only when framed as a question to investigate rather than an established finding.

The same 20-alert run after this change contains no insider-threat, malicious-intent, compromise, fraud, or exfiltration assertions. One recommendation says to check for “any discrepancies or unauthorized activities”; it is framed as an investigation, not an assertion. The validator accepted 20/20 responses.

## Cross-Sector Comparison

The alert IDs and input fields were unchanged across all rounds. Accuracy scores are manual judgments using the same four-criterion review rubric; the table reports accuracy only.

| Review round | Private-school acceptance | Private-school accuracy mean / median | SME acceptance | SME accuracy mean / median |
|---|---:|---:|---:|---:|
| Original prompt | 3/10 (30%) | 3.20 / 3 | 6/10 (60%) | 4.10 / 4.5 |
| Timing generation fix | 9/10 (90%) | 3.60 / 4 | 9/10 (90%) | 4.20 / 4.5 |
| Event-category grounding | 10/10 (100%) | 4.10 / 4 | 10/10 (100%) | 4.20 / 4 |
| Severity/intent grounding | 10/10 (100%) | 4.30 / 4 | 10/10 (100%) | 4.40 / 4 |

Final accuracy by alert ID:

| Sector | Alert IDs in order | Accuracy scores in order |
|---|---|---|
| Private school | 1440, 1435, 1431, 1418, 1416, 1394, 1376, 1370, 1367, 1365 | 5, 5, 4, 4, 4, 4, 5, 5, 4, 5 |
| SME & startup | 2393, 2378, 2371, 2346, 2345, 2330, 2329, 2310, 2299, 2292 | 4, 5, 4, 4, 4, 5, 5, 4, 4, 5 |

## Windows Teardown Root Cause and Fix

`test_sector_deployment.py` imports `app`, which starts `_background_simulation_loop` by default. The loop repeatedly calls database-backed simulation functions while the tests replace `database.DB_PATH` with temporary files; a worker can retain/use a handle as teardown tries to delete the file. In addition, direct test-owned SQLite connections were closed only on normal paths.

The app-importing tests now set `INSIGHTER_DISABLE_BACKGROUND_SIM=1` before importing `app`. Direct connections in the sector tests use `contextlib.closing`, so they close even if a query or assertion raises. `test_sector_deployment.py` passes standalone (6/6); the full suite passes 80/80 with zero errors.
