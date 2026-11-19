import copy, json
from pathlib import Path
from poster_framework import (
    OriginalRequest,
    RunSession,
    AgentRole,
    ExecutionIdentity,
    ExecutionMode,
    FrameworkMetadata,
    AgentInput,
    ArtifactPurpose,
    VersionDependency,
)
from poster_visual_producer.fonts import FontRegistry

ROOT = Path(__file__).resolve().parents[2]


def make_session(tmp_path, spec, brief, budget=None):
    request = OriginalRequest.from_keywords("run_demo_001", ["海洋", "宁静", "蓝色"])
    mapping = {
        old: new.keyword_id for old, new in zip(["k1", "k2", "k3"], request.keywords)
    }
    spec, brief = copy.deepcopy(spec), copy.deepcopy(brief)
    for row in brief["keyword_bindings"]:
        row["keyword_id"] = mapping[row["keyword_id"]]
    for row in spec["keyword_element_bindings"]:
        row["keyword_id"] = mapping[row["keyword_id"]]
    for row in spec["asset_plan"]:
        row["required_keyword_ids"] = [mapping[k] for k in row["required_keyword_ids"]]
    session = RunSession(request, root=tmp_path, budget=budget)
    publish(session, "brief", brief, AgentRole.creative_director, "brief1")
    publish(session, "layout", spec, AgentRole.layout_planner, "layout1")
    return session, spec, brief


def publish(session, name, content, role, invocation):
    from poster_visual_producer.registration import BRIEF_FORMAT, SPEC_FORMAT

    identity = ExecutionIdentity(
        run_id=session.run_id,
        role=role,
        invocation_id=invocation,
        execution_mode=ExecutionMode.real,
    )
    context = session.create_context(identity)
    fmt = BRIEF_FORMAT if name == "brief" else SPEC_FORMAT
    ref = context.files.write_bytes(
        context.files.invocation_path("agent-output.json"),
        json.dumps(content, ensure_ascii=False).encode(),
        purpose=ArtifactPurpose.evidence,
        media_type="application/json",
    )
    session.memory.publish_artifact(
        name, ref, content, fmt, publishing_identity=identity
    )


def call_context(session, spec, invocation, action=None, target=None):
    from poster_visual_producer.registration import SPEC_FORMAT

    identity = ExecutionIdentity(
        run_id=session.run_id,
        role=AgentRole.visual_producer,
        invocation_id=invocation,
        execution_mode=ExecutionMode.real,
    )
    memory = session.memory.scope(identity).context()
    deps = tuple(
        VersionDependency(
            artifact_id=x["reference"]["artifact_id"],
            revision=x["reference"]["revision"],
        )
        for x in memory["artifacts"]
        if x["name"] in {"brief", "layout"}
    )
    meta = FrameworkMetadata(
        identity=identity,
        original_request=session.original_request,
        version_dependencies=deps,
        revision_action=action,
        revision_target=target,
    )
    context = session.create_context(identity, meta)
    return AgentInput(content=spec, content_format=SPEC_FORMAT, metadata=meta), context


def fonts():
    return FontRegistry.from_config(
        json.loads((ROOT / "examples/visual/fonts.json").read_text(encoding="utf-8")),
        ROOT,
    )


def png(color="blue"):
    import io
    from PIL import Image

    b = io.BytesIO()
    Image.new("RGB", (900, 1180), color).save(b, "PNG")
    return b.getvalue()
