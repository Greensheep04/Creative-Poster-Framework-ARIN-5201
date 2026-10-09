from __future__ import annotations
import re
from typing import Annotated, Literal
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
    field_validator,
)

Id = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
Positive = Annotated[int, Field(strict=True, gt=0, le=8192)]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]


class StageError(ValueError):
    def __init__(self, code: str, message: str, *, affected_ids=()):
        self.code, self.affected_ids = code, tuple(affected_ids)
        super().__init__(message)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Size(Model):
    width: Positive
    height: Positive


class Canvas(Model):
    width: Literal[720] = 720
    height: Literal[1280] = 1280
    color_mode: Literal["RGB"] = "RGB"


class Box(Size):
    x: Annotated[int, Field(strict=True, ge=0)]
    y: Annotated[int, Field(strict=True, ge=0)]


class Copy(Model):
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    origin: str
    must_preserve: bool


class KeywordBinding(Model):
    keyword_id: str
    interpretation: str
    visual_evidence: list[str]
    text_alone_sufficient: bool


class CreativeBrief(Model):
    schema_version: Literal["0.1.0"]
    run_id: str
    brief_version: Annotated[int, Field(strict=True, gt=0)]
    request_ref: str
    intent: dict[str, JsonValue]
    keyword_bindings: list[KeywordBinding]
    copy_blocks: dict[str, Copy] = Field(alias="copy")
    visual_requirements: dict[str, JsonValue]
    layout_intent: dict[str, JsonValue]
    assumptions: list[dict[str, JsonValue]]
    reference_notes: list[JsonValue]
    open_issues: list[JsonValue]


class BasedOn(Model):
    brief_version: Annotated[int, Field(strict=True, gt=0)]


class AssetPlan(Model):
    asset_id: Id
    kind: Literal["generated_image"]
    prompt: Annotated[str, Field(min_length=1, max_length=12000)]
    negative_prompt: str = ""
    target_size: Size
    subject_safe_region_norm: tuple[float, float, float, float]
    required_keyword_ids: list[str]

    @field_validator("subject_safe_region_norm")
    @classmethod
    def region(cls, v):
        x, y, w, h = v
        if not (
            0 <= x <= 1
            and 0 <= y <= 1
            and 0 < w <= 1
            and 0 < h <= 1
            and x + w <= 1.000001
            and y + h <= 1.000001
        ):
            raise ValueError("invalid normalized subject region")
        return v


class ImageElement(Model):
    id: Id
    type: Literal["image"]
    z: Annotated[int, Field(strict=True, ge=0)]
    box: Box
    asset_id: Id
    fit: Literal["cover", "contain"]
    focus_point_norm: tuple[float, float]

    @field_validator("focus_point_norm")
    @classmethod
    def focus(cls, v):
        if not all(0 <= item <= 1 for item in v):
            raise ValueError("invalid focus point")
        return v


class TextElement(Model):
    id: Id
    type: Literal["text"]
    z: Annotated[int, Field(strict=True, ge=0)]
    box: Box
    copy_ref: str
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    font_family_key: Id
    font_weight: Literal[100, 200, 300, 400, 500, 600, 700, 800, 900]
    font_size_px: Annotated[int, Field(strict=True, ge=1, le=512)]
    line_height_px: Positive
    max_lines: Annotated[int, Field(strict=True, gt=0, le=64)]
    align: Literal["left", "center", "right"]
    color: Color


Element = Annotated[ImageElement | TextElement, Field(discriminator="type")]


class Tokens(Model):
    palette: list[Color]
    font_family_key: Id
    safe_margin_px: Annotated[int, Field(strict=True, ge=0)]


class Background(Model):
    color: Color


class KeywordElements(Model):
    keyword_id: str
    element_ids: list[Id]
    layout_evidence: str | None = None


class RenderPolicy(Model):
    text_overflow: Literal["error"]
    missing_font: Literal["error"]
    output_format: Literal["PNG"]


class PosterSpec(Model):
    schema_version: Literal["0.1.0"]
    run_id: str
    spec_version: Annotated[int, Field(strict=True, gt=0)]
    based_on: BasedOn
    canvas: Canvas
    template_id: Literal["framed_hero_v1", "full_bleed_v1", "split_stack_v1"]
    background: Background
    design_tokens: Tokens
    asset_plan: Annotated[list[AssetPlan], Field(min_length=1, max_length=8)]
    elements: Annotated[list[Element], Field(min_length=1, max_length=32)]
    keyword_element_bindings: list[KeywordElements]
    allowed_overlaps: list[tuple[Id, Id]]
    render_policy: RenderPolicy

    @model_validator(mode="after")
    def geometry(self):
        ids = [e.id for e in self.elements]
        assets = [a.asset_id for a in self.asset_plan]
        if len(set(ids)) != len(ids) or len(set(assets)) != len(assets):
            raise ValueError("duplicate element/asset ID")
        used = set()
        for e in self.elements:
            if e.box.x + e.box.width > 720 or e.box.y + e.box.height > 1280:
                raise ValueError("element out of canvas")
            if isinstance(e, ImageElement):
                if e.asset_id not in assets:
                    raise ValueError("dangling asset reference")
                used.add(e.asset_id)
                plan = next(a for a in self.asset_plan if a.asset_id == e.asset_id)
                if (plan.target_size.width, plan.target_size.height) != (
                    e.box.width,
                    e.box.height,
                ):
                    raise ValueError("asset target must match image slot in MVP")
        if used != set(assets):
            raise ValueError("unused asset plan")
        for binding in self.keyword_element_bindings:
            if not binding.element_ids or not set(binding.element_ids) <= set(ids):
                raise ValueError("dangling keyword element reference")
        for pair in self.allowed_overlaps:
            if pair[0] == pair[1] or not set(pair) <= set(ids):
                raise ValueError("invalid overlap permission")
        allowed = {frozenset(pair) for pair in self.allowed_overlaps}
        for i, a in enumerate(self.elements):
            for b in self.elements[i + 1 :]:
                if max(a.box.x, b.box.x) < min(
                    a.box.x + a.box.width, b.box.x + b.box.width
                ) and max(a.box.y, b.box.y) < min(
                    a.box.y + a.box.height, b.box.y + b.box.height
                ):
                    if frozenset((a.id, b.id)) not in allowed:
                        raise ValueError("undeclared element overlap")
        return self

    def model_dump(self, **kwargs):
        # Optional layout evidence is absent in the v0.1 document, preserve that representation.
        value = super().model_dump(**kwargs)
        for row in value["keyword_element_bindings"]:
            if row.get("layout_evidence") is None:
                row.pop("layout_evidence", None)
        return value


def validate_pair(
    spec: PosterSpec, brief: CreativeBrief, keyword_ids: list[str]
) -> None:
    if (
        spec.run_id != brief.run_id
        or spec.based_on.brief_version != brief.brief_version
    ):
        raise StageError("STALE_VERSION", "Spec and Brief versions/run do not match")
    expected = set(keyword_ids)
    spec_ids = [x.keyword_id for x in spec.keyword_element_bindings]
    brief_ids = [x.keyword_id for x in brief.keyword_bindings]
    if (
        set(spec_ids) != expected
        or set(brief_ids) != expected
        or len(spec_ids) != len(expected)
        or len(brief_ids) != len(expected)
    ):
        raise StageError(
            "SCHEMA_INVALID", "Original keyword IDs must appear exactly once"
        )
    if any(not set(a.required_keyword_ids) <= expected for a in spec.asset_plan):
        raise StageError("SCHEMA_INVALID", "Unknown asset keyword")
    if brief.open_issues:
        raise StageError("CONSTRAINT_CONFLICT", "Brief has unresolved issues")
    represented_copy = {e.copy_ref for e in spec.elements if isinstance(e, TextElement)}
    required_copy = {
        key for key, block in brief.copy_blocks.items() if block.must_preserve
    }
    if not required_copy <= represented_copy:
        raise StageError(
            "CONSTRAINT_CONFLICT", "Mandatory official copy is missing from the layout"
        )
    for e in spec.elements:
        if isinstance(e, TextElement):
            if (
                e.copy_ref not in brief.copy_blocks
                or e.text != brief.copy_blocks[e.copy_ref].text
            ):
                raise StageError(
                    "CONSTRAINT_CONFLICT", "Official copy changed", affected_ids=[e.id]
                )
