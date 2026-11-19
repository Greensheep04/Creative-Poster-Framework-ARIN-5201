# Visual Producer design — approved scope

Input: 2–5 keywords across the system; A3 consumes immutable PosterSpec and read-only CreativeBrief. Output: a real 720×1280 RGB PNG, typed candidate status rendered, render manifest and framework-owned artifact references.

Four roles are retained. A3 cannot rewrite copy, layout, font, or color. It selects cache/generate/rerender/error paths using backend capabilities, runtime budgets and authorized revision targets. No fifth agent, arbitrary generated scripts or pixel inpainting in MVP.

Use the existing Agent.run/AgentRegistration, context.call_model, GenerationFingerprint, context.cache and context.files APIs. Map integer business versions to framework dependency identities; do not replace framework revision IDs. Generation and rendering remain separate. Preserve source image, processing provenance and hashes.

Shared text engine: Pillow with explicit font files, real weights and fontTools cmap checks. Error on missing fonts/glyphs or overflow. Crop using fit/focus, preserve aspect ratio, record transforms. Preflight before expensive generation.

Generator backend is not selected. Provide a typed SDK callback adapter and explicitly labeled local fixture backend; no placeholder fallback on remote failure. Real service integration and visual performance remain pending provider selection. No claim that offline fixture is AI-generated or quality-approved.

Default app budget: 2 revision rounds, 3 image operations, 3 critic vision calls. All attempts counted via framework. Only unambiguous retry-safe failures may retry once; timeout with unknown submission status does not retry blindly.

Machine conventions: code/runs in D:/Projects/Creative-Poster-Framework-ARIN-5201; Python 3.11 environment in D:/Dev/conda-envs/creative-poster; Conda cache D:/Dev/conda-pkgs; uv cache D:/Dev/uv-cache; future datasets D:/Datasets and weights D:/Models. Use explicit interpreter, Git branch codex/visual-producer and uv.lock. Do not create .venv.
