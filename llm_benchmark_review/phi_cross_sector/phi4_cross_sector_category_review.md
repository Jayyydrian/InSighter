# Phi-4-mini Cross-Sector Category Grounding Review

> Historical benchmark: captured before `phi4-mini` became the default; these results document the model comparison that informed that decision, not current default behavior.

## Matched Sample

The final run used the same 20 alert IDs and original fields as the prior timing-only review. The only added input field is each source row's `data_category`: alert 1365 is `projects`; alert 2393 is `financial_transactions`. Full paired final inputs and raw responses are in `phi4_cross_sector_final_outputs.json`.

## Before / After

| Run | Private-school acceptance | Private-school accuracy mean / median | SME acceptance | SME accuracy mean / median |
|---|---:|---:|---:|---:|
| Original prompt, before timing grounding | 3/10 (30%) | 3.20 / 3 | 6/10 (60%) | 4.10 / 4.5 |
| Timing generation fix, category tag absent | 9/10 (90%) | 3.60 / 4 | 9/10 (90%) | 4.20 / 4.5 |
| Event-category grounding (current) | 10/10 (100%) | 4.10 / 4 | 10/10 (100%) | 4.20 / 4 |

Both former category rejections are now accepted: `1365` is explicitly tagged `projects`; `2393` is tagged `financial_transactions`. There are no validator rejections in the final sample.

## Final Accuracy Scores

| Alert ID | Sector | Accuracy / 5 | Brief rationale |
|---:|---|---:|---|
| 1440 | private_school | 4 | Correctly reports off-hours and major activity, with minor omission of exact severity detail. |
| 1435 | private_school | 5 | Matches event counts and the `projects` tag without inventing a schedule. |
| 1431 | private_school | 4 | Correctly describes project-file activity but omits some counts. |
| 1418 | private_school | 4 | Correctly describes the project category and failed login, though briefly. |
| 1416 | private_school | 4 | Matches the event's `projects` category and transfer facts. |
| 1394 | private_school | 3 | Adds “potential insider threat,” which is not established by the alert. |
| 1376 | private_school | 4 | Accurately summarizes file, transfer, and login activity without a timing claim. |
| 1370 | private_school | 4 | Reports the event facts; “projects sector” imprecisely names the category. |
| 1367 | private_school | 4 | Correctly identifies project-file activity, with limited event detail. |
| 1365 | private_school | 5 | Correctly states 15 project files, 7.27 MB, and one failed login. |
| 2393 | sme_startup | 4 | Uses the `financial_transactions` tag accurately, but omits the event quantities. |
| 2378 | sme_startup | 5 | Correctly connects the employee-record category to the off-hours event and quantities. |
| 2371 | sme_startup | 4 | Matches the financial-transaction category, but provides few specifics. |
| 2346 | sme_startup | 4 | Correctly uses its financial-transaction tag and avoids unsupported timing. |
| 2345 | sme_startup | 4 | Category is supported, though quantities and severity are omitted. |
| 2330 | sme_startup | 4 | Summarizes the critical activity without unsupported schedule/category claims. |
| 2329 | sme_startup | 5 | Correctly describes the employee-record event and off-hours activity. |
| 2310 | sme_startup | 4 | Correct category and a grounded account of the alert. |
| 2299 | sme_startup | 4 | Category-specific financial activity is grounded in the row tag. |
| 2292 | sme_startup | 4 | Correctly reports off-hours access and the file/transfer activity. |

## Conclusion

The event-level `data_category` field resolved the prior unsupported-category failures on this matched sample. Acceptance is now 100% in both sectors, exceeding the 95% bar; manual accuracy improved by 0.50 points in private school and remained 4.20 in SME relative to the timing-only round. This is a 20-alert seeded/simulated evaluation, not a production ground-truth accuracy study.
