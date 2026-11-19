# Poster quality implementation — first-round result

Code baseline: 4980f5f. Branch: codex/poster-quality-improvements.
Actual first-round generation: 8dca025. Hardened implementation: c9f0f38.

## Separate acceptance

- Engineering: 443 passed, one existing Windows symlink-privilege skip.
- Semantics: failed. No poster meets all required visual evidence.
- Design: not accepted. Typography improved, but intent/image relationships remain defective.
- Unknown observations are retained and never counted as pass.

| Case | Actual observed result |
|---|---|
| 01 | Doorway present; chair/photos and memory-migration mosaic absent |
| 02 | Worker/tools added; shared labor absent; repair action/hammer/repair contrast uncertain |
| 03 | Giant glass container with a small person inside; no held plastic bottle/watch |
| 04 | Three figures; no separate-window mutual gaze; empty subtitle retained |
| 05 | Multiple houses/path added; memory fragments and migration stages unclear |
| 06 | Bags/plastic containers present; electronic waste unclear; wave/degeneration relation absent |

All six A1 byte hashes, copy/punctuation, source image hashes and approved A3
geometry were verified. A2-approved contain retained 100% source area. Missing
objects were absent in raw sources, not lost by cropping. All six same-input
Pillow rerenders produced byte-identical PNGs without image operations.

## Budget and execution

Six successful local CUDA SDXL operations, no retries or failed submissions.
Existing weights, 30 steps, guidance 6, seed 42, model CPU offload.
Backend elapsed total: 118.340 seconds, including initialization/transfers.
Peak allocated VRAM: 5.21 GiB. Pure GPU kernel time was not instrumented.
No paid APIs, new model download or A4 calls.

The first six are spent; the remaining nine operations are not authorized for
automatic use. Authorization-level ledger recovery was tested in a different
output directory: six NO RETRY results and no new generation.

Final review fixes include source/receipt/keyword provenance, partial-object
preservation, qualified prohibitions, explicit case-03 quantity/ground exclusions,
A1 left alignment, quality fitter isolation, authorized repair constraints,
unaffected-source settings, final tokenizer enforcement and observation identity.
Corrected six-case requests passed real tokenizer/font/geometry and A3 provenance
preflight only. They must not be presented as the prompts that made the images.

## Artifacts

Local ignored runtime folder: runs/poster-quality-20261108.
acceptance.md and poster-quality-round1-for-gpt.txt include each original A1
visual requirement, actual compiled prompts/parameters, before/after findings and
per-item evidence. acceptance.json contains source/spec/poster identity bindings.
round1/01 through 06 contain raw/formal/processed PNGs, registered execution
records, contracts, Specs and reviewed evidence.
post-review-preflight contains the later corrected, ungenerated contracts.
The review ZIP includes the six comparisons, original files and code snapshots.

Actual GPU targeted regeneration has not been performed. Existing tests verify
copy/layout/untargeted-asset preservation; the backend does not support masked
inpainting or a guarantee that unaffected pixels inside one regenerated hero
stay identical. The next image round awaits the user's first-round review.
