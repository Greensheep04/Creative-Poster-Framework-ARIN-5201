import asyncio
import inspect
from typing import get_type_hints

import pytest
from pydantic import ValidationError

import poster_framework as framework


def require_public_api(*names):
    missing = [name for name in names if not hasattr(framework, name)]
    assert not missing, f"missing public API: {missing}"


def make_format(name="poster.brief", version="1"):
    return framework.ContentFormat(name=name, version=version)


def make_identity(role, run_id="run-1", invocation_id="invoke-1"):
    return framework.ExecutionIdentity(
        run_id=run_id,
        role=role,
        invocation_id=invocation_id,
        execution_mode=framework.ExecutionMode.real,
    )


def make_metadata(role, interface_version=None):
    interface_version = interface_version or framework.INTERFACE_VERSION
    identity = make_identity(role)
    request = framework.OriginalRequest.from_keywords(
        "run-1",
        ["茶饮海报", "茶饮海报"],
        {"language": "zh-CN", "visual": {"palette": ["green", "cream"]}},
    )
    return framework.FrameworkMetadata(
        identity=identity,
        original_request=request,
        interface_version=interface_version,
        review_route=(framework.AgentRole.quality_critic,),
        version_dependencies=(
            framework.VersionDependency(artifact_id="brief-1", revision="rev-3"),
        ),
        files=(
            framework.FileReference(
                file_id="file-1",
                run_id="run-1",
                uri="runs/run-1/reference.png",
                revision="rev-2",
                sha256="a" * 64,
                media_type="image/png",
            ),
        ),
    )


def make_input(content, content_format=None, role=None, metadata=None, provenance=None):
    role = role or framework.AgentRole.creative_director
    return framework.AgentInput(
        content=content,
        content_format=content_format or make_format(),
        metadata=metadata or make_metadata(role),
        provenance=provenance,
    )


class RecordingAgent:
    def __init__(self, state, output_format):
        self.state = state
        self.output_format = output_format
        self.state["instances"].append(self)

    async def run(self, inputs, context):
        self.state["calls"] += 1
        self.state["received"].append(inputs)
        return framework.AgentOutput(
            content=inputs.content,
            content_format=self.output_format,
            artifacts=(),
        )


def make_registration(
    role,
    *,
    input_formats=None,
    output_formats=None,
    input_adapters=(),
    output_adapters=(),
    input_validator=None,
    output_validator=None,
    interface_versions=None,
    config=None,
    instance_factory=None,
    state=None,
):
    input_format = make_format("poster.input", "1")
    output_format = make_format("poster.output", "1")
    state = state or {"calls": 0, "instances": [], "received": []}
    if instance_factory is None:
        instance_factory = lambda agent_config: RecordingAgent(state, output_format)
    return framework.AgentRegistration(
        role=role,
        instance_factory=instance_factory,
        input_formats=frozenset(input_formats or (input_format,)),
        output_formats=frozenset(output_formats or (output_format,)),
        input_adapters=tuple(input_adapters),
        output_adapters=tuple(output_adapters),
        input_validator=input_validator,
        output_validator=output_validator,
        interface_versions=frozenset(
            interface_versions or (framework.INTERFACE_VERSION,)
        ),
        config=config or {},
    )


def make_agent_set(overrides=None):
    overrides = overrides or {}
    registrations = [
        overrides.get(role) or make_registration(role)
        for role in framework.AgentRole
    ]
    return framework.AgentSet(registrations)


def test_public_api_has_four_roles_and_the_async_agent_signature():
    require_public_api("Agent", "AgentContext", "AgentInput", "AgentOutput", "AgentRole")
    assert {role.value for role in framework.AgentRole} == {
        "creative_director",
        "layout_planner",
        "visual_producer",
        "quality_critic",
    }
    signature = inspect.signature(framework.Agent.run)
    assert tuple(signature.parameters) == ("self", "inputs", "context")
    hints = get_type_hints(framework.Agent.run)
    assert hints["inputs"] is framework.AgentInput
    assert hints["context"] is framework.AgentContext
    assert hints["return"] is framework.AgentOutput
    assert inspect.iscoroutinefunction(framework.Agent.run)


@pytest.mark.parametrize(
    "content",
    [
        {"headline": "茶饮"},
        ["茶饮", 2],
        "茶饮海报",
        7,
        1.25,
        True,
        None,
    ],
)
def test_agent_output_accepts_each_json_value_shape(content):
    require_public_api("AgentOutput", "ContentFormat", "FrameworkMetadata")
    result = framework.AgentOutput(
        content=content,
        content_format=make_format(),
        artifacts=(),
    )
    assert result.content == content


@pytest.mark.parametrize("content", [object(), ("tuple",), {1: "non-string key"}, float("nan"), float("inf")])
def test_agent_input_rejects_values_that_are_not_json(content):
    require_public_api("AgentInput", "ContentFormat", "FrameworkMetadata")
    with pytest.raises(ValidationError):
        make_input(content)


@pytest.mark.parametrize("content", [object(), ("tuple",), float("nan"), float("inf")])
def test_agent_output_rejects_values_that_are_not_json(content):
    require_public_api("AgentOutput", "ContentFormat")
    with pytest.raises(ValidationError):
        framework.AgentOutput(content=content, content_format=make_format())


def test_original_request_preserves_keywords_and_assigns_stable_distinct_ids():
    require_public_api("OriginalRequest", "CanvasSpec", "UserConstraint")
    keywords = ["  茶饮  ", "咖啡", "  茶饮  "]
    first = framework.OriginalRequest.from_keywords(
        "run-stable", keywords, {"tone": "warm", "size": ["portrait"]}
    )
    second = framework.OriginalRequest.from_keywords(
        "run-stable", keywords, {"tone": "warm", "size": ["portrait"]}
    )
    assert [item.text for item in first.keywords] == keywords
    assert [item.keyword_id for item in first.keywords] == [
        item.keyword_id for item in second.keywords
    ]
    assert len({item.keyword_id for item in first.keywords}) == 3
    assert [item.position for item in first.keywords] == [0, 1, 2]
    assert first.canvas == framework.CanvasSpec(width=720, height=1280)
    assert [(item.name, item.value) for item in first.constraints] == [
        ("tone", "warm"),
        ("size", ["portrait"]),
    ]


@pytest.mark.parametrize(
    "keywords",
    [[], ["only-one"], ["a", "b", "c", "d", "e", "f"], ["a", " "], ["a", 2]],
)
def test_original_request_rejects_invalid_keyword_count_or_value(keywords):
    require_public_api("OriginalRequest")
    with pytest.raises((ValidationError, TypeError, ValueError)):
        framework.OriginalRequest.from_keywords("run-invalid", keywords)


def test_framework_metadata_types_files_routes_usage_and_budget():
    require_public_api(
        "FrameworkMetadata",
        "FileReference",
        "RunUsage",
        "RunBudget",
        "VersionDependency",
    )
    metadata = make_metadata(framework.AgentRole.visual_producer)
    assert metadata.files[0].run_id == "run-1"
    assert metadata.files[0].revision == "rev-2"
    assert metadata.version_dependencies[0].revision == "rev-3"
    assert metadata.review_route == (framework.AgentRole.quality_critic,)
    assert metadata.usage == framework.RunUsage()
    assert metadata.budget == framework.RunBudget(
        max_revision_rounds=4,
        max_image_operations=5,
        max_critic_vision_calls=5,
    )


def test_agent_set_accepts_replaceable_factories_for_all_four_roles():
    require_public_api("AgentRegistration", "AgentSet", "AgentRole")
    state_by_role = {
        role: {"calls": 0, "instances": [], "received": []}
        for role in framework.AgentRole
    }
    registrations = [
        make_registration(role, state=state_by_role[role])
        for role in framework.AgentRole
    ]
    agents = framework.AgentSet(registrations)
    assert agents.roles == frozenset(framework.AgentRole)

    async def run_each_role():
        outputs = []
        for role in framework.AgentRole:
            inputs = make_input({"role": role.value}, make_format("poster.input", "1"), role)
            context = type("Context", (), {"identity": inputs.metadata.identity})()
            outputs.append(await agents.run_agent(role, inputs, context))
        return outputs

    results = asyncio.run(run_each_role())
    assert [result.metadata.identity.role for result in results] == list(
        framework.AgentRole
    )
    assert all(state_by_role[role]["calls"] == 1 for role in framework.AgentRole)


def test_agent_factory_creates_a_fresh_instance_for_each_invocation():
    require_public_api("AgentSet", "AgentRegistration")
    state = {"calls": 0, "instances": [], "received": []}
    role = framework.AgentRole.creative_director
    agents = make_agent_set({role: make_registration(role, state=state)})

    async def invoke(invocation_id):
        metadata = make_metadata(role)
        metadata = metadata.model_copy(
            update={
                "identity": metadata.identity.model_copy(
                    update={"invocation_id": invocation_id}
                )
            }
        )
        inputs = make_input({"title": "tea"}, make_format("poster.input", "1"), role, metadata)
        context = type("Context", (), {"identity": inputs.metadata.identity})()
        await agents.run_agent(role, inputs, context)

    asyncio.run(invoke("invoke-1"))
    asyncio.run(invoke("invoke-2"))
    assert state["calls"] == 2
    assert len(state["instances"]) == 2
    assert state["instances"][0] is not state["instances"][1]


def test_registration_rejects_missing_or_duplicate_roles():
    require_public_api("AgentRegistration", "AgentSet", "AgentSetConfigurationError")
    one = make_registration(framework.AgentRole.creative_director)
    with pytest.raises(framework.AgentSetConfigurationError):
        framework.AgentSet([one])
    with pytest.raises(framework.AgentSetConfigurationError):
        framework.AgentSet([one, one])


def test_input_conversion_only_changes_business_content_and_records_provenance():
    require_public_api(
        "ContentAdapter",
        "ContentFormat",
        "ContentProvenance",
        "AgentRegistration",
        "AgentRole",
    )
    source_format = make_format("poster.brief", "1")
    target_format = make_format("poster.brief", "2")
    provenance = framework.ContentProvenance(
        run_id="run-1",
        source_id="brief-1",
        role=framework.AgentRole.creative_director,
        invocation_id="invoke-brief",
        revision="rev-4",
    )

    def convert(content):
        content["title"] = content["title"].upper()
        return content

    adapter = framework.ContentAdapter(
        adapter_id="brief-v1-to-v2",
        source_format=source_format,
        target_format=target_format,
        transform=convert,
    )
    registration = make_registration(
        framework.AgentRole.layout_planner,
        input_formats=(target_format,),
        input_adapters=(adapter,),
    )
    original_content = {"title": "tea", "keywords": ["tea", "coffee"]}
    metadata = make_metadata(framework.AgentRole.layout_planner)
    inputs = make_input(original_content, source_format, metadata=metadata, provenance=provenance)

    prepared = registration.prepare_input(inputs)

    assert prepared.inputs.content == {"title": "TEA", "keywords": ["tea", "coffee"]}
    assert prepared.inputs.content_format == target_format
    assert prepared.inputs.metadata == metadata
    assert prepared.inputs.metadata is not metadata
    assert prepared.inputs.provenance == provenance
    assert inputs.content == {"title": "tea", "keywords": ["tea", "coffee"]}
    assert prepared.conversion.source_content == inputs.content
    assert prepared.conversion.target_content == prepared.inputs.content
    assert prepared.conversion.source_format == source_format
    assert prepared.conversion.target_format == target_format
    assert prepared.conversion.source_provenance == provenance


def test_agent_run_isolates_compatible_input_from_caller_content_and_request():
    require_public_api("AgentSet", "AgentIdentityMismatchError")
    role = framework.AgentRole.layout_planner
    input_format = make_format("poster.input", "1")
    output_format = make_format("poster.output", "1")
    state = {"factory_calls": 0, "calls": 0, "received": None}

    class MutatingAgent:
        async def run(self, inputs, context):
            state["calls"] += 1
            state["received"] = inputs
            inputs.content["nested"]["items"].append("agent-change")
            inputs.metadata.original_request.constraints[1].value["palette"].append(
                "agent-change"
            )
            return framework.AgentOutput(
                content={"accepted": True},
                content_format=output_format,
            )

    def factory(config):
        state["factory_calls"] += 1
        return MutatingAgent()

    registration = make_registration(
        role,
        input_formats=(input_format,),
        output_formats=(output_format,),
        instance_factory=factory,
    )
    agents = make_agent_set({role: registration})
    metadata = make_metadata(role)
    inputs = make_input(
        {"nested": {"items": ["caller"]}},
        input_format,
        role,
        metadata,
    )
    context = type("Context", (), {"identity": metadata.identity})()

    asyncio.run(agents.run_agent(role, inputs, context))

    assert state["factory_calls"] == 1
    assert state["calls"] == 1
    assert state["received"].content["nested"]["items"] == ["caller", "agent-change"]
    assert inputs.content == {"nested": {"items": ["caller"]}}
    assert metadata.original_request.constraints[1].value == {
        "palette": ["green", "cream"]
    }


def test_agent_run_isolates_transformed_input_from_request_and_conversion_record():
    require_public_api("AgentSet", "ContentAdapter", "ContentProvenance")
    role = framework.AgentRole.layout_planner
    source_format = make_format("poster.brief", "1")
    target_format = make_format("poster.brief", "2")
    output_format = make_format("poster.output", "1")
    state = {"factory_calls": 0, "calls": 0, "received": None}

    def convert(content):
        return {"layout": {"elements": list(content["elements"])}}

    class MutatingAgent:
        async def run(self, inputs, context):
            state["calls"] += 1
            state["received"] = inputs
            inputs.content["layout"]["elements"].append("agent-change")
            inputs.metadata.original_request.constraints[1].value["palette"].append(
                "agent-change"
            )
            return framework.AgentOutput(
                content={"accepted": True},
                content_format=output_format,
            )

    def factory(config):
        state["factory_calls"] += 1
        return MutatingAgent()

    registration = make_registration(
        role,
        input_formats=(target_format,),
        output_formats=(output_format,),
        input_adapters=(
            framework.ContentAdapter(
                adapter_id="brief-v1-to-v2",
                source_format=source_format,
                target_format=target_format,
                transform=convert,
            ),
        ),
        instance_factory=factory,
    )
    agents = make_agent_set({role: registration})
    metadata = make_metadata(role)
    original_content = {"elements": ["headline"]}
    inputs = make_input(
        original_content,
        source_format,
        role,
        metadata,
        framework.ContentProvenance(run_id="run-1", source_id="brief-1"),
    )
    context = type("Context", (), {"identity": metadata.identity})()

    result = asyncio.run(agents.run_agent(role, inputs, context))

    assert state["factory_calls"] == 1
    assert state["calls"] == 1
    assert state["received"].content["layout"]["elements"] == [
        "headline",
        "agent-change",
    ]
    assert inputs.content == {"elements": ["headline"]}
    assert metadata.original_request.constraints[1].value == {
        "palette": ["green", "cream"]
    }
    assert result.input_conversion.source_content == {"elements": ["headline"]}
    assert result.input_conversion.target_content == {
        "layout": {"elements": ["headline"]}
    }


def test_input_provenance_run_mismatch_fails_before_conversion_or_factory():
    require_public_api("AgentSet", "AgentIdentityMismatchError", "ContentAdapter")
    role = framework.AgentRole.layout_planner
    source_format = make_format("poster.brief", "1")
    target_format = make_format("poster.brief", "2")
    output_format = make_format("poster.output", "1")
    state = {
        "factory_calls": 0,
        "calls": 0,
        "conversion_calls": 0,
        "instances": [],
        "received": [],
    }

    def convert(content):
        state["conversion_calls"] += 1
        return {"title": content["title"]}

    def factory(config):
        state["factory_calls"] += 1
        return RecordingAgent(state, output_format)

    registration = make_registration(
        role,
        input_formats=(target_format,),
        input_adapters=(
            framework.ContentAdapter(
                adapter_id="brief-v1-to-v2",
                source_format=source_format,
                target_format=target_format,
                transform=convert,
            ),
        ),
        instance_factory=factory,
        state=state,
    )
    agents = make_agent_set({role: registration})
    inputs = make_input(
        {"title": "tea"},
        source_format,
        role,
        provenance=framework.ContentProvenance(
            run_id="run-2",
            source_id="brief-from-another-run",
        ),
    )
    context = type("Context", (), {"identity": inputs.metadata.identity})()

    with pytest.raises(framework.AgentIdentityMismatchError):
        asyncio.run(agents.run_agent(role, inputs, context))

    assert state["conversion_calls"] == 0
    assert state["factory_calls"] == 0
    assert state["calls"] == 0


def test_missing_input_conversion_fails_before_factory_or_agent_call():
    require_public_api(
        "AgentSet",
        "ContentFormatCompatibilityError",
        "AgentRole",
    )
    state = {"factory_calls": 0, "calls": 0, "instances": [], "received": []}

    def factory(config):
        state["factory_calls"] += 1
        return RecordingAgent(state, make_format("poster.output", "1"))

    role = framework.AgentRole.layout_planner
    registration = make_registration(
        role,
        input_formats=(make_format("poster.brief", "2"),),
        instance_factory=factory,
        state=state,
    )
    agents = make_agent_set({role: registration})
    inputs = make_input(
        {"title": "tea"},
        make_format("poster.brief", "1"),
        role,
    )
    context = type("Context", (), {"identity": inputs.metadata.identity})()

    with pytest.raises(framework.ContentFormatCompatibilityError):
        asyncio.run(agents.run_agent(role, inputs, context))

    assert state["factory_calls"] == 0
    assert state["calls"] == 0


def test_interface_version_mismatch_fails_before_factory_or_agent_call():
    require_public_api("InterfaceVersionCompatibilityError")
    state = {"factory_calls": 0, "calls": 0, "instances": [], "received": []}

    def factory(config):
        state["factory_calls"] += 1
        return RecordingAgent(state, make_format("poster.output", "1"))

    role = framework.AgentRole.layout_planner
    registration = make_registration(
        role,
        interface_versions=("2.0.0",),
        instance_factory=factory,
        state=state,
    )
    agents = make_agent_set({role: registration})
    metadata = make_metadata(role, interface_version="1.0.0")
    inputs = make_input({"title": "tea"}, make_format("poster.input", "1"), role, metadata)
    context = type("Context", (), {"identity": inputs.metadata.identity})()

    with pytest.raises(framework.InterfaceVersionCompatibilityError):
        asyncio.run(agents.run_agent(role, inputs, context))

    assert state["factory_calls"] == 0
    assert state["calls"] == 0


def test_output_conversion_preserves_review_artifacts_and_source_identity():
    require_public_api("ContentAdapter", "ReviewRecord", "KeywordReviewResult")
    role = framework.AgentRole.quality_critic
    source_format = make_format("poster.review", "1")
    target_format = make_format("poster.review", "2")
    metadata = make_metadata(role)
    artifact = framework.ArtifactReference(
        artifact_id="candidate-1",
        run_id="run-1",
        revision="rev-9",
        role=framework.AgentRole.visual_producer,
        uri="runs/run-1/candidate.png",
        sha256="b" * 64,
        media_type="image/png",
    )
    review = framework.ReviewRecord(
        candidate_id="candidate-1",
        candidate_revision="rev-9",
        decision="pass",
        keyword_results=(
            framework.KeywordReviewResult(
                keyword_id=metadata.original_request.keywords[0].keyword_id,
                status="pass",
            ),
        ),
    )

    def convert(content):
        return {"summary": content["summary"], "checked": True}

    adapter = framework.ContentAdapter(
        adapter_id="review-v1-to-v2",
        source_format=source_format,
        target_format=target_format,
        transform=convert,
    )
    registration = make_registration(
        role,
        output_formats=(source_format,),
        output_adapters=(adapter,),
    )
    output = framework.AgentOutput(
        content={"summary": "passed"},
        content_format=source_format,
        artifacts=(artifact,),
        review=review,
    )

    prepared = registration.prepare_output(output, metadata, target_format)

    assert prepared.output.content == {"summary": "passed", "checked": True}
    assert prepared.output.content_format == target_format
    assert prepared.output.artifacts == (artifact,)
    assert prepared.output.review.decision == review.decision
    assert prepared.output.review.candidate_revision == "rev-9"
    assert prepared.metadata.identity == metadata.identity
    assert prepared.output.review.reviewer_identity == metadata.identity
    assert prepared.conversion.source_provenance.role == role
    assert prepared.conversion.source_provenance.invocation_id == metadata.identity.invocation_id


def test_business_validator_runs_after_input_conversion():
    require_public_api("ContentAdapter", "AgentRegistration")
    source_format = make_format("poster.brief", "1")
    target_format = make_format("poster.brief", "2")

    def validate(content):
        if "required" not in content:
            raise ValueError("required business field is missing")

    registration = make_registration(
        framework.AgentRole.layout_planner,
        input_formats=(target_format,),
        input_adapters=(
            framework.ContentAdapter(
                adapter_id="add-required",
                source_format=source_format,
                target_format=target_format,
                transform=lambda content: {"title": content["title"]},
            ),
        ),
        input_validator=validate,
    )
    inputs = make_input({"title": "tea"}, source_format, framework.AgentRole.layout_planner)
    with pytest.raises(ValueError, match="required business field"):
        registration.prepare_input(inputs)


def test_output_conversion_requires_a_declared_adapter():
    require_public_api("ContentFormatCompatibilityError")
    role = framework.AgentRole.visual_producer
    source_format = make_format("poster.asset", "1")
    target_format = make_format("poster.asset", "2")
    registration = make_registration(role, output_formats=(source_format,))
    output = framework.AgentOutput(
        content={"asset": "file.png"},
        content_format=source_format,
        artifacts=(),
    )

    with pytest.raises(framework.ContentFormatCompatibilityError):
        registration.prepare_output(
            output,
            make_metadata(role),
            target_format,
        )


def test_unreachable_requested_output_format_fails_before_factory_or_agent_call():
    require_public_api("AgentSet", "ContentFormatCompatibilityError")
    role = framework.AgentRole.visual_producer
    source_format = make_format("poster.asset", "1")
    target_format = make_format("poster.asset", "2")
    state = {"factory_calls": 0, "calls": 0, "instances": [], "received": []}

    def factory(config):
        state["factory_calls"] += 1
        return RecordingAgent(state, source_format)

    registration = make_registration(
        role,
        output_formats=(source_format,),
        instance_factory=factory,
        state=state,
    )
    agents = make_agent_set({role: registration})
    inputs = make_input(
        {"task": "render"},
        make_format("poster.input", "1"),
        role,
    )
    context = type("Context", (), {"identity": inputs.metadata.identity})()

    with pytest.raises(framework.ContentFormatCompatibilityError):
        asyncio.run(
            agents.run_agent(
                role,
                inputs,
                context,
                output_format=target_format,
            )
        )

    assert state["factory_calls"] == 0
    assert state["calls"] == 0


def test_reachable_requested_output_format_uses_declared_adapter():
    require_public_api("AgentSet", "ContentAdapter")
    role = framework.AgentRole.visual_producer
    source_format = make_format("poster.asset", "1")
    target_format = make_format("poster.asset", "2")
    state = {"factory_calls": 0, "calls": 0, "instances": [], "received": []}

    def factory(config):
        state["factory_calls"] += 1
        return RecordingAgent(state, source_format)

    registration = make_registration(
        role,
        output_formats=(source_format,),
        output_adapters=(
            framework.ContentAdapter(
                adapter_id="asset-v1-to-v2",
                source_format=source_format,
                target_format=target_format,
                transform=lambda content: {"asset": content["asset"], "ready": True},
            ),
        ),
        instance_factory=factory,
        state=state,
    )
    agents = make_agent_set({role: registration})
    inputs = make_input(
        {"asset": "file.png"},
        make_format("poster.input", "1"),
        role,
    )
    context = type("Context", (), {"identity": inputs.metadata.identity})()

    result = asyncio.run(
        agents.run_agent(
            role,
            inputs,
            context,
            output_format=target_format,
        )
    )

    assert state["factory_calls"] == 1
    assert state["calls"] == 1
    assert result.output.content == {"asset": "file.png", "ready": True}
    assert result.output.content_format == target_format
    assert result.output_conversion.target_content == {
        "asset": "file.png",
        "ready": True,
    }


def test_output_business_validator_checks_adapted_content():
    require_public_api("ContentAdapter")
    role = framework.AgentRole.visual_producer
    source_format = make_format("poster.asset", "1")
    target_format = make_format("poster.asset", "2")

    def validate(content):
        if content.get("approved") is not True:
            raise ValueError("approved must be true")

    registration = make_registration(
        role,
        output_formats=(source_format,),
        output_adapters=(
            framework.ContentAdapter(
                adapter_id="strip-approval",
                source_format=source_format,
                target_format=target_format,
                transform=lambda content: {"asset": content["asset"]},
            ),
        ),
        output_validator=validate,
    )
    output = framework.AgentOutput(
        content={"asset": "file.png", "approved": True},
        content_format=source_format,
    )
    with pytest.raises(ValueError, match="approved must be true"):
        registration.prepare_output(output, make_metadata(role), target_format)


def test_interface_format_and_artifact_revision_are_independent_versions():
    require_public_api(
        "InterfaceVersionCompatibilityError",
        "ContentFormatCompatibilityError",
        "ArtifactReference",
        "VersionDependency",
    )
    role = framework.AgentRole.creative_director
    content_format = make_format("poster.brief", "v7")
    registration = make_registration(
        role,
        input_formats=(content_format,),
        interface_versions=("2.0.0",),
    )
    metadata = make_metadata(role, interface_version="2.0.0")
    inputs = make_input({"title": "tea"}, content_format, metadata=metadata)
    prepared = registration.prepare_input(inputs)
    assert prepared.inputs.content_format.version == "v7"
    assert prepared.inputs.metadata.interface_version == "2.0.0"
    assert prepared.inputs.metadata.version_dependencies[0].revision == "rev-3"

    incompatible_metadata = make_metadata(role, interface_version="3.0.0")
    incompatible = make_input({"title": "tea"}, content_format, metadata=incompatible_metadata)
    with pytest.raises(framework.InterfaceVersionCompatibilityError):
        registration.prepare_input(incompatible)

    artifact = framework.ArtifactReference(
        artifact_id="poster-1",
        run_id="run-1",
        revision="rev-11",
        role=role,
        uri="runs/run-1/poster.png",
        sha256="c" * 64,
        media_type="image/png",
    )
    assert artifact.revision == "rev-11"
    assert artifact.revision != content_format.version


def test_registration_snapshot_isolated_from_later_configuration_mutation():
    require_public_api("AgentSet", "AgentSetSnapshot", "AgentRegistrationSnapshot")
    observed_configs = []
    output_format = make_format("poster.output", "1")

    def make_factory(label):
        def factory(config):
            observed_configs.append((label, config))
            state = {"calls": 0, "instances": [], "received": []}
            return RecordingAgent(state, output_format)

        return factory

    role = framework.AgentRole.creative_director
    registration = make_registration(
        role,
        config={"generation": {"temperature": 0.4}},
        instance_factory=make_factory("original"),
    )
    agents = make_agent_set({role: registration})
    first_snapshot = agents.snapshot()
    registration.config["generation"]["temperature"] = 0.8
    second_snapshot = agents.snapshot()
    replacement_registration = make_registration(
        role,
        config={"generation": {"temperature": 0.6}},
        instance_factory=make_factory("replacement"),
    )
    replacement_snapshot = make_agent_set({role: replacement_registration}).snapshot()

    first_config = first_snapshot.registration_for(role).config
    first_config["generation"]["temperature"] = 9.0
    assert first_snapshot.registration_for(role).config["generation"]["temperature"] == 0.4
    assert second_snapshot.registration_for(role).config["generation"]["temperature"] == 0.8

    async def run_snapshot(snapshot):
        inputs = make_input({"title": "tea"}, make_format("poster.input", "1"), role)
        context = type("Context", (), {"identity": inputs.metadata.identity})()
        return await snapshot.run_agent(role, inputs, context)

    asyncio.run(run_snapshot(first_snapshot))
    asyncio.run(run_snapshot(first_snapshot))
    asyncio.run(run_snapshot(second_snapshot))
    asyncio.run(run_snapshot(replacement_snapshot))
    assert [
        (label, config["generation"]["temperature"])
        for label, config in observed_configs
    ] == [
        ("original", 0.4),
        ("original", 0.4),
        ("original", 0.8),
        ("replacement", 0.6),
    ]


def test_agent_set_rejects_call_identity_mismatch_before_factory():
    require_public_api("AgentIdentityMismatchError")
    state = {"factory_calls": 0, "calls": 0, "instances": [], "received": []}

    def factory(config):
        state["factory_calls"] += 1
        return RecordingAgent(state, make_format("poster.output", "1"))

    role = framework.AgentRole.layout_planner
    agents = make_agent_set(
        {
            role: make_registration(
                role,
                instance_factory=factory,
                state=state,
            )
        }
    )
    inputs = make_input(
        {"title": "tea"},
        make_format("poster.input", "1"),
        role,
    )
    context = type("Context", (), {"identity": make_identity(role, invocation_id="wrong")})()

    with pytest.raises(framework.AgentIdentityMismatchError):
        asyncio.run(agents.run_agent(role, inputs, context))

    assert state["factory_calls"] == 0
    assert state["calls"] == 0
