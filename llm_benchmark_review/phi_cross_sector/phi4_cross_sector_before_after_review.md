# Phi-4-mini Cross-Sector Before/After Review

> Historical benchmark: captured before `phi4-mini` became the default; these results document the model comparison that informed that decision, not current default behavior.

## Sample and Results

The after run used the same 20 alert IDs and identical alert input fields as the prior review: private-school IDs `1440, 1435, 1431, 1418, 1416, 1394, 1376, 1370, 1367, 1365`; SME IDs `2393, 2378, 2371, 2346, 2345, 2330, 2329, 2310, 2299, 2292`.

| Sector | Validator acceptance before | Validator acceptance after | Accuracy mean / median before | Accuracy mean / median after |
|---|---:|---:|---:|---:|
| `private_school` | 3/10 (30%) | 9/10 (90%) | 3.20 / 3 | 3.60 / 4 |
| `sme_startup` | 6/10 (60%) | 9/10 (90%) | 4.10 / 4.5 | 4.20 / 4.5 |

The old outputs were replayed through the same current validator; this corrects one stale saved status for alert `1370`. All previous timing-claim rejects now pass the timing validator. The remaining rejects are unsupported monitored-category recommendations: private alert `1365` and SME alert `2393`.

## Per-Alert Accuracy

| Alert ID | Sector | Before | After | Delta / finding |
|---:|---|---:|---:|---|
| 1440 | private_school | 5 | 4 | -1; accurate but less specific. |
| 1435 | private_school | 3 | 5 | +2; exact activity details, no schedule claim. |
| 1431 | private_school | 4 | 4 | Unchanged; concise but omits quantities. |
| 1418 | private_school | 4 | 4 | Unchanged; grounded counts, no timing inference. |
| 1416 | private_school | 3 | 3 | Still invents a “private document” type. |
| 1394 | private_school | 3 | 4 | +1; accurate event summary, no false schedule. |
| 1376 | private_school | 2 | 3 | +1; false timing removed, role-norm claim remains. |
| 1370 | private_school | 2 | 4 | +2; no schedule claim; reports alert activity and scores. |
| 1367 | private_school | 3 | 3 | Still calls files “sensitive” without evidence. |
| 1365 | private_school | 3 | 2 | -1; invents category-specific access and a review period. |
| 2393 | sme_startup | 3 | 2 | -1; unsupported category-specific access recommendation. |
| 2378 | sme_startup | 4 | 4 | Unchanged; accurate but omits some exact counts. |
| 2371 | sme_startup | 5 | 4 | -1; accurate high-level summary, less specific. |
| 2346 | sme_startup | 5 | 5 | Unchanged; accurate details and cautious risk language. |
| 2345 | sme_startup | 5 | 5 | Unchanged; all key event details grounded. |
| 2330 | sme_startup | 3 | 5 | +2; accurate critical-event summary without schedule claim. |
| 2329 | sme_startup | 5 | 5 | Unchanged; accurate off-hours and event summary. |
| 2310 | sme_startup | 5 | 4 | -1; correct event facts, generic next step. |
| 2299 | sme_startup | 2 | 3 | +1; schedule claim removed, explanation remains opaque. |
| 2292 | sme_startup | 4 | 5 | +1; accurate off-hours event and quantities. |

## Residual Rejections

### Private School: Alert 1365

**Input:** `admin_staff_01`, role `admin_staff`, severity `MEDIUM`; description `unusual activity pattern`; 15 files, 7.27 MB transferred, one failed login; final score 75.5; `off_hours_access=0`.

**Response:**

```json
{
  "explanation": "An admin staff member accessed 15 files, transferred 7.27 MB of data, and had one failed login attempt. Anomaly scores suggest unusual activity.",
  "recommendation": "Review the admin_staff_01's access to private_documents, intellectual_property, and pii for the past week."
}
```

The explanation is grounded; the recommendation turns sector-wide monitored categories into claims about this user's access and invents a one-week review period.

### SME: Alert 2393

**Input:** `hr_05`, role `hr`, severity `MEDIUM`; description `unusual activity pattern`; 15 files, 31.13 MB transferred, no failed logins; final score 50.8; `off_hours_access=0`.

**Response:**

```json
{
  "explanation": "The alert indicates unusual file activity by an HR employee in a startup sector, with a medium severity level.",
  "recommendation": "Review the HR employee's access to sensitive financial transactions, client data, and employee records."
}
```

The recommendation names monitored categories without evidence that this alert involved those data types.

## Verdict

The timing generation fix worked on this matched sample: validator acceptance rose from 30% to 90% in private school and from 60% to 90% in SME, with no false off-hours claims in the after responses. Accuracy increased by 0.40 points in private school and 0.10 in SME. A reasonable ship bar is at least 95% acceptance per sector with no unsupported source/category recommendations. The result misses that bar, so Phi-4-mini is **not ready as the unreviewed default for private_school**; the remaining rejection is now category grounding, not timing. The after-run raw outputs are in `phi4_cross_sector_after_outputs.json`.
