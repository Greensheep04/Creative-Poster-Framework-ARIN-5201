# Unusable runs and attempt history

Archived at the user's request on 14 October 2026. The main outputs folder retains only the 31 valid briefs.

This archive holds 14 unusable Qwen3.5 cases: 13 responses with `finish_reason: length` (12 empty final answers and one incomplete JSON answer), plus human-story/ai-creativity-identity, which failed with provider/network errors and has no captured response. Long reasoning precedes the missing or incomplete final content; this is consistent with exhausting the generation allowance. The exact numeric limit was not exposed by the widget.

`outputs/` preserves the original directory layout and unchanged request, response, reasoning, metadata, error, and screenshot files. `attempts/` contains earlier failed or superseded attempts. `evidence/` holds the resumed-batch error screenshot. `collection-before-cleanup/` preserves the collection's previous reports and documentation; their paths and counts describe the pre-archive layout.

`archive-manifest.json` records every moved or copied file with its original path and SHA-256 hash. No failed response was repaired or counted as a usable brief. The previous-samples archive is unchanged.
