"""Typed PosterSpec contract shared with the visual producer.

The models mirror ``docs/schemas/PosterSpec.json`` on the visual-producer
branch, so an A2 output can be consumed by A3 without an adapter.  The JSON
document is the source of truth for the cross-role contract; this module only
re-expresses it as Pydantic models plus the cross-field checks that a pure
schema cannot state.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .layout_errors import LayoutContractError
from .types import CANVAS_HEIGHT, CANVAS_WIDTH

SCHEMA_VERSION = "0.1.0"
ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"
HEX_PATTERN = r"^#[0-9a-fA-F]{6}$"
FONT_WEIGHTS = (100, 200, 300, 400, 500, 600, 700, 800, 900)
TEMPLATE_IDS = ("framed_hero_v1", "full_bleed_v1", "split_stack_v1")

class SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BasedOn(SpecModel):
    brief_version: int = Field(gt=0)


class Canvas(SpecModel):
    width: Literal[CANVAS_WIDTH] = CANVAS_WIDTH
    height: Literal[CANVAS_HEIGHT] = CANVAS_HEIGHT
    color_mode: Literal["RGB"] = "RGB"


class Background(SpecModel):
    color: str = Field(pattern=HEX_PATTERN)


class Tokens(SpecModel):
    palette: list[Annotated[str, Field(pattern=HEX_PATTERN)]]
    font_family_key: str = Field(pattern=ID_PATTERN)
    safe_margin_px: int = Field(ge=0)


#: Name used by the layout templates when they talk about design tokens.
DesignTokens = Tokens


class Size(SpecModel):
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)


class Box(SpecModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)

    @model_validator(mode="after")
    def validate_canvas_bounds(self) -> Box:
        if self.x + self.width > CANVAS_WIDTH or self.y + self.height > CANVAS_HEIGHT:
            raise ValueError("element box must stay inside the 720x1280 canvas")
        return self


class AssetPlan(SpecModel):
    asset_id: str = Field(pattern=ID_PATTERN)
    kind: Literal["generated_image"]
    prompt: str = Field(min_length=1, max_length=12000)
    negative_prompt: str = ""
    target_size: Size
    subject_safe_region_norm: list[float] = Field(min_length=4, max_length=4)
    required_keyword_ids: list[str]

    @model_validator(mode="after")
    def validate_safe_region(self) -> AssetPlan:
        x, y, width, height = self.subject_safe_region_norm
        if any(not 0.0 <= value <= 1.0 for value in (x, y, width, height)):
            raise ValueError("subject_safe_region_norm values must be within 0..1")
        if x + width > 1.0 or y + height > 1.0:
            raise ValueError("subject_safe_region_norm must stay inside the frame")
        return self


class _ElementBase(SpecModel):
    id: str = Field(pattern=ID_PATTERN)
    z: int = Field(ge=0)
    box: Box


class ImageElement(_ElementBase):
    type: Literal["image"]
    asset_id: str = Field(pattern=ID_PATTERN)
    fit: Literal["cover", "contain"]
    focus_point_norm: list[float] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_focus_point(self) -> ImageElement:
        if any(not 0.0 <= value <= 1.0 for value in self.focus_point_norm):
            raise ValueError("focus_point_norm values must be within 0..1")
        return self


class TextElement(_ElementBase):
    type: Literal["text"]
    copy_ref: str
    text: str = Field(min_length=1, max_length=2000)
    font_family_key: str = Field(pattern=ID_PATTERN)
    font_weight: Literal[100, 200, 300, 400, 500, 600, 700, 800, 900]
    font_size_px: int = Field(ge=1, le=512)
    line_height_px: int = Field(gt=0, le=8192)
    max_lines: int = Field(gt=0, le=64)
    align: Literal["left", "center", "right"]
    color: str = Field(pattern=HEX_PATTERN)

    @model_validator(mode="after")
    def validate_text_metrics(self) -> TextElement:
        if not self.text.strip():
            raise ValueError("text element copy must be non-empty")
        if self.line_height_px < self.font_size_px:
            raise ValueError("line_height_px must be at least font_size_px")
        return self


PosterElement = Annotated[
    Union[ImageElement, TextElement],
    Field(discriminator="type"),
]


class KeywordElements(SpecModel):
    keyword_id: str
    element_ids: list[Annotated[str, Field(pattern=ID_PATTERN)]]
    layout_evidence: str | None = None


class RenderPolicy(SpecModel):
    text_overflow: Literal["error"]
    missing_font: Literal["error"]
    output_format: Literal["PNG"]


class PosterSpec(SpecModel):
    schema_version: Literal["0.1.0"]
    run_id: str
    spec_version: int = Field(gt=0)
    based_on: BasedOn
    canvas: Canvas
    template_id: Literal["framed_hero_v1", "full_bleed_v1", "split_stack_v1"]
    background: Background
    design_tokens: DesignTokens
    asset_plan: list[AssetPlan] = Field(min_length=1, max_length=8)
    elements: list[PosterElement] = Field(min_length=1, max_length=32)
    keyword_element_bindings: list[KeywordElements]
    allowed_overlaps: list[Annotated[list[str], Field(min_length=2, max_length=2)]]
    render_policy: RenderPolicy

    @model_validator(mode="after")
    def validate_references(self) -> PosterSpec:
        asset_ids = [asset.asset_id for asset in self.asset_plan]
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError("asset_plan entries require unique asset_id values")
        element_ids = [element.id for element in self.elements]
        if len(set(element_ids)) != len(element_ids):
            raise ValueError("element ids must be unique")
        known = set(element_ids)
        for element in self.elements:
            if isinstance(element, ImageElement) and element.asset_id not in asset_ids:
                raise ValueError(
                    f"image element {element.id!r} references an unknown asset"
                )
        binding_ids: list[str] = []
        for binding in self.keyword_element_bindings:
            if not binding.element_ids:
                raise ValueError("each keyword requires at least one element binding")
            missing = [item for item in binding.element_ids if item not in known]
            if missing:
                raise ValueError(
                    f"keyword binding {binding.keyword_id!r} references an unknown element"
                )
            binding_ids.append(binding.keyword_id)
        if len(set(binding_ids)) != len(binding_ids):
            raise ValueError("keyword bindings must have unique keyword_id values")
        for pair in self.allowed_overlaps:
            if len(pair) != 2 or any(item not in known for item in pair):
                raise ValueError("allowed_overlaps must reference two known element ids")
            if pair[0] == pair[1]:
                raise ValueError("allowed_overlaps cannot pair an element with itself")
        return self

    def to_json(self) -> dict[str, object]:
        return self.model_dump(mode="json")


def parse_poster_spec(value: object) -> PosterSpec:
    if not isinstance(value, dict):
        raise LayoutContractError("PosterSpec must be a JSON object")
    try:
        return PosterSpec.model_validate(value)
    except Exception as error:  # pydantic ValidationError carries the field path
        raise LayoutContractError(f"invalid PosterSpec: {error}") from error


def validate_poster_spec(value: object) -> None:
    """Business validator used by ``AgentRegistration(output_validator=...)``."""

    parse_poster_spec(value)


def poster_spec_json_schema() -> dict[str, object]:
    return PosterSpec.model_json_schema()


def element_box(element: PosterElement) -> tuple[int, int, int, int]:
    return element.box.x, element.box.y, element.box.width, element.box.height


def rect_intersection(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> int:
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    width = min(ax + aw, bx + bw) - max(ax, bx)
    height = min(ay + ah, by + bh) - max(ay, by)
    if width <= 0 or height <= 0:
        return 0
    return width * height




