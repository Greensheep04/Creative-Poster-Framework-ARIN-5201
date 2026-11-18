"""Contextual bridge from the completed A1 v2 to the shared A2/A3 Brief."""
from __future__ import annotations
import copy
import hashlib
import json
from creative.creative_director import registration as a1_registration, validate_brief
from poster_framework import AgentOutput, AgentRegistration, ArtifactPurpose, RevisionAction
from poster_framework.quality_planner import SOURCE_MEDIA_TYPE
from .a1_adapter import adapt_a1
from .contracts import CreativeBrief
from .registration import BRIEF_FORMAT


class _View:
    def __init__(self, original, **overrides):
        self._original = original
        self.__dict__.update(overrides)

    def __getattr__(self, name):
        return getattr(self._original, name)


def _source_bytes(raw):
    return json.dumps(raw, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")


def _previous(context):
    target = context.metadata.revision_target
    if target is None:
        raise ValueError("A1 revision requires a current brief target")
    for item in context.memory.context()["artifacts"]:
        ref = item["reference"]
        if item["name"] == "brief" and ref["artifact_id"] == target.artifact_id and ref["revision"] == target.revision:
            brief = CreativeBrief.model_validate(item["content"])
            if brief.run_id != context.original_request.run_id or brief.request_ref != context.original_request.request_id:
                raise ValueError("A1 revision brief belongs to a different request")
            notes = [n for n in brief.reference_notes if isinstance(n, dict) and n.get("source_file") == "creative-director-v2.json"]
            if len(notes) != 1:
                raise ValueError("A1 revision requires its original v2 source")
            note = notes[0]; raw = note["original_a1"]
            validate_brief(raw)
            if hashlib.sha256(_source_bytes(raw)).hexdigest() != note["source_sha256"]:
                raise ValueError("A1 original source fingerprint changed")
            return brief, raw
    raise ValueError("A1 revision target does not match the current brief")


class TeamCreativeDirector:
    def __init__(self, delegate):
        self.delegate = delegate

    async def run(self, inputs, context):
        version = 1
        delegate_context = context
        if context.metadata.revision_action == RevisionAction.revise_brief:
            previous, raw = _previous(context)
            version = previous.brief_version + 1
            # The original A1 reads its v2 brief from memory; expose a private view.
            # Shared memory, file references and version identities remain untouched.
            def memory_view():
                memory = copy.deepcopy(context.memory.context())
                for item in memory["artifacts"]:
                    if item["name"] == "brief":
                        item["content"] = copy.deepcopy(raw)
                return memory
            delegate_context = _View(context, memory=_View(context.memory, context=memory_view))
        output = await self.delegate.run(inputs, delegate_context)
        raw = output.content
        validate_brief(raw)
        if raw["keywords"] != [k.text for k in context.original_request.keywords]:
            raise ValueError("A1 keywords must preserve the original request in order")
        data = _source_bytes(raw)
        brief = adapt_a1(data, context.original_request, "creative-director-v2.json")
        brief["brief_version"] = version
        brief["intent"].update({key: copy.deepcopy(raw[key]) for key in ("interpretation", "audience", "objective", "palette")})
        brief["visual_requirements"] = {**brief["visual_requirements"], "avoid": list(raw["avoid"])}
        CreativeBrief.model_validate(brief)
        source = context.files.write_bytes(context.files.invocation_path("original-a1.json"), data,
            purpose=ArtifactPurpose.evidence, media_type=SOURCE_MEDIA_TYPE)
        return AgentOutput(content=brief, content_format=BRIEF_FORMAT, artifacts=(*output.artifacts, source))


def creative_registration(*, offline=False, model=None, options=None):
    original = a1_registration(offline=offline, model=model)
    def factory(config):
        if options is not None and not offline:
            from creative.creative_director import CreativeDirectorAgent
            delegate = CreativeDirectorAgent(model=config["model"], **options)
        else:
            delegate = original.instance_factory(config)
        return TeamCreativeDirector(delegate)
    return AgentRegistration(role=original.role,
        instance_factory=factory,
        input_formats=original.input_formats, output_formats=frozenset({BRIEF_FORMAT}),
        output_validator=lambda content: CreativeBrief.model_validate(content), config=original.config)
