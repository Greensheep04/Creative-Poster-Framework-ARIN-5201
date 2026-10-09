from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Protocol, Callable, Awaitable
import inspect, math
from .contracts import AssetPlan, StageError
from .imaging import decode_image
from .metadata import safe_metadata


@dataclass(frozen=True)
class GeneratorCapabilities:
    provider: str
    model: str
    sizes: tuple[tuple[int, int], ...]
    supports_seed: bool = False
    supports_negative_prompt: bool = False
    supports_edit: bool = False
    offline_fixture: bool = False
    weights_version: str | None = None
    generation_parameters: tuple[tuple[str, str | int | float | bool], ...] = ()

    def __post_init__(self):
        if (
            not self.provider.strip()
            or not self.model.strip()
            or not self.sizes
            or any(type(v) != int or v <= 0 for size in self.sizes for v in size)
            or any(len(size) != 2 for size in self.sizes)
        ):
            raise StageError(
                "UNSUPPORTED_CAPABILITY", "Invalid generator capability configuration"
            )


@dataclass(frozen=True)
class GenerationRequest:
    asset_id: str
    prompt: str
    negative_prompt: str | None
    size: tuple[int, int]
    seed: int | None
    seed_reason: str
    adaptations: tuple[str, ...]
    repair_constraints: tuple[str, ...]

    def as_json(self):
        return {
            "asset_id": self.asset_id,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "size": list(self.size),
            "seed": self.seed,
            "seed_reason": self.seed_reason,
            "adaptations": list(self.adaptations),
            "repair_constraints": list(self.repair_constraints),
        }


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    request_id: str | None = None
    usage: dict | None = None
    provenance: dict = field(default_factory=dict)


class ProviderError(StageError):
    """The adapter must explicitly certify that a resubmission is safe."""

    def __init__(
        self,
        message: str,
        *,
        retry_safe: bool = False,
        submission_status="unknown",
        code="PROVIDER_UNAVAILABLE",
    ):
        super().__init__(code, message)
        self.retry_safe = retry_safe
        self.submission_status = submission_status


class ImageGenerator(Protocol):
    capabilities: GeneratorCapabilities

    async def generate(self, request: GenerationRequest) -> GeneratedImage: ...


def compile_request(
    plan: AssetPlan | dict,
    capabilities: GeneratorCapabilities,
    *,
    seed=None,
    negative_policy="append",
    repair_constraints=(),
) -> GenerationRequest:
    if isinstance(plan, dict):
        plan = AssetPlan.model_validate(plan)
    if seed is not None and (type(seed) != int or not capabilities.supports_seed):
        raise StageError("UNSUPPORTED_CAPABILITY", "Explicit seed is not supported")
    if negative_policy not in {"append", "error"}:
        raise ValueError("Unknown negative prompt policy")
    target = plan.target_size

    def distance(size):
        w, h = size
        return abs(math.log((w / h) / (target.width / target.height))) + (
            10 if w < target.width or h < target.height else 0
        ), w * h

    size = min(capabilities.sizes, key=distance)
    prompt = plan.prompt
    negative = plan.negative_prompt or None
    adaptations = []
    if negative and not capabilities.supports_negative_prompt:
        if negative_policy == "error":
            raise StageError(
                "UNSUPPORTED_CAPABILITY", "Backend has no independent negative prompt"
            )
        prompt += "\nAvoid the following content: " + negative
        negative = None
        adaptations.append("negative_prompt_appended_to_prompt")
    constraints = tuple(repair_constraints)
    if constraints:
        prompt += "\nApproved asset repair constraints:\n" + "\n".join(constraints)
    reason = (
        "specified"
        if seed is not None
        else ("not_specified" if capabilities.supports_seed else "unsupported")
    )
    return GenerationRequest(
        plan.asset_id,
        prompt,
        negative,
        size,
        seed,
        reason,
        tuple(adaptations),
        constraints,
    )


class CallableImageGenerator:
    """Wrap a trusted provider SDK callback; credentials stay inside the callback."""

    def __init__(
        self,
        capabilities: GeneratorCapabilities,
        callback: Callable[
            [GenerationRequest], GeneratedImage | Awaitable[GeneratedImage]
        ],
    ):
        self.capabilities, self.callback = capabilities, callback

    async def generate(self, request: GenerationRequest) -> GeneratedImage:
        result = self.callback(request)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, GeneratedImage):
            raise StageError("INVALID_ASSET", "SDK adapter must return GeneratedImage")
        decode_image(result.data)
        return GeneratedImage(
            result.data,
            result.request_id,
            safe_metadata(result.usage),
            safe_metadata(result.provenance),
        )


class FixtureImageGenerator(CallableImageGenerator):
    """Explicit trusted offline fixture for engineering tests, never fallback for a failed SDK."""

    def __init__(
        self,
        source: Callable[[GenerationRequest], bytes],
        *,
        sizes=((1024, 1536),),
        model="procedural-fixture-v1",
    ):
        caps = GeneratorCapabilities(
            "offline_fixture",
            model,
            tuple(sizes),
            offline_fixture=True,
            weights_version="fixture-v1",
        )

        def callback(request):
            return GeneratedImage(
                source(request),
                usage=None,
                provenance={"source_mode": "offline_fixture", "not_ai_generated": True},
            )

        super().__init__(caps, callback)
