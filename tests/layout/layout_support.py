"""Shared stubs and fixtures for the layout planner test suite."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

# A2 tests must run from a plain source checkout even when the editable install
# cannot resolve a non-ASCII checkout path (Python 3.11 reads .pth files with the
# locale encoding).  Keep this bootstrap inside the A2 test helper so no shared
# pytest configuration changes for the other roles.
_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from poster_framework import (
    AgentInput,
    AgentRole,
    ExecutionIdentity,
    ExecutionMode,
    FrameworkMetadata,
    MissingFontError,
    OriginalRequest,
    RevisionAction,
    default_font_registry,
)
from poster_framework.layout_planner import BRIEF_FORMAT

DEFAULT_KEYWORDS = ["海洋", "宁静", "蓝色"]


def fonts_available() -> bool:
    registry = default_font_registry()
    try:
        registry.resolve("noto_sans_sc", 400, allow_nearest=True)
        registry.resolve("noto_sans_sc", 700, allow_nearest=True)
    except MissingFontError:
        return False
    return True


FONTS_AVAILABLE = fonts_available()


def make_request(
    keywords: list[str] | None = None,
    *,
    run_id: str = "layout-test-run",
) -> OriginalRequest:
    return OriginalRequest.from_keywords(run_id, keywords or DEFAULT_KEYWORDS)


def make_brief(**overrides: Any) -> dict[str, Any]:
    brief: dict[str, Any] = {
        "copy": {"title": "静海之蓝", "subtitle": "留一点宁静给自己"},
    }
    brief.update(overrides)
    return brief


def make_metadata(
    request: OriginalRequest,
    *,
    action: RevisionAction | None = None,
    target: Any = None,
    invocation_id: str = "layout-invocation-1",
) -> FrameworkMetadata:
    return FrameworkMetadata(
        identity=ExecutionIdentity(
            run_id=request.run_id,
            role=AgentRole.layout_planner,
            invocation_id=invocation_id,
            execution_mode=ExecutionMode.real,
        ),
        original_request=request,
        revision_action=action,
        revision_target=target,
    )


class StubMemory:
    def __init__(self, artifacts: list[Mapping[str, Any]] | None = None) -> None:
        self._artifacts = list(artifacts or [])

    def context(self) -> dict[str, Any]:
        return {
            "original_request": {},
            "artifacts": self._artifacts,
            "notes": [],
            "unresolved_issues": [],
            "revision_history": [],
            "private_messages": [],
        }


class StubContext:
    def __init__(
        self,
        request: OriginalRequest,
        *,
        action: RevisionAction | None = None,
        target: Any = None,
        artifacts: list[Mapping[str, Any]] | None = None,
        invocation_id: str = "layout-invocation-1",
    ) -> None:
        self.original_request = request
        self.metadata = make_metadata(
            request, action=action, target=target, invocation_id=invocation_id
        )
        self.identity = self.metadata.identity
        self.memory = StubMemory(artifacts)


def make_inputs(
    brief: Mapping[str, Any],
    request: OriginalRequest,
    *,
    action: RevisionAction | None = None,
    target: Any = None,
) -> AgentInput:
    return AgentInput(
        content=dict(brief),
        content_format=BRIEF_FORMAT,
        metadata=make_metadata(request, action=action, target=target),
    )


def memory_artifact(spec: Mapping[str, Any], *, revision: str = "rev-1") -> dict[str, Any]:
    return {
        "name": "layout",
        "reference": {
            "role": AgentRole.layout_planner.value,
            "artifact_id": "artifact-layout",
            "revision": revision,
        },
        "content": dict(spec),
        "content_format": {"name": "poster.spec", "version": "0.1.0"},
    }


def target_for(spec: Mapping[str, Any], revision: str = "rev-1") -> SimpleNamespace:
    from poster_framework import VersionDependency

    return VersionDependency(artifact_id="artifact-layout", revision=revision)
