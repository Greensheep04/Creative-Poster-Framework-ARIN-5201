# Team Integration Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for continuous local implementation.

**Goal:** Merge completed roles and verify the actual offline four-role pipeline.
**Architecture:** A1 boundary wrapper publishes normalized Brief plus original v2 source; reuses team A2, A3, QCBridge and A4. A unified runner selects explicit offline or live configuration.
**Tech Stack:** Python 3.11, Conda, uv, pytest, Pillow, existing poster framework.
**Spec:** docs/superpowers/specs/2026-11-18-team-integration-design.md

## Global Constraints
- No push, live API calls, model downloads or credentials committed.
- Keep dedicated existing Conda environment; preserve GPU extras and locked dependencies.
- Preserve original keywords, exact copy, current candidate identities and bounded budgets.

## Review Focus
- Brief revisions must retain raw A1 format for the next A1 prompt.
- Empty optional copy must remain valid; palette/design intent must not disappear.
- Offline reports must not release production candidates.
- Missing live configuration must fail before model calls.
- Repeated/unknown feedback must terminate within budgets.

## Task 1: Merge and packaging
Files: pyproject.toml, uv.lock.
- [ ] Resolve conflicts using the union of packages/resources and strict A4 httpx bounds.
- [ ] Regenerate lock offline, check it and commit merge.

## Task 2: A1 interface and revisions
Files: src/poster_visual_producer/team_creative.py, tests/visual/test_team_creative.py.
Interface: creative_registration(offline=False, model=None), produces poster.creative-brief@0.1.0.
- [ ] Write and run failing tests for initial/revised versioned Brief and exact original source.
- [ ] Implement contextual wrapper and feedback routing; preserve raw v2 memory for revisions.
- [ ] Run focused tests until passing.

## Task 3: Unified runner and verification
Files: examples/team_workflow.py, docs/team-integration.md, tests/visual/test_team_workflow.py.
- [ ] Write failing tests for offline CLI, revisions, config checks and bounded stop.
- [ ] Implement explicit offline/live runner with no implicit service selection.
- [ ] Run full suite in short paths, offline examples and package/resource checks.
- [ ] Record results/prerequisites, review the branch and commit locally.
