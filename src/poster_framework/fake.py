from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
from collections.abc import Mapping

from .registration import AgentRegistration, AgentSet
from .types import (
    AgentContext,
    AgentInput,
    AgentOutput,
    AgentRole,
    ArtifactPurpose,
    ArtifactReference,
    ContentFormat,
    ExecutionMode,
    KeywordReviewResult,
    KeywordReviewStatus,
    OriginalRequest,
    ReviewDecision,
    ReviewRecord,
    RevisionAction,
    VersionDependency,
)
from .workflow import FAKE_CANDIDATE_MEDIA_TYPE, WORKFLOW_REQUEST_FORMAT


FAKE_BRIEF_FORMAT = ContentFormat(name="poster_framework.fake_brief", version="1")
FAKE_LAYOUT_FORMAT = ContentFormat(name="poster_framework.fake_layout", version="1")
FAKE_VISUAL_FORMAT = ContentFormat(name="poster_framework.fake_visual", version="1")
FAKE_REVIEW_FORMAT = ContentFormat(name="poster_framework.fake_review", version="1")

_TEXT_BY_REMAINING_REVISIONS = {
    0: "可以通过审核的图片",
    1: "需要修改的图片",
    2: "需要修改两次的图片",
    3: "需要修改三次的图片",
}
_ROLES = (
    AgentRole.creative_director,
    AgentRole.layout_planner,
    AgentRole.visual_producer,
    AgentRole.quality_critic,
)


class FakePosterModel:
    def __init__(
        self,
        *,
        initial_remaining_revisions: int,
        delay_seconds: float = 15,
    ) -> None:
        if (
            isinstance(initial_remaining_revisions, bool)
            or not isinstance(initial_remaining_revisions, int)
            or initial_remaining_revisions not in _TEXT_BY_REMAINING_REVISIONS
        ):
            raise ValueError("initial_remaining_revisions must be an integer from zero to three")
        if (
            isinstance(delay_seconds, bool)
            or not isinstance(delay_seconds, (int, float))
            or not math.isfinite(delay_seconds)
            or delay_seconds < 0
        ):
            raise ValueError("delay_seconds must be a finite non-negative number")
        self.initial_remaining_revisions = initial_remaining_revisions
        self.delay_seconds = float(delay_seconds)
        self._calls: list[dict[str, object]] = []

    @property
    def calls(self) -> tuple[dict[str, object], ...]:
        return tuple(copy.deepcopy(self._calls))

    def agent_set(self) -> AgentSet:
        formats = {
            AgentRole.creative_director: (WORKFLOW_REQUEST_FORMAT, FAKE_BRIEF_FORMAT),
            AgentRole.layout_planner: (FAKE_BRIEF_FORMAT, FAKE_LAYOUT_FORMAT),
            AgentRole.visual_producer: (FAKE_LAYOUT_FORMAT, FAKE_VISUAL_FORMAT),
            AgentRole.quality_critic: (FAKE_VISUAL_FORMAT, FAKE_REVIEW_FORMAT),
        }
        return AgentSet(self.registration(role) for role in formats)

    def registration(self, role: AgentRole) -> AgentRegistration:
        if not isinstance(role, AgentRole):
            raise TypeError("role must be an AgentRole")
        formats = {
            AgentRole.creative_director: (WORKFLOW_REQUEST_FORMAT, FAKE_BRIEF_FORMAT),
            AgentRole.layout_planner: (FAKE_BRIEF_FORMAT, FAKE_LAYOUT_FORMAT),
            AgentRole.visual_producer: (FAKE_LAYOUT_FORMAT, FAKE_VISUAL_FORMAT),
            AgentRole.quality_critic: (FAKE_VISUAL_FORMAT, FAKE_REVIEW_FORMAT),
        }
        input_format, output_format = formats[role]
        return AgentRegistration(
            role=role,
            instance_factory=lambda config: _FakePosterAgent(role, self, config),
            input_formats=frozenset({input_format}),
            output_formats=frozenset({output_format}),
            config={"initial_remaining_revisions": self.initial_remaining_revisions},
        )

    async def _complete(
        self,
        provider: str,
        model: str,
        request_data: object,
        parameters: object,
    ) -> object:
        if provider != "poster-framework-fake":
            raise ValueError("FakePosterModel only accepts its fixed provider")
        if not isinstance(request_data, Mapping):
            raise TypeError("FakePosterModel request_data must be an object")
        role = AgentRole(request_data["role"])
        call_record = {
            "role": role.value,
            "model": model,
            "request_data": copy.deepcopy(dict(request_data)),
            "parameters": copy.deepcopy(parameters),
        }
        self._calls.append(call_record)
        await asyncio.sleep(self.delay_seconds)
        expected_role = self._expected_role(len(self._calls) - 1)
        if role != expected_role:
            raise ValueError(
                f"FakePosterModel expected {expected_role.value}, received {role.value}"
            )
        self._validate_request(role, request_data)
        response = self._response(role, request_data)
        encoded_response = json.dumps(
            response,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        call_record["response"] = copy.deepcopy(response)
        return encoded_response

    @staticmethod
    def _expected_role(call_index: int) -> AgentRole:
        if call_index < len(_ROLES):
            return _ROLES[call_index]
        return AgentRole.visual_producer if call_index % 2 == 0 else AgentRole.quality_critic

    @staticmethod
    def _validate_request(role: AgentRole, request_data: Mapping[str, object]) -> None:
        required = {
            "role",
            "input",
            "input_format",
            "original_request",
            "provenance",
            "revision_action",
            "revision_target",
            "public_context",
            "execution_identity",
            "model_request",
        }
        if set(request_data) != required | (
            {"candidate_reference", "candidate_content", "candidate_sha256"}
            if role == AgentRole.quality_critic
            else set()
        ):
            raise ValueError(f"{role.value} received an input outside its fixed template")
        request = OriginalRequest.model_validate(request_data["original_request"])
        execution_identity = request_data["execution_identity"]
        model_request = request_data["model_request"]
        if (
            not isinstance(execution_identity, Mapping)
            or execution_identity.get("run_id") != request.run_id
            or execution_identity.get("role") != role.value
        ):
            raise ValueError(f"{role.value} execution identity does not match its request")
        if (
            not isinstance(model_request, Mapping)
            or model_request.get("original_request") != request_data["original_request"]
            or model_request.get("context") != request_data["public_context"]
        ):
            raise ValueError(f"{role.value} model request does not match its public context")
        private_messages = model_request["context"].get("private_messages")
        if not isinstance(private_messages, list) or len(private_messages) != 1:
            raise ValueError(f"{role.value} model request requires one private message")
        private_message = private_messages[0]
        if (
            not isinstance(private_message, Mapping)
            or private_message.get("role") != role.value
            or private_message.get("invocation_id") != execution_identity.get("invocation_id")
        ):
            raise ValueError(f"{role.value} private message identity does not match its call")
        source_input = request_data["input"]
        if not isinstance(source_input, Mapping):
            raise TypeError(f"{role.value} input must be an object")
        provenance = request_data["provenance"]
        if not isinstance(provenance, Mapping) or provenance.get("run_id") != request.run_id:
            raise ValueError(f"{role.value} input source belongs to another run")
        if role == AgentRole.creative_director:
            if source_input != request.model_dump(mode="json"):
                raise ValueError("creative_director input must be the original request")
            if provenance.get("source_id") != request.request_id:
                raise ValueError("creative_director input source must identify the original request")
        elif role == AgentRole.layout_planner:
            FakePosterModel._validate_brief(source_input, request)
        elif role == AgentRole.visual_producer:
            FakePosterModel._validate_layout(source_input, request)
            FakePosterModel._validate_revision_context(request_data)
        else:
            FakePosterModel._validate_candidate(request_data, request)

    @staticmethod
    def _validate_brief(value: Mapping[str, object], request: OriginalRequest) -> None:
        expected = {
            "headline": " + ".join(item.text for item in request.keywords),
            "copy": "、".join(item.text for item in request.keywords),
            "keyword_ids": [item.keyword_id for item in request.keywords],
            "constraints": [item.model_dump(mode="json") for item in request.constraints],
        }
        if dict(value) != expected:
            raise ValueError("layout_planner input does not match the fixed brief template")

    @staticmethod
    def _validate_layout(value: Mapping[str, object], request: OriginalRequest) -> None:
        expected = {
            "headline": " + ".join(item.text for item in request.keywords),
            "copy": "、".join(item.text for item in request.keywords),
            "keyword_ids": [item.keyword_id for item in request.keywords],
            "constraints": [item.model_dump(mode="json") for item in request.constraints],
            "canvas": {"width": 720, "height": 1280},
            "blocks": ["headline", "copy"],
        }
        if dict(value) != expected:
            raise ValueError("visual_producer input does not match the fixed layout template")

    @staticmethod
    def _validate_revision_context(request_data: Mapping[str, object]) -> None:
        action = request_data["revision_action"]
        public_context = request_data["public_context"]
        if action is None:
            return
        if action != RevisionAction.rerender.value:
            raise ValueError("FakePosterModel supports only rerender revisions")
        if not isinstance(public_context, Mapping):
            raise TypeError("visual_producer public context must be an object")
        history = public_context["revision_history"]
        if not isinstance(history, list) or not history:
            raise ValueError("visual_producer requires the public rerender instruction")
        details = history[-1]["changes"]["details"]
        if not isinstance(details, Mapping):
            raise ValueError("visual_producer rerender instruction is malformed")
        remaining = details["remaining_revisions"]
        if isinstance(remaining, bool) or not isinstance(remaining, int) or remaining < 1:
            raise ValueError("visual_producer rerender instruction has no remaining revision")
        if history[-1]["changes"]["action"] != RevisionAction.rerender.value:
            raise ValueError("visual_producer received a non-rerender instruction")

    @staticmethod
    def _validate_candidate(request_data: Mapping[str, object], request: OriginalRequest) -> None:
        candidate = request_data["candidate_content"]
        source_input = request_data["input"]
        reference = request_data["candidate_reference"]
        checksum = request_data["candidate_sha256"]
        if not isinstance(candidate, Mapping) or dict(candidate) != source_input:
            raise ValueError("quality_critic input must match the current candidate file")
        remaining = candidate.get("remaining_revisions")
        image_text = candidate.get("image_text")
        if (
            isinstance(remaining, bool)
            or not isinstance(remaining, int)
            or remaining < 0
            or image_text != _TEXT_BY_REMAINING_REVISIONS.get(remaining)
        ):
            raise ValueError("quality_critic candidate does not match the fixed text template")
        if not isinstance(reference, Mapping) or reference.get("run_id") != request.run_id:
            raise ValueError("quality_critic candidate belongs to another run")
        if not isinstance(checksum, str) or checksum != reference.get("sha256"):
            raise ValueError("quality_critic candidate checksum does not match its file")

    def _response(self, role: AgentRole, request_data: Mapping[str, object]) -> dict[str, object]:
        request = OriginalRequest.model_validate(request_data["original_request"])
        if role == AgentRole.creative_director:
            return {
                "headline": " + ".join(item.text for item in request.keywords),
                "copy": "、".join(item.text for item in request.keywords),
                "keyword_ids": [item.keyword_id for item in request.keywords],
                "constraints": [item.model_dump(mode="json") for item in request.constraints],
            }
        if role == AgentRole.layout_planner:
            return {
                **dict(request_data["input"]),
                "canvas": {"width": 720, "height": 1280},
                "blocks": ["headline", "copy"],
            }
        if role == AgentRole.visual_producer:
            remaining = self._remaining_revisions(request_data)
            return {
                "image_text": _TEXT_BY_REMAINING_REVISIONS[remaining],
                "remaining_revisions": remaining,
            }
        candidate = request_data["candidate_content"]
        remaining = candidate["remaining_revisions"]
        reference = request_data["candidate_reference"]
        return {
            "candidate_id": reference["artifact_id"],
            "candidate_revision": reference["revision"],
            "image_text": candidate["image_text"],
            "remaining_revisions": remaining,
            "decision": ReviewDecision.pass_.value if remaining == 0 else ReviewDecision.fail.value,
            "action": RevisionAction.rerender.value if remaining > 0 else None,
        }

    def _remaining_revisions(self, request_data: Mapping[str, object]) -> int:
        if request_data["revision_action"] is None:
            return self.initial_remaining_revisions
        public_context = request_data["public_context"]
        history = public_context["revision_history"]
        previous_remaining = history[-1]["changes"]["details"]["remaining_revisions"]
        return previous_remaining - 1


class _FakePosterAgent:
    def __init__(
        self,
        role: AgentRole,
        model: FakePosterModel,
        configuration: Mapping[str, object],
    ) -> None:
        self._role = role
        self._model = model
        self._configuration = dict(configuration)

    async def run(self, inputs: AgentInput, context: AgentContext) -> AgentOutput:
        public_context = context.memory.context()
        request_data: dict[str, object] = {
            "role": self._role.value,
            "input": copy.deepcopy(inputs.content),
            "input_format": inputs.content_format.model_dump(mode="json"),
            "original_request": inputs.metadata.original_request.model_dump(mode="json"),
            "provenance": inputs.provenance.model_dump(mode="json") if inputs.provenance else None,
            "revision_action": (
                inputs.metadata.revision_action.value
                if inputs.metadata.revision_action is not None
                else None
            ),
            "revision_target": (
                inputs.metadata.revision_target.model_dump(mode="json")
                if inputs.metadata.revision_target is not None
                else None
            ),
            "public_context": public_context,
        }
        if self._role == AgentRole.quality_critic:
            stored = next(item for item in public_context["artifacts"] if item["name"] == "candidate")
            reference = ArtifactReference.model_validate(stored["reference"])
            candidate_bytes = context.files.read(reference)
            candidate_content = json.loads(candidate_bytes)
            if hashlib.sha256(candidate_bytes).hexdigest() != reference.sha256:
                raise ValueError("quality_critic candidate file hash does not match its reference")
            request_data.update(
                {
                    "candidate_reference": reference.model_dump(mode="json"),
                    "candidate_content": candidate_content,
                    "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
                }
            )
        private_message = {
            "role": self._role.value,
            "invocation_id": context.identity.invocation_id,
            "content": copy.deepcopy(request_data),
        }
        context.memory.append_private_message(private_message)
        model_request = context.build_model_request(
            [private_message],
            max_history_chars=100_000,
        )
        request_data["execution_identity"] = context.identity.model_dump(mode="json")
        request_data["model_request"] = model_request
        request_data["public_context"] = model_request["context"]
        raw_response = await context.call_model(
            provider="poster-framework-fake",
            model=f"fixed-template-{self._role.value}",
            request_data=request_data,
            parameters={"delay_seconds": self._model.delay_seconds},
            invoke=self._model._complete,
        )
        response = json.loads(raw_response)
        if self._role == AgentRole.creative_director:
            return AgentOutput(content=response, content_format=FAKE_BRIEF_FORMAT)
        if self._role == AgentRole.layout_planner:
            return AgentOutput(content=response, content_format=FAKE_LAYOUT_FORMAT)
        if self._role == AgentRole.visual_producer:
            encoded = json.dumps(
                response,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            candidate = context.files.write_bytes(
                context.files.invocation_path("candidate.json"),
                encoded,
                purpose=ArtifactPurpose.candidate,
                media_type=FAKE_CANDIDATE_MEDIA_TYPE,
            )
            return AgentOutput(
                content=response,
                content_format=FAKE_VISUAL_FORMAT,
                artifacts=(candidate,),
            )
        reference = ArtifactReference.model_validate(request_data["candidate_reference"])
        candidate = request_data["candidate_content"]
        remaining = candidate["remaining_revisions"]
        review = ReviewRecord(
            candidate_id=reference.artifact_id,
            candidate_revision=reference.revision,
            decision=ReviewDecision(response["decision"]),
            action=RevisionAction(response["action"]) if response["action"] else None,
            target=(
                VersionDependency(
                    artifact_id=reference.artifact_id,
                    revision=reference.revision,
                )
                if response["action"]
                else None
            ),
            keyword_results=tuple(
                KeywordReviewResult(
                    keyword_id=item.keyword_id,
                    status=KeywordReviewStatus.pass_,
                )
                for item in inputs.metadata.original_request.keywords
            ),
            details={
                "candidate_id": reference.artifact_id,
                "candidate_revision": reference.revision,
                "image_text": candidate["image_text"],
                "remaining_revisions": remaining,
            },
        )
        return AgentOutput(
            content={"checked": True, "remaining_revisions": remaining},
            content_format=FAKE_REVIEW_FORMAT,
            review=review,
        )


__all__ = [
    "FAKE_BRIEF_FORMAT",
    "FAKE_CANDIDATE_MEDIA_TYPE",
    "FAKE_LAYOUT_FORMAT",
    "FAKE_REVIEW_FORMAT",
    "FAKE_VISUAL_FORMAT",
    "FakePosterModel",
]
