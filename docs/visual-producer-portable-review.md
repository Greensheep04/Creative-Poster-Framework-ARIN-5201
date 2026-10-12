# A3 portable/QC independent review — 2026-10-12

Scope: BASE 85c386480a789bf60fe9d834a41ad708204ea3f2 → reviewed HEAD 423d17370d120921e8a2bbea58cc3f877cf5377c. User selected sequential implementation and one independent read-only review. Reviewer inspected code, approved spec/plan and isolated probes; no additional implementation agents.

Verdict at reviewed HEAD: no Critical findings; two Important/P2 findings. Final integration includes the following verified fixes.

| Finding | Reproduction | Fix and regression evidence |
|---|---|---|
| Real QC pass retained previous unresolved issues | fail → authorized rerender → real pass yielded passed but memory.unresolved_issues still contained quality review failed | Clear current unresolved issues on release, retain the original failure in revisions. test_real_pass_after_authorized_repair_clears_unresolved_issues observed RED then GREEN. |
| Invalid inline cloud Key appeared in production CLI stderr | actual `python -m poster_visual_producer --backend-config ...` rejected extra api_key but printed its sentinel value in Pydantic ValidationError | Backend config models hide input values in rendered errors. test_production_cli_rejects_inline_secret_without_echo observed RED then GREEN, CLI still rejects config before network. |

Minor: docs used sdxl_local instead of legacy sdxl. Corrected only documentation; existing configuration names remain unchanged. All three setup commands and locked dependency export commands were actually executed.

Final verification: 23 related CLI/QC/cloud tests passed; full suite 284 passed, 1 existing WinError 1314 symlink privilege skip. Additional actual HTTP tests cover 401 rejected without retry, 503 unknown without retry, one 429 safe retry counted against budget, and transport disconnect without resubmission. No A1/A2 implementation, registration, format or prompt changes; existing workflow_demo.py, brief.json and spec.json remain unchanged.

Declined-to-judge rulings:

- Actual Qwen GPU execution, paid external cloud compatibility and real A4 vision quality: not exercised; GPU/Hub doubles and local HTTP prove engineering behavior. This limitation remains explicit in delivery.
- Upstream semantic preserve compliance: owned by trusted integration and existing revision_validator for revise_brief/revise_layout; A3 targeted regeneration/rerender preservation is checked by real production tests. No A1/A2 modification is required.
- Arbitrary cloud protocols: only synchronous Images JSON is directly implemented; the trusted factory interface handles other SDKs.
- Callback authenticity: a trusted application boundary. Raw repair_hint never creates an authorization; no callback/None stops needs_review.

Acceptance entry point: examples/visual/qc_collaboration.py combines Task 3 demo and Task 7 acceptance to avoid duplicate sample publishers. Four actual CLI scenarios save real PNG/bundles/records under runs/qca and stop needs_review. Source/wheel packaging verification is saved in runs/qca/package-verification.json. These generated files are intentionally excluded from Git/distribution.

Branch remains local codex/visual-producer, as approved; no merge, push or publication.
