# Unified Configuration Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans continuously.

**Goal:** Manage the merged workflow from one private TOML while preserving standalone agents.
**Architecture:** Strict project config, private constructor credentials, explicit central CLI and redacted snapshots.
**Tech Stack:** Python 3.11 tomllib/Pydantic/pytest.
**Spec:** docs/superpowers/specs/2026-11-18-unified-config-design.md

## Global Constraints
- No live calls/push, no global credential environment mutation, no keys committed.
- Empty-key template tracked; config/project.local.toml ignored.
- Existing per-agent CLI/config/default behavior remains compatible.

## Review Focus
- TOML and validation errors must not reveal source values.
- Repr, registration metadata and snapshots must not contain keys.
- Relative paths resolve from config parent, independent of cwd.
- Explicit empty central keys must not accidentally use ambient credentials.
- Budget stops must still export a coherent result when no candidate exists.

## Task 1: Config model and templates
Files: src/poster_framework/project_config.py, config/project.example.toml, .gitignore, tests/test_project_config.py.
- [x] Write and run failing strict schema/path/redaction tests.
- [x] Implement config loader, local example, missing-field checks and redacted snapshot.

## Task 2: Private client credentials
Files: src/creative/creative_director.py, src/poster_visual_producer/team_creative.py, cloud_backend.py, backends.py; tests/test_unified_credentials.py.
- [x] Write HTTP-mock credential isolation and compatibility regressions, observe failure.
- [x] Add optional private key/parameter injection with old defaults intact.

## Task 3: Unified runner and verification
Files: examples/team_workflow.py, tests/visual/test_unified_workflow.py, docs/unified-configuration.md, package manifest.
- [x] Write failing central config CLI/offline/preflight/snapshot tests.
- [x] Wire existing roles, fonts, budget and providers from the project config.
- [x] Run targeted tests, independent review, final full suite, offline CLI, build/resource checks.
- [x] Record evidence and commit locally.
