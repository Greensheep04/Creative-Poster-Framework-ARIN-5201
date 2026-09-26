from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, ValidationError

import poster_framework as framework


def require_public_api(*names: str) -> None:
    missing = [name for name in names if not hasattr(framework, name)]
    assert not missing, f"missing public API: {missing}"


def request(run_id: str = "run-tools") -> framework.OriginalRequest:
    return framework.OriginalRequest.from_keywords(
        run_id,
        ["tea", "calm"],
        constraints={"palette": {"colors": ["green", "cream"]}},
    )


class ToolShape(BaseModel):
    width: int
    height: int


class ToolAreaResult(BaseModel):
    area: int


def test_registry_runs_sync_async_and_pydantic_tools_with_role_context(tmp_path):
    require_public_api("RunSession", "ToolRegistry", "RunBudget")
    registry = framework.ToolRegistry()
    state = {"sync": 0, "async": 0, "typed": 0}

    def make_label(text: str, count: int = 1) -> str:
        state["sync"] += 1
        return text * count

    registry.register(
        "text.make_label",
        make_label,
        description="Repeat a label.",
        allowed_roles={framework.AgentRole.layout_planner},
    )

    @registry.tool(
        name="text.decorate",
        description="Decorate a label.",
        allowed_roles={framework.AgentRole.layout_planner},
    )
    async def decorate(text: str) -> str:
        state["async"] += 1
        return f"<{text}>"

    class PaletteInput(BaseModel):
        colors: list[str]

    def palette_names(value: PaletteInput) -> list[str]:
        state["typed"] += 1
        return value.colors

    registry.register(
        "palette.names",
        palette_names,
        description="Return palette names.",
        allowed_roles={framework.AgentRole.layout_planner},
        input_model=PaletteInput,
        output_type=list[str],
    )

    identity = framework.ExecutionIdentity(
        run_id="run-tools",
        role=framework.AgentRole.layout_planner,
        invocation_id="layout-1",
        execution_mode=framework.ExecutionMode.fake,
    )
    session = framework.RunSession(
        request("run-tools"),
        root=tmp_path,
        tool_registry=registry,
        budget=framework.RunBudget(max_tool_calls=3),
    )
    context = session.create_context(identity)

    result = asyncio.run(context.tools.call("text.make_label", {"text": "A", "count": 2}))
    decorated = asyncio.run(context.tools.call("text.decorate", {"text": result}))
    names = asyncio.run(
        context.tools.call("palette.names", {"colors": ["green", "cream"]})
    )

    assert result == "AA"
    assert decorated == "<AA>"
    assert names == ["green", "cream"]
    assert state == {"sync": 1, "async": 1, "typed": 1}
    assert [event.name for event in session.tool_events] == [
        "text.make_label",
        "text.decorate",
        "palette.names",
    ]
    assert all(event.status == "succeeded" for event in session.tool_events)
    assert session.usage.tool_calls == 3
    assert registry.parameter_schema("text.make_label")["properties"]["count"][
        "default"
    ] == 1


def test_run_session_uses_a_snapshot_of_registered_tools(tmp_path):
    registry = framework.ToolRegistry()

    def lower(value: str) -> str:
        return value.lower()

    def upper(value: str) -> str:
        return value.upper()

    registry.register(
        "text.lower",
        lower,
        description="Return lowercase text.",
        allowed_roles={framework.AgentRole.layout_planner},
    )
    first_session = framework.RunSession(
        request("run-tool-snapshot-1"), root=tmp_path, tool_registry=registry
    )
    registry.register(
        "text.upper",
        upper,
        description="Return uppercase text.",
        allowed_roles={framework.AgentRole.layout_planner},
    )
    second_session = framework.RunSession(
        request("run-tool-snapshot-2"), root=tmp_path / "second", tool_registry=registry
    )

    def context_for(session, run_id):
        return session.create_context(
            framework.ExecutionIdentity(
                run_id=run_id,
                role=framework.AgentRole.layout_planner,
                invocation_id="layout-1",
                execution_mode=framework.ExecutionMode.fake,
            )
        )

    first = context_for(first_session, "run-tool-snapshot-1")
    second = context_for(second_session, "run-tool-snapshot-2")

    assert asyncio.run(first.tools.call("text.lower", {"value": "Poster"})) == "poster"
    with pytest.raises(framework.ToolInvocationError):
        asyncio.run(first.tools.call("text.upper", {"value": "Poster"}))
    assert asyncio.run(second.tools.call("text.upper", {"value": "Poster"})) == "POSTER"
    assert [event.status for event in first_session.tool_events] == ["succeeded", "rejected"]
    assert [event.status for event in second_session.tool_events] == ["succeeded"]


def test_tool_calls_preserve_declared_python_input_and_output_types(tmp_path):
    registry = framework.ToolRegistry()
    received_shapes = []

    def shape_area(shape: ToolShape) -> int:
        received_shapes.append(type(shape))
        return shape.width * shape.height

    def image_bytes(value: int) -> bytes:
        return bytes([value, 0, 255])

    def dimensions(value: int) -> tuple[int, str]:
        return (value, "pixels")

    def area_result(value: int) -> ToolAreaResult:
        return ToolAreaResult(area=value)

    for name, function in (
        ("shape.area", shape_area),
        ("image.bytes", image_bytes),
        ("image.dimensions", dimensions),
        ("image.area_result", area_result),
    ):
        registry.register(
            name,
            function,
            description="Return typed tool data.",
            allowed_roles={framework.AgentRole.visual_producer},
        )

    session = framework.RunSession(
        request("run-tool-types"), root=tmp_path, tool_registry=registry
    )
    context = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-tool-types",
            role=framework.AgentRole.visual_producer,
            invocation_id="producer-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )

    area = asyncio.run(
        context.tools.call("shape.area", {"shape": {"width": 3, "height": 4}})
    )
    binary = asyncio.run(context.tools.call("image.bytes", {"value": 7}))
    size = asyncio.run(context.tools.call("image.dimensions", {"value": 12}))
    structured = asyncio.run(context.tools.call("image.area_result", {"value": 12}))

    assert area == 12
    assert received_shapes == [ToolShape]
    assert type(binary) is bytes
    assert binary == b"\x07\x00\xff"
    assert type(size) is tuple
    assert size == (12, "pixels")
    assert isinstance(structured, ToolAreaResult)
    assert structured == ToolAreaResult(area=12)
    record_path = tmp_path / "run-tool-types.json"
    session.save(record_path)
    restored = framework.RunSession.load(
        record_path, root=tmp_path, tool_registry=registry
    )
    assert {event.name: event.result for event in restored.tool_events} == {
        "shape.area": 12,
        "image.bytes": "[NON_JSON:bytes]",
        "image.dimensions": [12, "pixels"],
        "image.area_result": {"area": 12},
    }


def test_tool_boundaries_reject_invalid_input_unknown_role_duplicate_and_output(tmp_path):
    require_public_api("RunSession", "ToolRegistry", "ToolRegistrationError")
    registry = framework.ToolRegistry()
    calls = {"allowed": 0, "wrong_output": 0}

    def allowed(text: str) -> str:
        calls["allowed"] += 1
        return text

    registry.register(
        "text.allowed",
        allowed,
        description="Return text.",
        allowed_roles={framework.AgentRole.layout_planner},
    )

    def wrong_output(value: int) -> str:
        calls["wrong_output"] += 1
        return str(value)

    registry.register(
        "number.wrong_output",
        wrong_output,
        description="Return an invalid value.",
        allowed_roles={framework.AgentRole.layout_planner},
        output_type=int,
    )

    with pytest.raises(framework.ToolRegistrationError):
        registry.register(
            "text.allowed",
            allowed,
            description="Duplicate.",
            allowed_roles={framework.AgentRole.layout_planner},
        )

    session = framework.RunSession(request("run-tools"), root=tmp_path, tool_registry=registry)
    planner = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-tools",
            role=framework.AgentRole.layout_planner,
            invocation_id="layout-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    critic = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-tools",
            role=framework.AgentRole.quality_critic,
            invocation_id="critic-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )

    with pytest.raises(ValidationError):
        asyncio.run(planner.tools.call("text.allowed", {"text": 5}))
    with pytest.raises(framework.ToolInvocationError):
        asyncio.run(planner.tools.call("missing.tool", {}))
    with pytest.raises(framework.ToolInvocationError):
        asyncio.run(critic.tools.call("text.allowed", {"text": "blocked"}))
    with pytest.raises(ValidationError):
        asyncio.run(planner.tools.call("number.wrong_output", {"value": 4}))

    assert calls == {"allowed": 0, "wrong_output": 1}
    assert [event.status for event in session.tool_events] == [
        "rejected",
        "rejected",
        "rejected",
        "failed",
    ]
    assert session.usage.tool_calls == 1


def test_tool_failure_and_budget_rejection_are_recorded_without_hiding_errors(tmp_path):
    require_public_api("RunSession", "ToolRegistry", "BudgetExceededError")
    registry = framework.ToolRegistry()
    calls = {"explode": 0, "later": 0}

    def explode(value: str) -> str:
        calls["explode"] += 1
        raise RuntimeError("tool failed")

    registry.register(
        "work.explode",
        explode,
        description="Raise after invocation.",
        allowed_roles={framework.AgentRole.visual_producer},
    )

    def later(value: str) -> str:
        calls["later"] += 1
        return value

    registry.register(
        "work.later",
        later,
        description="Run after budget use.",
        allowed_roles={framework.AgentRole.visual_producer},
    )
    session = framework.RunSession(
        request("run-tools"),
        root=tmp_path,
        tool_registry=registry,
        budget=framework.RunBudget(max_tool_calls=1),
    )
    context = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-tools",
            role=framework.AgentRole.visual_producer,
            invocation_id="producer-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )

    with pytest.raises(RuntimeError, match="tool failed"):
        asyncio.run(context.tools.call("work.explode", {"value": "x"}))
    with pytest.raises(framework.BudgetExceededError):
        asyncio.run(context.tools.call("work.later", {"value": "x"}))

    assert calls == {"explode": 1, "later": 0}
    assert [event.status for event in session.tool_events] == ["failed", "denied"]
    assert session.tool_events[0].duration_ms >= 0
    assert session.usage.tool_calls == 1


def test_nested_model_calls_share_counters_and_keep_typed_call_events(tmp_path):
    require_public_api("ModelCallEvent", "RunSession", "ToolRegistry")
    registry = framework.ToolRegistry()
    model_calls = []

    async def responder(provider, model, request_data, parameters):
        model_calls.append((provider, model, request_data, parameters))
        return {"text": request_data.get("prompt", request_data.get("candidate", ""))}

    @registry.tool(
        name="image.prepare",
        description="Prepare an image with one model call.",
        allowed_roles={framework.AgentRole.visual_producer},
    )
    async def prepare_image(context: framework.AgentContext, prompt: str) -> str:
        result = await context.call_model(
            provider="local",
            model="vision-edit-v4",
            kind="image_operation",
            request_data={"prompt": prompt},
            parameters={"steps": 18},
            invoke=responder,
        )
        await context.call_model(
            provider="local",
            model="text-check-v2",
            request_data={"prompt": "check " + result["text"]},
            parameters={"temperature": 0},
            invoke=responder,
        )
        return result["text"]

    session = framework.RunSession(
        request("run-tools"),
        root=tmp_path,
        tool_registry=registry,
        budget=framework.RunBudget(max_model_calls=3, max_tool_calls=1),
    )
    producer = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-tools",
            role=framework.AgentRole.visual_producer,
            invocation_id="producer-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    critic = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-tools",
            role=framework.AgentRole.quality_critic,
            invocation_id="critic-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )

    assert asyncio.run(
        producer.tools.call("image.prepare", {"prompt": "green tea"})
    ) == "green tea"
    asyncio.run(
        critic.call_model(
            provider="local",
            model="critic-vision-v1",
            kind="critic_vision",
            request_data={"candidate": "candidate-1"},
            parameters={"detail": "high"},
            invoke=responder,
        )
    )

    assert len(model_calls) == 3
    assert session.usage.model_calls == 3
    assert session.usage.image_operations == 1
    assert session.usage.critic_vision_calls == 1
    assert session.usage.tool_calls == 1
    assert [event.kind for event in session.model_events] == [
        "image_operation",
        "llm",
        "critic_vision",
    ]
    assert all(event.status == "succeeded" for event in session.model_events)
    assert session.model_events[0].identity.invocation_id == "producer-1"
    assert session.model_events[2].identity.role == framework.AgentRole.quality_critic


def test_model_call_returns_the_original_non_json_response_and_agent_output_stays_json(
    tmp_path,
):
    session = framework.RunSession(request("run-model-object"), root=tmp_path)
    context = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-model-object",
            role=framework.AgentRole.visual_producer,
            invocation_id="producer-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    response = b"\x00\x89PNG\r\n"
    calls = []

    def invoke(provider, model, request_data, parameters):
        calls.append((provider, model, request_data, parameters))
        return response

    received = asyncio.run(
        context.call_model(
            provider="local",
            model="image-v1",
            request_data={"prompt": "green tea"},
            parameters={"steps": 1},
            invoke=invoke,
        )
    )

    assert received is response
    assert calls == [("local", "image-v1", {"prompt": "green tea"}, {"steps": 1})]
    assert session.usage.model_calls == 1
    assert session.model_events[0].status == "succeeded"
    assert session.model_events[0].executed is True
    with pytest.raises(ValidationError):
        framework.AgentOutput(
            content=response,
            content_format=framework.ContentFormat(name="image", version="1"),
        )
    output = framework.AgentOutput(
        content={"image_bytes": len(received)},
        content_format=framework.ContentFormat(name="image", version="1"),
    )
    assert output.content == {"image_bytes": len(response)}


def test_context_metadata_tracks_custom_run_budget_and_live_usage(tmp_path):
    run_request = request("run-metadata")
    identity = framework.ExecutionIdentity(
        run_id="run-metadata",
        role=framework.AgentRole.layout_planner,
        invocation_id="layout-1",
        execution_mode=framework.ExecutionMode.fake,
    )
    budget = framework.RunBudget(max_model_calls=1, max_image_operations=0)
    external_metadata = framework.FrameworkMetadata(
        identity=identity,
        original_request=run_request,
        budget=framework.RunBudget(max_model_calls=9),
        usage=framework.RunUsage(model_calls=8),
    )
    session = framework.RunSession(
        run_request, root=tmp_path, budget=budget
    )
    context = session.create_context(identity, metadata=external_metadata)
    calls = []

    def invoke(provider, model, request_data, parameters):
        calls.append((provider, model))
        return {"ok": True}

    assert context.metadata.budget == budget
    assert context.metadata.usage == framework.RunUsage()
    assert asyncio.run(
        context.call_model(
            provider="local",
            model="text-v1",
            request_data={"prompt": "first"},
            parameters={},
            invoke=invoke,
        )
    ) == {"ok": True}
    assert context.metadata.usage == framework.RunUsage(model_calls=1)
    with pytest.raises(framework.BudgetExceededError):
        asyncio.run(
            context.call_model(
                provider="local",
                model="text-v1",
                request_data={"prompt": "second"},
                parameters={},
                invoke=invoke,
            )
        )

    assert context.metadata.budget == budget
    assert context.metadata.usage == session.usage
    assert calls == [("local", "text-v1")]
    assert [event.status for event in session.model_events] == ["succeeded", "denied"]


@pytest.mark.parametrize("field", ["request_data", "parameters"])
@pytest.mark.parametrize(
    ("value", "marker"),
    [
        (float("nan"), "[NON_FINITE:nan]"),
        (float("inf"), "[NON_FINITE:inf]"),
    ],
)
def test_model_rejects_non_finite_input_and_persists_safe_rejection(
    tmp_path, field, value, marker
):
    run_id = "run-model-non-finite"
    session = framework.RunSession(
        request(run_id), root=tmp_path, budget=framework.RunBudget(max_model_calls=1)
    )
    context = session.create_context(
        framework.ExecutionIdentity(
            run_id=run_id,
            role=framework.AgentRole.layout_planner,
            invocation_id="layout-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    calls = []

    def invoke(provider, model, request_data, parameters):
        calls.append((provider, model, request_data, parameters))
        return {"ok": True}

    request_data = {"value": value} if field == "request_data" else {"prompt": "tea"}
    parameters = {"value": value} if field == "parameters" else {"temperature": 0.2}
    with pytest.raises(ValidationError):
        asyncio.run(
            context.call_model(
                provider="local",
                model="text-v1",
                request_data=request_data,
                parameters=parameters,
                invoke=invoke,
            )
        )

    assert calls == []
    assert session.usage.model_calls == 0
    event = session.model_events[-1]
    assert event.status == "rejected"
    assert event.executed is False
    assert getattr(event, field) == {"value": marker}

    record_path = tmp_path / "model-non-finite.json"
    session.save(record_path)
    restored = framework.RunSession.load(record_path, root=tmp_path)
    restored_event = restored.model_events[-1]
    assert restored_event.status == "rejected"
    assert restored_event.executed is False
    assert getattr(restored_event, field) == {"value": marker}
    assert restored.usage.model_calls == 0


def test_model_rejects_invalid_json_and_kind_before_counting_or_invoking(tmp_path):
    run_id = "run-model-rejections"
    session = framework.RunSession(
        request(run_id), root=tmp_path, budget=framework.RunBudget(max_model_calls=1)
    )
    context = session.create_context(
        framework.ExecutionIdentity(
            run_id=run_id,
            role=framework.AgentRole.layout_planner,
            invocation_id="layout-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    calls = []

    def invoke(provider, model, request_data, parameters):
        calls.append((provider, model, request_data, parameters))
        return {"ok": True}

    with pytest.raises(ValidationError):
        asyncio.run(
            context.call_model(
                provider="local",
                model="text-v1",
                request_data=object(),
                parameters={},
                invoke=invoke,
            )
        )
    with pytest.raises(framework.ToolInvocationError):
        asyncio.run(
            context.call_model(
                provider="local",
                model="text-v1",
                kind="unsupported",
                request_data={"prompt": "valid"},
                parameters={},
                invoke=invoke,
            )
        )

    assert calls == []
    assert session.usage.model_calls == 0
    assert [event.kind for event in session.model_events] == ["llm", "unsupported"]
    assert [event.status for event in session.model_events] == ["rejected", "rejected"]
    assert [event.executed for event in session.model_events] == [False, False]
    assert all(event.duration_ms >= 0 for event in session.model_events)
    record_path = tmp_path / "model-rejections.json"
    session.save(record_path)
    restored = framework.RunSession.load(record_path, root=tmp_path)
    assert [event.kind for event in restored.model_events] == ["llm", "unsupported"]
    assert [event.status for event in restored.model_events] == ["rejected", "rejected"]


def test_model_failure_and_exhausted_budget_record_before_propagation(tmp_path):
    require_public_api("ModelCallEvent", "BudgetExceededError", "RunSession")
    session = framework.RunSession(
        request("run-tools"),
        root=tmp_path,
        budget=framework.RunBudget(max_model_calls=1),
    )
    context = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-tools",
            role=framework.AgentRole.layout_planner,
            invocation_id="layout-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    calls = {"failed": 0, "blocked": 0}

    def failed(provider, model, request_data, parameters):
        calls["failed"] += 1
        raise RuntimeError("provider request failed")

    def blocked(provider, model, request_data, parameters):
        calls["blocked"] += 1
        return {"ok": True}

    with pytest.raises(RuntimeError, match="provider request failed"):
        asyncio.run(
            context.call_model(
                provider="local",
                model="text-v1",
                request_data={"prompt": "first"},
                parameters={"temperature": 0},
                invoke=failed,
            )
        )
    with pytest.raises(framework.BudgetExceededError):
        asyncio.run(
            context.call_model(
                provider="local",
                model="text-v1",
                request_data={"prompt": "second"},
                parameters={},
                invoke=blocked,
            )
        )

    assert calls == {"failed": 1, "blocked": 0}
    assert [event.status for event in session.model_events] == ["failed", "denied"]
    assert session.model_events[0].duration_ms >= 0
    assert session.usage.model_calls == 1


@pytest.mark.parametrize(
    ("role", "kind", "budget"),
    [
        (
            framework.AgentRole.visual_producer,
            "image_operation",
            framework.RunBudget(max_image_operations=0),
        ),
        (
            framework.AgentRole.quality_critic,
            "critic_vision",
            framework.RunBudget(max_critic_vision_calls=0),
        ),
    ],
)
def test_zero_specialized_budgets_reject_before_invoking_model(
    tmp_path, role, kind, budget
):
    session = framework.RunSession(request(), root=tmp_path, budget=budget)
    context = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-tools",
            role=role,
            invocation_id=f"{role.value}-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    calls = []

    def respond(provider, model, request_data, parameters):
        calls.append((provider, model, request_data, parameters))
        return {"ok": True}

    with pytest.raises(framework.BudgetExceededError):
        asyncio.run(
            context.call_model(
                provider="local",
                model="poster-v1",
                kind=kind,
                request_data={"prompt": "green tea"},
                parameters={},
                invoke=respond,
            )
        )

    assert calls == []
    assert session.usage.model_calls == 0
    assert session.usage.image_operations == 0
    assert session.usage.critic_vision_calls == 0
    assert session.model_events[-1].kind == kind
    assert session.model_events[-1].status == "denied"
    assert session.model_events[-1].executed is False


def test_original_request_private_messages_and_task_memory_are_isolated_by_invocation(
    tmp_path,
):
    require_public_api("TaskMemory", "MemoryReference", "ArtifactPurpose")
    source_constraints = {"palette": {"colors": ["green", "cream"]}}
    original = framework.OriginalRequest.from_keywords(
        "run-memory", ["tea", "calm"], constraints=source_constraints
    )
    session = framework.RunSession(original, root=tmp_path)
    first = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-memory",
            role=framework.AgentRole.creative_director,
            invocation_id="director-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    repeated = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-memory",
            role=framework.AgentRole.creative_director,
            invocation_id="director-2",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    other_role = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-memory",
            role=framework.AgentRole.layout_planner,
            invocation_id="layout-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    other_run = framework.RunSession(request("run-other"), root=tmp_path / "other")
    other = other_run.create_context(
        framework.ExecutionIdentity(
            run_id="run-other",
            role=framework.AgentRole.creative_director,
            invocation_id="director-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )

    first.memory.append_private_message({"role": "assistant", "content": "private A"})
    other_role.memory.append_private_message({"role": "assistant", "content": "private B"})
    other.memory.append_private_message({"role": "assistant", "content": "private C"})
    source_constraints["palette"]["colors"][0] = "purple"
    exposed_request = first.original_request
    exposed_request.constraints[0].value["colors"][0] = "orange"
    exposed_metadata = first.metadata
    exposed_metadata.original_request.constraints[0].value["colors"][0] = "black"

    assert first.memory.private_messages == (
        {"role": "assistant", "content": "private A"},
    )
    assert repeated.memory.private_messages == ()
    assert other_role.memory.private_messages == (
        {"role": "assistant", "content": "private B"},
    )
    assert other.memory.private_messages == (
        {"role": "assistant", "content": "private C"},
    )
    assert first.memory.context()["private_messages"] == [
        {"role": "assistant", "content": "private A"}
    ]
    assert repeated.memory.context()["private_messages"] == []
    assert session.original_request.keywords[0].text == "tea"
    assert session.original_request.constraints[0].value["colors"] == [
        "green",
        "cream",
    ]


def test_versioned_notes_leave_default_context_after_source_revision_changes(tmp_path):
    require_public_api("TaskMemory", "MemoryReference", "ArtifactPurpose")
    session = framework.RunSession(request("run-memory"), root=tmp_path)
    identity = framework.ExecutionIdentity(
        run_id="run-memory",
        role=framework.AgentRole.layout_planner,
        invocation_id="layout-1",
        execution_mode=framework.ExecutionMode.fake,
    )
    context = session.create_context(identity)
    director_context = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-memory",
            role=framework.AgentRole.creative_director,
            invocation_id="director-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    original = director_context.files.write_bytes(
        "creative/brief-rev1.json",
        b'{"direction":"green"}',
        purpose=framework.ArtifactPurpose.asset,
        media_type="application/json",
    )
    updated = director_context.files.write_bytes(
        "creative/brief-rev2.json",
        b'{"direction":"forest"}',
        purpose=framework.ArtifactPurpose.asset,
        media_type="application/json",
    )

    director_context.memory.publish_artifact(
        "creative",
        original,
        {"direction": "green"},
        content_format=framework.ContentFormat(name="creative_brief", version="1"),
    )
    context.memory.add_note(
        note_id="layout-note-1",
        role=framework.AgentRole.layout_planner,
        content={"note": "use rev 1"},
        based_on={original.artifact_id: original.revision},
    )
    note_reference = context.memory.note_reference("layout-note-1")
    context.memory.set_unresolved_issues(["headline is too small"])
    context.memory.add_revision(
        revision_id="revision-1",
        changes={"headline": "enlarge"},
        unresolved_issues=["headline is too small"],
    )
    director_context.memory.publish_artifact(
        "creative",
        updated,
        {"direction": "forest"},
        content_format=framework.ContentFormat(name="creative_brief", version="1"),
    )
    context.memory.add_revision(
        revision_id="revision-2",
        changes={"palette": "forest"},
        unresolved_issues=["contrast needs review"],
    )
    context.memory.add_revision(
        revision_id="revision-3",
        changes={"spacing": "increase"},
        unresolved_issues=["contrast needs review"],
    )

    view = context.memory.context()
    old_revision = context.memory.read_history(
        context.memory.history_reference("revision-1")
    )
    old_note = context.memory.read_note(note_reference)

    assert view["artifacts"][0]["reference"]["revision"] == updated.revision
    assert view["notes"] == []
    assert view["unresolved_issues"] == ["contrast needs review"]
    assert [item["revision_id"] for item in view["revision_history"]] == [
        "revision-2",
        "revision-3",
    ]
    assert old_revision.revision_id == "revision-1"
    assert old_note.content == {"note": "use rev 1"}


def test_published_artifacts_keep_formats_through_reload_and_f1_input_preflight(tmp_path):
    run_id = "run-content-formats"
    session = framework.RunSession(request(run_id), root=tmp_path)
    identity = framework.ExecutionIdentity(
        run_id=run_id,
        role=framework.AgentRole.visual_producer,
        invocation_id="producer-1",
        execution_mode=framework.ExecutionMode.fake,
    )
    context = session.create_context(identity)
    brief_format = framework.ContentFormat(name="creative_brief", version="1")
    layout_format = framework.ContentFormat(name="layout_plan", version="2")
    brief_reference = context.files.write_bytes(
        "business/brief.json",
        b'{"title":"Tea"}',
        purpose=framework.ArtifactPurpose.asset,
        media_type="application/json",
    )
    layout_reference = context.files.write_bytes(
        "business/layout.json",
        b'{"columns":3}',
        purpose=framework.ArtifactPurpose.asset,
        media_type="application/json",
    )
    context.memory.publish_artifact(
        "creative",
        brief_reference,
        {"title": "Tea"},
        content_format=brief_format,
    )
    context.memory.publish_artifact(
        "layout",
        layout_reference,
        {"columns": 3},
        content_format=layout_format,
    )
    record_path = tmp_path / "content-formats.json"
    session.save(record_path)
    restored = framework.RunSession.load(record_path, root=tmp_path)
    restored_context = restored.create_context(identity)
    artifacts = {
        item["name"]: item
        for item in restored_context.memory.context()["artifacts"]
    }

    assert artifacts["creative"]["content_format"] == {
        "name": "creative_brief",
        "version": "1",
    }
    assert artifacts["layout"]["content_format"] == {
        "name": "layout_plan",
        "version": "2",
    }
    registration = framework.AgentRegistration(
        role=framework.AgentRole.visual_producer,
        instance_factory=lambda configuration: None,
        input_formats={layout_format},
        output_formats={framework.ContentFormat(name="poster", version="1")},
        input_adapters=(
            framework.ContentAdapter(
                adapter_id="brief-to-layout",
                source_format=brief_format,
                target_format=layout_format,
                transform=lambda content: {"columns": 2, "title": content["title"]},
            ),
        ),
    )
    brief_input = registration.prepare_input(
        framework.AgentInput(
            content=artifacts["creative"]["content"],
            content_format=framework.ContentFormat.model_validate(
                artifacts["creative"]["content_format"]
            ),
            metadata=restored_context.metadata,
        )
    )
    layout_input = registration.prepare_input(
        framework.AgentInput(
            content=artifacts["layout"]["content"],
            content_format=framework.ContentFormat.model_validate(
                artifacts["layout"]["content_format"]
            ),
            metadata=restored_context.metadata,
        )
    )

    assert brief_input.inputs.content_format == layout_format
    assert brief_input.inputs.content == {"columns": 2, "title": "Tea"}
    assert brief_input.conversion.adapter_id == "brief-to-layout"
    assert layout_input.inputs.content_format == layout_format
    assert layout_input.inputs.content == {"columns": 3}
    assert layout_input.conversion is None


def test_model_request_keeps_full_original_request_when_history_is_limited(tmp_path):
    require_public_api("TaskMemory", "RunSession")
    session = framework.RunSession(request("run-memory"), root=tmp_path)
    identity = framework.ExecutionIdentity(
        run_id="run-memory",
        role=framework.AgentRole.creative_director,
        invocation_id="director-1",
        execution_mode=framework.ExecutionMode.fake,
    )
    context = session.create_context(identity)
    delivered = []

    def invoke(provider, model, request_data, parameters):
        delivered.append(request_data)
        return {"ok": True}

    bounded = context.build_model_request(
        [
            {"role": "user", "content": "old " * 100},
            {"role": "assistant", "content": "current"},
        ],
        max_history_chars=80,
    )
    asyncio.run(
        context.call_model(
            provider="local",
            model="text-v1",
            request_data=bounded,
            parameters={},
            invoke=invoke,
        )
    )

    sent_request = delivered[0]["original_request"]
    assert [item["text"] for item in sent_request["keywords"]] == ["tea", "calm"]
    assert [item["keyword_id"] for item in sent_request["keywords"]] == [
        item.keyword_id for item in session.original_request.keywords
    ]
    assert sent_request["constraints"][0]["value"] == {
        "colors": ["green", "cream"]
    }
    assert delivered[0]["history"] == [{"role": "assistant", "content": "current"}]


def test_run_files_preserve_purpose_source_and_revision_and_verify_bytes(tmp_path):
    require_public_api("RunFileStore", "FileSecurityError", "ArtifactPurpose")
    session = framework.RunSession(request("run-files"), root=tmp_path)
    producer = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-files",
            role=framework.AgentRole.visual_producer,
            invocation_id="producer-7",
            execution_mode=framework.ExecutionMode.fake,
        )
    )

    reference = producer.files.write_bytes(
        "candidate.png",
        b"image bytes for candidate",
        purpose=framework.ArtifactPurpose.candidate,
        media_type="image/png",
    )

    assert reference.run_id == "run-files"
    assert reference.role == framework.AgentRole.visual_producer
    assert reference.purpose == framework.ArtifactPurpose.candidate
    assert reference.source_id == "producer-7"
    assert reference.revision.startswith("file-rev-")
    assert producer.files.read(reference) == b"image bytes for candidate"
    with pytest.raises(framework.FileSecurityError):
        producer.files.write_bytes(
            "candidate.png",
            b"replacement bytes",
            purpose=framework.ArtifactPurpose.candidate,
            media_type="image/png",
        )

    changed = tmp_path / "run-files" / "candidate.png"
    changed.write_bytes(b"changed bytes")
    with pytest.raises(framework.FileSecurityError):
        producer.files.read(reference)


def test_run_files_reject_run_escape_external_symlink_and_foreign_run(tmp_path):
    require_public_api("RunFileStore", "FileSecurityError", "ArtifactPurpose")
    session = framework.RunSession(request("run-files"), root=tmp_path)
    producer = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-files",
            role=framework.AgentRole.visual_producer,
            invocation_id="producer-7",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    reference = producer.files.write_bytes(
        "safe.png",
        b"safe bytes",
        purpose=framework.ArtifactPurpose.asset,
        media_type="image/png",
    )
    with pytest.raises(framework.FileSecurityError):
        producer.files.write_bytes(
            "../../outside.png",
            b"outside bytes",
            purpose=framework.ArtifactPurpose.asset,
            media_type="image/png",
        )
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside bytes")
    linked = tmp_path / "run-files" / "linked.png"
    linked_reference = producer.files.write_bytes(
        "linked.png",
        b"registered bytes",
        purpose=framework.ArtifactPurpose.asset,
        media_type="image/png",
    )
    linked.unlink()
    linked.symlink_to(outside)
    with pytest.raises(framework.FileSecurityError):
        producer.files.read(linked_reference)
    with pytest.raises(framework.FileSecurityError):
        producer.files.read(reference.model_copy(update={"run_id": "another-run"}))


def test_run_record_persists_and_reloads_events_artifacts_conversions_and_result(tmp_path):
    require_public_api("RunRecord", "RunSession", "ArtifactPurpose")
    registry = framework.ToolRegistry()

    def save_label(text: str, api_key: str) -> dict[str, str]:
        return {"label": text, "api_key": api_key}

    registry.register(
        "text.save_label",
        save_label,
        description="Return a label.",
        allowed_roles={framework.AgentRole.visual_producer},
    )
    session = framework.RunSession(
        request("run-persist"),
        root=tmp_path,
        tool_registry=registry,
        budget=framework.RunBudget(max_model_calls=4, max_tool_calls=2),
        configuration={
            "generation": {"temperature": 0.25},
            "api_key": "config-secret-7",
        },
    )
    context = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-persist",
            role=framework.AgentRole.visual_producer,
            invocation_id="producer-12",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    reference = context.files.write_bytes(
        "candidate.png",
        b"candidate bytes",
        purpose=framework.ArtifactPurpose.candidate,
        media_type="image/png",
    )
    asyncio.run(
        context.tools.call(
            "text.save_label",
            {"text": "poster", "api_key": "tool-secret-6"},
        )
    )

    def responder(provider, model, request_data, parameters):
        return {"accepted": True}

    asyncio.run(
        context.call_model(
            provider="local",
            model="image-model-v5",
            kind="image_operation",
            request_data={"prompt": "draw", "token": "request-secret-1"},
            parameters={"steps": 22, "api_key": "parameter-secret-2"},
            invoke=responder,
        )
    )
    session.record_conversion(
        framework.ContentConversionRecord(
            adapter_id="brief-v1-v2",
            source_content={"authorization": "conversion-secret-3", "title": "Tea"},
            source_format=framework.ContentFormat(name="brief", version="1"),
            target_content={"title": "Tea"},
            target_format=framework.ContentFormat(name="brief", version="2"),
            source_provenance=framework.ContentProvenance(
                run_id="run-persist",
                source_id="producer-12",
                role=framework.AgentRole.visual_producer,
                invocation_id="producer-12",
            ),
        )
    )
    context.memory.append_private_message(
        {"role": "assistant", "content": "saved", "password": "memory-secret-4"}
    )
    session.set_final_result(
        {"candidate": reference.artifact_id, "secret": "result-secret-5"}
    )
    record_path = tmp_path / "run-persist.json"
    session.save(record_path)

    stored = record_path.read_text(encoding="utf-8")
    assert all(
        secret not in stored
        for secret in (
            "config-secret-7",
            "request-secret-1",
            "parameter-secret-2",
            "conversion-secret-3",
            "memory-secret-4",
            "tool-secret-6",
            "result-secret-5",
        )
    )
    restored = framework.RunSession.load(record_path, root=tmp_path, tool_registry=registry)

    assert restored.original_request.run_id == "run-persist"
    assert restored.configuration["generation"] == {"temperature": 0.25}
    assert restored.configuration["api_key"] == "[REDACTED]"
    assert restored.usage.model_calls == 1
    assert restored.usage.image_operations == 1
    assert restored.usage.tool_calls == 1
    assert restored.model_events[0].identity.invocation_id == "producer-12"
    assert restored.model_events[0].model == "image-model-v5"
    assert restored.model_events[0].parameters == {
        "steps": 22,
        "api_key": "[REDACTED]",
    }
    assert restored.tool_events[0].name == "text.save_label"
    assert restored.tool_events[0].arguments["api_key"] == "[REDACTED]"
    assert restored.tool_events[0].result["api_key"] == "[REDACTED]"
    assert restored.conversion_records[0].source_format.version == "1"
    assert restored.final_result == {
        "candidate": reference.artifact_id,
        "secret": "[REDACTED]",
    }
    assert restored.file_store.read(reference) == b"candidate bytes"
    assert restored.memory.scope(context.identity).private_messages == (
        {"role": "assistant", "content": "saved", "password": "[REDACTED]"},
    )


def test_generation_cache_fingerprint_changes_and_revalidates_every_file(tmp_path):
    require_public_api("GenerationCache", "GenerationFingerprint", "ArtifactPurpose")
    session = framework.RunSession(request("run-cache"), root=tmp_path)
    producer = session.create_context(
        framework.ExecutionIdentity(
            run_id="run-cache",
            role=framework.AgentRole.visual_producer,
            invocation_id="producer-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    reference = producer.files.write_bytes(
        "references/source.png",
        b"reference one",
        purpose=framework.ArtifactPurpose.asset,
        media_type="image/png",
    )
    candidate = producer.files.write_bytes(
        "outputs/candidate.png",
        b"generated candidate",
        purpose=framework.ArtifactPurpose.candidate,
        media_type="image/png",
    )
    fingerprint = framework.GenerationFingerprint.build(
        provider="local",
        model="poster-v3",
        weights={"model_sha256": "weight-hash-1"},
        prompt="green tea poster",
        parameters={"steps": 18, "guidance": 4},
        reference_materials=[reference],
        processing_config={"resize": [720, 1280], "format": "png"},
    )
    cache = producer.cache
    assert cache.get(fingerprint) is None
    cache.put(fingerprint, {"status": "generated"}, artifacts=[candidate])
    hit = cache.get(fingerprint)

    assert hit.value == {"status": "generated"}
    assert hit.artifacts == (candidate,)
    variants = [
        framework.GenerationFingerprint.build(
            provider="remote",
            model="poster-v3",
            weights={"model_sha256": "weight-hash-1"},
            prompt="green tea poster",
            parameters={"steps": 18, "guidance": 4},
            reference_materials=[reference],
            processing_config={"resize": [720, 1280], "format": "png"},
        ),
        framework.GenerationFingerprint.build(
            provider="local",
            model="poster-v4",
            weights={"model_sha256": "weight-hash-1"},
            prompt="green tea poster",
            parameters={"steps": 18, "guidance": 4},
            reference_materials=[reference],
            processing_config={"resize": [720, 1280], "format": "png"},
        ),
        framework.GenerationFingerprint.build(
            provider="local",
            model="poster-v3",
            weights={"model_sha256": "weight-hash-2"},
            prompt="green tea poster",
            parameters={"steps": 18, "guidance": 4},
            reference_materials=[reference],
            processing_config={"resize": [720, 1280], "format": "png"},
        ),
        framework.GenerationFingerprint.build(
            provider="local",
            model="poster-v3",
            weights={"model_sha256": "weight-hash-1"},
            prompt="forest tea poster",
            parameters={"steps": 18, "guidance": 4},
            reference_materials=[reference],
            processing_config={"resize": [720, 1280], "format": "png"},
        ),
        framework.GenerationFingerprint.build(
            provider="local",
            model="poster-v3",
            weights={"model_sha256": "weight-hash-1"},
            prompt="green tea poster",
            parameters={"steps": 20, "guidance": 4},
            reference_materials=[reference],
            processing_config={"resize": [720, 1280], "format": "png"},
        ),
        framework.GenerationFingerprint.build(
            provider="local",
            model="poster-v3",
            weights={"model_sha256": "weight-hash-1"},
            prompt="green tea poster",
            parameters={"steps": 18, "guidance": 4},
            reference_materials=[reference.model_copy(update={"sha256": "0" * 64})],
            processing_config={"resize": [720, 1280], "format": "png"},
        ),
        framework.GenerationFingerprint.build(
            provider="local",
            model="poster-v3",
            weights={"model_sha256": "weight-hash-1"},
            prompt="green tea poster",
            parameters={"steps": 18, "guidance": 4},
            reference_materials=[reference],
            processing_config={"resize": [1080, 1920], "format": "png"},
        ),
    ]
    assert all(item.key != fingerprint.key for item in variants)
    assert all(cache.get(item) is None for item in variants)

    changed_candidate = tmp_path / "run-cache" / "outputs" / "candidate.png"
    changed_candidate.write_bytes(b"modified candidate")
    with pytest.raises(framework.FileSecurityError):
        cache.get(fingerprint)


def test_default_context_filters_artifacts_by_role_and_checks_registered_files(tmp_path):
    require_public_api("TaskMemory", "ArtifactPurpose")
    session = framework.RunSession(request("run-context"), root=tmp_path)
    contexts = {
        role: session.create_context(
            framework.ExecutionIdentity(
                run_id="run-context",
                role=role,
                invocation_id=f"{role.value}-1",
                execution_mode=framework.ExecutionMode.fake,
            )
        )
        for role in framework.AgentRole
    }

    def publish(role, name, purpose):
        context = contexts[role]
        reference = context.files.write_bytes(
            f"{name}.dat",
            f"{name} bytes".encode(),
            purpose=purpose,
            media_type="application/octet-stream",
        )
        context.memory.publish_artifact(
            name,
            reference,
            {"value": name},
            content_format=framework.ContentFormat(name=name, version="1"),
        )
        return reference

    creative = publish(
        framework.AgentRole.creative_director,
        "creative",
        framework.ArtifactPurpose.asset,
    )
    layout = publish(
        framework.AgentRole.layout_planner,
        "layout",
        framework.ArtifactPurpose.asset,
    )
    material = publish(
        framework.AgentRole.visual_producer,
        "material",
        framework.ArtifactPurpose.asset,
    )
    candidate = publish(
        framework.AgentRole.visual_producer,
        "candidate",
        framework.ArtifactPurpose.candidate,
    )
    evidence = publish(
        framework.AgentRole.quality_critic,
        "evidence",
        framework.ArtifactPurpose.evidence,
    )

    with pytest.raises(framework.FileSecurityError):
        contexts[framework.AgentRole.layout_planner].memory.publish_artifact(
            "unregistered",
            creative.model_copy(update={"artifact_id": "forged-artifact"}),
            {"value": "forged"},
            content_format=framework.ContentFormat(name="forged", version="1"),
        )

    def names_for(role):
        return {
            item["name"]
            for item in contexts[role].memory.context()["artifacts"]
        }

    assert names_for(framework.AgentRole.creative_director) == {"creative"}
    assert names_for(framework.AgentRole.layout_planner) == {"creative", "layout"}
    assert names_for(framework.AgentRole.visual_producer) == {
        "creative",
        "layout",
        "material",
    }
    assert names_for(framework.AgentRole.quality_critic) == {
        "creative",
        "layout",
        "material",
        "candidate",
        "evidence",
    }
    assert candidate.purpose == framework.ArtifactPurpose.candidate
    assert evidence.purpose == framework.ArtifactPurpose.evidence
    assert contexts[framework.AgentRole.quality_critic].memory.context()[
        "original_request"
    ]["request_id"] == session.original_request.request_id


def test_scoped_memory_rejects_cross_role_publication_without_mutating_current_artifacts(
    tmp_path,
):
    run_id = "run-artifact-owners"
    session = framework.RunSession(request(run_id), root=tmp_path)
    director = session.create_context(
        framework.ExecutionIdentity(
            run_id=run_id,
            role=framework.AgentRole.creative_director,
            invocation_id="director-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    planner = session.create_context(
        framework.ExecutionIdentity(
            run_id=run_id,
            role=framework.AgentRole.layout_planner,
            invocation_id="layout-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    critic = session.create_context(
        framework.ExecutionIdentity(
            run_id=run_id,
            role=framework.AgentRole.quality_critic,
            invocation_id="critic-1",
            execution_mode=framework.ExecutionMode.fake,
        )
    )
    original_format = framework.ContentFormat(name="creative_brief", version="1")
    updated_format = framework.ContentFormat(name="creative_brief", version="2")
    original_reference = director.files.write_bytes(
        "creative/brief-v1.json",
        b'{"direction":"green"}',
        purpose=framework.ArtifactPurpose.asset,
        media_type="application/json",
    )
    director.memory.publish_artifact(
        "creative",
        original_reference,
        {"direction": "green"},
        content_format=original_format,
    )
    planner_reference = planner.files.write_bytes(
        "layout/brief-rewrite.json",
        b'{"direction":"orange"}',
        purpose=framework.ArtifactPurpose.asset,
        media_type="application/json",
    )
    before = session.memory.snapshot()

    with pytest.raises(framework.AgentIdentityMismatchError):
        planner.memory.publish_artifact(
            "creative",
            original_reference,
            {"direction": "stolen"},
            content_format=framework.ContentFormat(name="stolen", version="9"),
        )
    assert session.memory.snapshot() == before
    with pytest.raises(framework.AgentIdentityMismatchError):
        planner.memory.publish_artifact(
            "creative",
            planner_reference,
            {"direction": "overwritten"},
            content_format=framework.ContentFormat(name="layout_plan", version="1"),
        )
    assert session.memory.snapshot() == before

    updated_reference = director.files.write_bytes(
        "creative/brief-v2.json",
        b'{"direction":"forest"}',
        purpose=framework.ArtifactPurpose.asset,
        media_type="application/json",
    )
    director.memory.publish_artifact(
        "creative",
        updated_reference,
        {"direction": "forest"},
        content_format=updated_format,
    )

    updated = next(
        item
        for item in critic.memory.context()["artifacts"]
        if item["name"] == "creative"
    )
    assert updated["content"] == {"direction": "forest"}
    assert updated["content_format"] == updated_format.model_dump(mode="json")
    assert updated["reference"]["artifact_id"] == updated_reference.artifact_id
    assert updated["reference"]["revision"] == updated_reference.revision
    current_versions = session.memory.snapshot()["current_versions"]
    assert original_reference.artifact_id not in current_versions
    assert current_versions[updated_reference.artifact_id] == updated_reference.revision

    record_path = tmp_path / "artifact-owners.json"
    session.save(record_path)
    restored = framework.RunSession.load(record_path, root=tmp_path)
    restored_critic = restored.create_context(critic.identity)
    restored_artifact = next(
        item
        for item in restored_critic.memory.context()["artifacts"]
        if item["name"] == "creative"
    )
    assert restored_artifact == updated


def test_scoped_tool_catalog_filters_role_and_keeps_run_snapshot_isolated(tmp_path):
    require_public_api("ToolDescription", "ToolRegistry", "RunSession")
    registry = framework.ToolRegistry()

    def repeat_text(text: str, repeat: int = 1) -> str:
        return text * repeat

    def inspect_image(prompt: str) -> str:
        return prompt

    def normalize_layout(value: str) -> str:
        return value.strip()

    registry.register(
        "layout.repeat_text",
        repeat_text,
        description="Repeat a layout label.",
        allowed_roles={framework.AgentRole.layout_planner},
    )
    registry.register(
        "image.inspect",
        inspect_image,
        description="Inspect an image request.",
        allowed_roles={framework.AgentRole.visual_producer},
    )
    first_session = framework.RunSession(
        request("run-tool-catalog-first"),
        root=tmp_path,
        tool_registry=registry,
    )
    layout_identity = framework.ExecutionIdentity(
        run_id="run-tool-catalog-first",
        role=framework.AgentRole.layout_planner,
        invocation_id="layout-1",
        execution_mode=framework.ExecutionMode.real,
    )
    visual_identity = framework.ExecutionIdentity(
        run_id="run-tool-catalog-first",
        role=framework.AgentRole.visual_producer,
        invocation_id="visual-1",
        execution_mode=framework.ExecutionMode.real,
    )
    layout_context = first_session.create_context(layout_identity)
    visual_context = first_session.create_context(visual_identity)

    registry.register(
        "layout.normalize",
        normalize_layout,
        description="Trim layout text.",
        allowed_roles={framework.AgentRole.layout_planner},
    )

    layout_tools = layout_context.tools.available_tools()
    visual_tools = visual_context.tools.available_tools()

    assert [tool.name for tool in layout_tools] == ["layout.repeat_text"]
    assert layout_tools[0].description == "Repeat a layout label."
    assert layout_tools[0].parameters["properties"]["repeat"]["default"] == 1
    assert [tool.name for tool in visual_tools] == ["image.inspect"]
    assert isinstance(layout_tools[0], framework.ToolDescription)

    layout_tools[0].parameters["properties"]["repeat"]["default"] = 9
    assert (
        layout_context.tools.available_tools()[0].parameters["properties"]["repeat"][
            "default"
        ]
        == 1
    )

    second_session = framework.RunSession(
        request("run-tool-catalog-second"),
        root=tmp_path / "second",
        tool_registry=registry,
    )
    second_context = second_session.create_context(
        framework.ExecutionIdentity(
            run_id="run-tool-catalog-second",
            role=framework.AgentRole.layout_planner,
            invocation_id="layout-2",
            execution_mode=framework.ExecutionMode.real,
        )
    )
    assert [tool.name for tool in second_context.tools.available_tools()] == [
        "layout.normalize",
        "layout.repeat_text",
    ]
