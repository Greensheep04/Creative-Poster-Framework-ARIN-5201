"""Visual production extension for the Creative Poster Framework."""

from .agent import VisualProducerAgent
from .contracts import CreativeBrief, PosterSpec, StageError, validate_pair
from .fonts import FontFace, FontRegistry
from .generators import (
    CallableImageGenerator,
    FixtureImageGenerator,
    GeneratedImage,
    GenerationRequest,
    GeneratorCapabilities,
    ImageGenerator,
    ProviderError,
)
from .registration import (
    BRIEF_FORMAT,
    SPEC_FORMAT,
    CANDIDATE_FORMAT,
    project_budget,
    registration,
)
from .renderer import measure_text_block, render_poster
from .tools import build_tool_registry

__all__ = [
    "VisualProducerAgent",
    "CreativeBrief",
    "PosterSpec",
    "StageError",
    "validate_pair",
    "FontFace",
    "FontRegistry",
    "CallableImageGenerator",
    "FixtureImageGenerator",
    "GeneratedImage",
    "GenerationRequest",
    "GeneratorCapabilities",
    "ImageGenerator",
    "ProviderError",
    "BRIEF_FORMAT",
    "SPEC_FORMAT",
    "CANDIDATE_FORMAT",
    "project_budget",
    "registration",
    "measure_text_block",
    "render_poster",
    "build_tool_registry",
]
