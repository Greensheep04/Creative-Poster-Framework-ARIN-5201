import asyncio, importlib.util, json, subprocess, sys
from pathlib import Path
from poster_framework import (
    OriginalRequest,
    AgentRegistration,
    AgentRole,
    WORKFLOW_REQUEST_FORMAT,
    run_single_agent_from_request,
    run_single_agent_from_upstream,
    ExecutionIdentity,
    ExecutionMode,
    RunSession,
)

ROOT = Path(__file__).resolve().parents[2]


def test_upstream_record_cli_produces_real_png(tmp_path):
    assert (ROOT / "src/poster_visual_producer/__main__.py").exists(), (
        "single-agent CLI required"
    )
    from examples.visual.workflow_demo import DemoCreativeDirector, DemoLayoutPlanner
    from poster_visual_producer.registration import BRIEF_FORMAT, SPEC_FORMAT
    from .helpers import png

    cd = AgentRegistration(
        role=AgentRole.creative_director,
        instance_factory=lambda config: DemoCreativeDirector(),
        input_formats=frozenset({WORKFLOW_REQUEST_FORMAT}),
        output_formats=frozenset({BRIEF_FORMAT}),
    )
    lp = AgentRegistration(
        role=AgentRole.layout_planner,
        instance_factory=lambda config: DemoLayoutPlanner("initial"),
        input_formats=frozenset({BRIEF_FORMAT}),
        output_formats=frozenset({SPEC_FORMAT}),
    )
    req = OriginalRequest.from_keywords("single-demo", ["海洋", "宁静", "蓝色"])
    asyncio.run(
        run_single_agent_from_request(
            cd, req, root=tmp_path, record_path=tmp_path / "a1.json"
        )
    )
    asyncio.run(
        run_single_agent_from_upstream(
            lp, tmp_path / "a1.json", root=tmp_path, record_path=tmp_path / "a2.json"
        )
    )
    (tmp_path / "source.png").write_bytes(png())
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "poster_visual_producer",
            "--upstream-record",
            str(tmp_path / "a2.json"),
            "--root",
            str(tmp_path),
            "--output-record",
            str(tmp_path / "a3.json"),
            "--fonts",
            str(ROOT / "examples/visual/fonts.json"),
            "--font-base",
            str(ROOT),
            "--fixture",
            str(tmp_path / "source.png"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["status"] == "rendered" and output["source_mode"] == "offline_fixture"
    assert (tmp_path / "a3.json").is_file()


def test_shared_measurement_tool_is_available_to_layout(tmp_path, spec_data):
    assert importlib.util.find_spec("poster_visual_producer.tools"), (
        "shared typed measurement tool required"
    )
    from poster_visual_producer.tools import build_tool_registry
    from poster_visual_producer.generators import FixtureImageGenerator
    from .helpers import fonts, png

    registry = build_tool_registry(fonts(), FixtureImageGenerator(lambda req: png()))
    req = OriginalRequest.from_keywords("tools-demo", ["ocean", "blue"])
    session = RunSession(req, root=tmp_path, tool_registry=registry)
    ctx = session.create_context(
        ExecutionIdentity(
            run_id=req.run_id,
            role=AgentRole.layout_planner,
            invocation_id="layout",
            execution_mode=ExecutionMode.real,
        )
    )
    result = asyncio.run(
        ctx.tools.call(
            "poster.measure_text_block", {"element": spec_data["elements"][1]}
        )
    )
    assert result["lines"] == ["静海之蓝"] and result["ink_bounds"][0] >= 36
    assert session.usage.tool_calls == 1
