# Visual Producer Implementation Plan

Execution: implement inline with superpowers:executing-plans and TDD; final independent code review.
Goal: implement the approved A3 production pipeline on the existing framework.
Architecture: typed contracts → preflight → capability-adapted generator/cache → processing → deterministic renderer → candidate/evidence → existing QA routing.
Tech: Python 3.11, Pydantic 2, Pillow, fontTools, existing LangGraph runtime, Conda and uv.lock.
Spec: ../specs/2026-10-09-visual-producer-design.md

Global constraints: canvas 720×1280; schema 0.1.0; rendered does not mean passed; preserve A1/A2 content; explicit real PNG execution; budget 2/3/3; do not silently downgrade parameters/fonts; no arbitrary file refs; no secrets in logs.
Review focus: multilingual overflow/glyphs; stale business/framework dependencies; rerender/source preservation; corrupt files/cache; retry and budget correctness.

## Task 1: environment and baseline
- [ ] Clone to D drive, create branch, Conda Python 3.11, export locked deps and explicitly install into this interpreter.
- [ ] Run full pytest, preserve evidence and environment limitations.
## Task 2: contracts
Files: src/poster_visual_producer/contracts.py; tests/visual/test_contracts.py; examples/visual/*.json.
Consumes document JSON; produces PosterSpec, CreativeBrief, validate_pair(spec, brief, keyword_ids).
- [ ] Test valid document fixture and reject duplicate IDs, dangling assets, modified copy, invalid canvas/geometry and unknown keyword IDs; run RED.
- [ ] Implement strict typed schemas and cross-checks; run GREEN and full suite; export schemas and commit.
## Task 3: deterministic render
Files: fonts.py, imaging.py, renderer.py; tests/visual/test_rendering.py.
Produces FontRegistry, prepare_asset(bytes, element), render_poster(spec, assets, registry).
- [ ] Test exact PNG size/pixel placement, Chinese/English text, overflow, missing font/glyph, cover/contain geometry, unsafe oversized input; run RED.
- [ ] Implement measurement and rendering from same layout records; emit actual bounds/font/transform hashes; run GREEN/full suite, publish sample and commit.
## Task 4: generator adapter
Files: generators.py; tests/visual/test_generation.py.
Produces GeneratorCapabilities, GenerationRequest/Result, CallableImageGenerator, FixtureImageGenerator, compile_request.
- [ ] Test unsupported seed/negative prompt, supported size selection, corrupt response and explicit offline provenance; run RED.
- [ ] Implement capability negotiation and trusted source adapter; run GREEN/full suite and commit.
## Task 5: A3 and revisions
Files: agent.py, registration.py; tests/visual/test_agent.py.
Produces VisualProducerAgent.run(inputs, context) -> AgentOutput; registration and project_budget().
- [ ] Test real PNG/evidence, cache, no image call for title move, targeted regeneration, stale repair, missing font before image call, failed attempts and budget; run RED.
- [ ] Implement framework-owned files/fingerprints/usage and immutable upstream constraints; run GREEN/full suite and commit.
## Task 6: integration and handoff
Files: demo.py, CLI/__main__.py, docs/visual-producer.md, scripts/setup-visual.ps1.
- [ ] Test four-role workflow with deterministic upstream/QA test doubles, rerender/regeneration and artifact preservation.
- [ ] Publish Chinese and English offline PNGs plus change/usage verification; build wheel and verify both packages import; full suite.
- [ ] Independent code review, fix substantive findings with RED/GREEN, record final evidence and commit.
