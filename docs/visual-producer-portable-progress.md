# Execution ledger — plan: docs/superpowers/plans/2026-10-12-a3-portable-qc.md

User approved Native execution and one independent review; A1/A2 implementations and interfaces unchanged.
BASE: 85c386480a789bf60fe9d834a41ad708204ea3f2.
Existing clean feature checkout codex/visual-producer; reuse approved D-drive project and Conda environment. No extra worktree or environment needed.
Baseline already verified: 215 passed, 1 Windows symlink capability skip.
Task order: production evidence → QC bridge → external authorization → cloud → Qwen → setup/doctor → acceptance/docs/package/review.

Task 1: RED 3 missing-field failures; GREEN 22 production/repair regressions. Added per-element processed bindings, copy_ref, source_size, actual execution decisions. Legacy source file_ref and first-element compatibility references retained.

Task 1 full suite: 218 passed, 1 capability skip. Task 2: RED 18 missing-module cases; GREEN 18 QC contract/bundle/report identity tests. Added issued index_ref to Candidate so QC reads index through ArtifactStore rather than trusting a relative path. Legacy producers need an explicit index adapter; no arbitrary file reads.

Task 2 full suite: 236 passed, 1 capability skip. Task 3: 6 initial RED cases; GREEN external authorization and unchanged A1/A2 paths. Additional RED→GREEN: equal-condition assets now share cached bytes but have distinct repair identities; repeated QC problems use semantic issue signatures excluding sample/hash/version churn. 7 QC collaboration tests pass; 62 authorization/production/legacy-workflow tests passed before additional repeat guard. No A1/A2 source changes or fabricated Agent.run calls.
Ruling: QC coordination is an external trusted callback and feedback artifact, not a new A1 method — user explicitly required A1/A2 unchanged; integration owners supply confirmed authorization. Without it workflow stops needs_review.

Task 3 full suite: 244 passed, 1 capability skip. Task 4: RED 7 unrecognized backend cases; GREEN 7 actual local HTTP tests. Cloud downloads do not forward API Authorization, submitted-download failures never resubmit, inline keys rejected, endpoint participates in cache identity. No external paid calls performed.
