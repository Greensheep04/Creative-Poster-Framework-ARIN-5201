# Local team integration

Approved in chat on 2026-11-18: merge all completed team modules locally, adapt interfaces, and finish offline verification. Do not push or call live services until credentials/configuration arrive.

Use the existing A3 integration as the baseline and merge creative-director. Preserve all four implementations and prompts. Bridge A1 v2 to the current A2/A3 Brief schema, retaining original JSON, exact copy, original request identities and increasing business versions. Preserve the raw v2 brief for A1 revision feedback.

Provide a single explicit offline/live runner. Offline uses the actual A1 deterministic mode, actual A2/A3 renderer and actual A4 engine with fixture image/observations. Never label offline output as production passed. Live uses existing A1 HF configuration, configured A3 backend and explicit A4 configuration; report prerequisites before calls.

Cover initial flow, feedback-driven brief revision, rerender/regenerate routing where supported, limits and provenance. First live scope remains one case and at most one revision; no live execution in this phase. Deliver a committed local branch, reproducible commands and evidence.

Approach: adapt at the integration boundary rather than replacing team schemas or rewriting each role. Work on a new local branch in the clean existing checkout to preserve short Windows paths and existing fonts/runtime configuration. Existing A3 branch remains intact.
