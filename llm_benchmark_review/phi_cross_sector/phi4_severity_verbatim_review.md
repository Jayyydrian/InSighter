# Severity-Label Fidelity Review

> Historical benchmark: captured before `phi4-mini` became the default; these results document the model comparison that informed that decision, not current default behavior.

## Baseline Mismatch

**Alert 2329 input:** user `hr_01`, role `hr`; severity `CRITICAL`; IF 100.0, OC-SVM 100.0, final score 100.0; description `off-hours login, accessed 103 files, transferred 793 MB, 9 failed logins`.

**Baseline response wording:** “Anomaly scores are 100%, indicating a high risk.”

The prompt previously grounded risk language in severity/scores and prohibited unsupported conclusions, but it did not explicitly require reproducing the severity label verbatim. Its existing restriction barred raising MEDIUM/LOW to high/critical risk; it did not bar substituting “high risk” for CRITICAL.

## Fix and Re-run

The prompt now explicitly says to repeat the alert's severity label verbatim and includes a correct/incorrect example: CRITICAL should be called “CRITICAL,” not “high risk” or “elevated.” A response validator rejects explicit severity/risk labels that mismatch the alert. A regression test reproduces alert 2329's input and confirms the prior mixed wording is rejected while the verbatim CRITICAL response is accepted.

The same 20 alert IDs and input fields were rerun. Alert 2329 now says: “The alert severity is CRITICAL. The user hr_01 logged in during off-hours and accessed 103 files, transferred 793 MB, and had 9 failed logins. Anomaly scores are 100.0 for both Isolation Forest and One-Class SVM.” The final cross-sector run accepted 10/10 private-school and 10/10 SME responses.

| Sector | Previous accuracy mean / median | Final accuracy mean / median | Acceptance |
|---|---:|---:|---:|
| `private_school` | 4.30 / 4 | 4.30 / 4 | 10/10 |
| `sme_startup` | 4.40 / 4 | 4.50 / 4.5 | 10/10 |

## Test-Count Reconciliation

| Stage | Tests added | Running total |
|---|---|---:|
| Previous full-suite baseline | — | 76 |
| Severity/intent grounding round | `test_alert_1394_rejects_potential_insider_threat_conclusion`; `test_alert_severity_and_score_language_is_allowed`; `test_high_risk_label_requires_high_or_critical_severity`; `test_verbatim_critical_label_is_allowed_for_critical_severity` | 80 |
| This round | `test_alert_2329_requires_verbatim_critical_severity` | 81 |

The CRITICAL-label acceptance test was renamed to reflect its updated assertion; it is not an additional test. The duplicate stale root `test_llm_advisor.py` was removed after moving the current file to `llm_benchmark_review/tests/`, so discovery no longer runs two copies.

The app's background simulation worker is disabled before app imports in app-dependent tests, and test-owned SQLite connections in `test_sector_deployment.py` close deterministically. Final verification: `python -m unittest test_sector_deployment` passed 6/6; full discovery passed **81/81 with zero errors or skips**.
