from __future__ import annotations
import argparse, asyncio, importlib, json
from pathlib import Path
from poster_framework import run_single_agent_from_upstream
from .fonts import FontRegistry
from .generators import FixtureImageGenerator
from .registration import registration


async def execute(args):
    config = json.loads(args.fonts.read_text(encoding="utf-8"))
    fonts = FontRegistry.from_config(config, args.font_base)
    if args.fixture:
        # Only trusted application/user CLI configuration may provide this path.
        data = args.fixture.read_bytes()
        generator = FixtureImageGenerator(lambda request: data, model="file-fixture-v1")
    else:
        module, name = args.generator_factory.split(":", 1)
        generator = getattr(importlib.import_module(module), name)()
    result = await run_single_agent_from_upstream(
        registration(generator, fonts),
        args.upstream_record,
        root=args.root,
        record_path=args.output_record,
    )
    content = result.output.content
    return {
        "status": content["status"],
        "source_mode": content["source_mode"],
        "poster_ref": content["poster_ref"],
        "candidate_ref": content["candidate_ref"],
        "record_path": str(result.record_path),
        "usage_delta": content["usage_delta"],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Run Visual Producer from the Layout Planner's exported single-role record."
    )
    parser.add_argument("--upstream-record", type=Path, required=True)
    parser.add_argument(
        "--root", type=Path, required=True, help="Same artifact root used by upstream."
    )
    parser.add_argument("--output-record", type=Path, required=True)
    parser.add_argument("--fonts", type=Path, required=True)
    parser.add_argument("--font-base", type=Path, default=Path.cwd())
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--fixture",
        type=Path,
        help="Explicit local engineering fixture, not AI-generated.",
    )
    mode.add_argument(
        "--generator-factory",
        help="Trusted application module:function returning an SDK-backed generator.",
    )
    args = parser.parse_args()
    result = asyncio.run(execute(args))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()
