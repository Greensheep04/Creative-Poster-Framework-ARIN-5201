# A1 → A2 → A3 quality path

The quality path is opt-in. Existing public schemas, legacy registrations and
SDXL/Qwen/cloud generator protocols keep their required interfaces. No A4.
A1 is replay of existing remote model output, not a new A1 model invocation.

A1 source bytes are separately registered and hash-checked; canonical Brief
retains the complete original JSON and exact approved punctuation/copy.
A2 owns the internal visual contract, prompt compilation, fonts/colors/layout.
The version-1 sidecar binds source pointers/quotes/priorities/receipts to current
Brief/Spec digests and travels through existing artifacts/files/dependencies.
A3 checks registered identity, ownership, hashes, dependencies and prompt equality.
ScopedRunFiles.resolve_file only wraps the existing registered-file lookup;
it cannot resolve arbitrary paths or expand filesystem access.

Quality extraction uses conservative deterministic English vocabulary and
description rules, not unrestricted natural-language understanding. Abstract
goals remain context; unsupported core evidence needs confirmation. Required or
Forbidden overflow blocks; preferred deferrals are explicit receipts. Hex colors
compile to approximate named colors for SDXL; original RGB values remain intact.
The two explicit user clarifications are independent source-hash-bound records.

A2 templates use intended composition and reading order before generation.
Actual subject positions and image contrast remain unknown until inspected.
Typography tiers/gaps/leading are adjustable starting strategies. Independent
concept exploration is default; explicit cohesive series mode fixes the policy
and refuses unfittable text. A2 approves image fit/focus; A3 cannot alter it.
Unsupported shadow/fade preferences are recorded rather than silently claimed.

The optional A3 request_validator checks final requests after backend adaptation
and repair additions; require_visual_contract fails closed for quality callers.
Legacy callers need no migration. No generator method signature changes.

## Run the six samples
Use the dedicated environment and existing model weights. Pass a trusted font
configuration with keys default_family, role_families (serif/sans/handwritten),
and families (same existing FontRegistry configuration format). Paths belong in
local configuration, never reusable library code. No fonts/models are downloaded.

Run python -m examples.visual.quality_regression with --authorization AUTHORIZATION, --model-dir MODEL_DIR,
--font-config CONFIG, --output-dir NEW_DIRECTORY and --dry-run first. Then use
a directory without --dry-run. --resume recovers only unattempted cases under a new immutable recovery subdirectory. --baseline-dir optionally enables comparisons.
All six must preflight before any image call. Existing pinned SDXL, 30 steps,
guidance 6 and seed 42 are retained. CPU model offload reduces VRAM; denoising
runs on CUDA, with no CPU fallback.

The persistent ledger is bound to an explicit authorization ID and all six
source hashes, under the project runtime image-authorizations directory.
Changing output directories cannot reset it. Kernel locks release after process
death; interrupted/pending attempts remain spent. Existing completed results are
recovered, never regenerated. The authorization file declares authorization_id,
round=1, limit=6 and sources (case ID to original SHA-256). It is trusted
application configuration, not an Agent-generated permission.
The persistent ledger permits six first-round attempts, one per sample.
Failures, interruptions and pending operations consume capacity; no automatic
retry or overwrite. Each framework run permits one image operation, zero A4.
There is no second-round switch. Total ceiling 15 does not authorize the other
nine operations before the user sees round one and decides.

## Evidence and acceptance
Each case saves original A1 bytes, contract and receipts, Spec, final prompts,
tokenizer counts/parameters/seeds, framework records, raw source, final PNG and
render/visual evidence. Technical checks do not establish semantic success.
Actual semantic source/poster statuses start unknown. Source-, poster-, Spec- and requirement-bound observations
can carry object boxes, projected sizes, crop safety and thumbnail size. Initial
48px raw/24px projected prop-side thresholds supplement recognizability review.

Formal export copies exact rendered PNG bytes and adds no labels. Experimental
comparison labels occupy a separate band; exporting them never calls generation.
Original sources and baseline runs are preserved.

Quality asset repair requires current candidate/target/versions, explicit
RepairAuthorization, constraints/preserve and remaining budget. Unaffected
assets reuse exact bytes; unchanged Spec preserves copy/fonts/colors/layout.
Existing SDXL supports full-asset generation, not masked inpainting. Preservation
of other pixels inside a regenerated hero is not promised. Image/crop/semantic
issues go to A2 replanning or separately authorized asset work.

Evaluate engineering, semantics and design separately. Unknown never passes.
Unit-test success cannot substitute for visual evidence. Show round one before
spending further image operations.


## First-round history and review corrections
The first six actual images were generated at 8dca025 before the final
independent review. Their original requests, registered contracts, images and
records remain intact. The review's code corrections consume zero new image
operations. A later dry preflight records the corrected intended requests,
including case 03 quantity/ground exclusions and case 04 left alignment; do not
present those new requests as the ones that generated the archived six images.
All six existing operations were migrated to the authorization ledger with the
original ledger path and SHA-256, without resetting its spent count.
Old private sidecars are historical evidence; new executions/rework require a
fresh A2 sidecar meeting the strengthened provenance checks. Public JSON schemas
and existing required backend interfaces are unchanged.
