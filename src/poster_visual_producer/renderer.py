from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import PIL
from PIL import Image, ImageDraw
from .contracts import PosterSpec, ImageElement, TextElement, StageError
from .fonts import FontRegistry
from .imaging import prepare_asset, encode_png

RENDERER_VERSION = "0.1.0"


@dataclass
class TextLayout:
    element: TextElement
    font: object
    font_sha256: str
    lines: list[str]
    placements: list[dict]
    ink_bounds: list[int]


def measure_text_block(element: TextElement, fonts: FontRegistry) -> TextLayout:
    font, digest = fonts.resolve(element)
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    def bounds(text):
        return draw.textbbox((0, 0), text, font=font, anchor="lt")

    def width(text):
        box = bounds(text)
        return max(box[2] - box[0], draw.textlength(text, font=font))

    lines = []
    # CRLF and CR are line separators, never drawable glyphs. Preserve the
    # original official string in the manifest while normalizing layout only.
    layout_text = element.text.replace("\r\n", "\n").replace("\r", "\n")
    for paragraph in layout_text.split("\n"):
        current = ""
        for char in paragraph:
            if width(current + char) > element.box.width:
                if not current:
                    raise StageError(
                        "TEXT_OVERFLOW",
                        "A glyph does not fit the specified box",
                        affected_ids=[element.id],
                    )
                lines.append(current)
                current = char
                if width(current) > element.box.width:
                    raise StageError(
                        "TEXT_OVERFLOW",
                        "A glyph does not fit",
                        affected_ids=[element.id],
                    )
            else:
                current += char
        lines.append(current)
    if len(lines) > element.max_lines:
        raise StageError(
            "TEXT_OVERFLOW", "Text exceeds max_lines", affected_ids=[element.id]
        )
    placements = []
    real = []
    for i, line in enumerate(lines):
        l, t, r, b = bounds(line)
        ink_w, ink_h = r - l, b - t
        if len(lines) > 1 and ink_h > element.line_height_px:
            raise StageError(
                "TEXT_OVERFLOW",
                "Line height would overlap glyphs",
                affected_ids=[element.id],
            )
        offset = {
            "left": 0,
            "center": (element.box.width - ink_w) // 2,
            "right": element.box.width - ink_w,
        }[element.align]
        x, y = element.box.x + offset, element.box.y + i * element.line_height_px
        if y + ink_h > element.box.y + element.box.height:
            raise StageError(
                "TEXT_OVERFLOW", "Text ink exceeds height", affected_ids=[element.id]
            )
        placements.append(
            {
                "text": line,
                "draw_xy": [x - l, y - t],
                "ink_bounds": [x, y, x + ink_w, y + ink_h],
            }
        )
        if line:
            real.append([x, y, x + ink_w, y + ink_h])
    ink = (
        [
            min(p[0] for p in real),
            min(p[1] for p in real),
            max(p[2] for p in real),
            max(p[3] for p in real),
        ]
        if real
        else [element.box.x, element.box.y, element.box.x, element.box.y]
    )
    return TextLayout(element, font, digest, lines, placements, ink)


def preflight(spec: PosterSpec, fonts: FontRegistry) -> dict[str, TextLayout]:
    return {
        e.id: measure_text_block(e, fonts)
        for e in spec.elements
        if isinstance(e, TextElement)
    }


@dataclass
class RenderResult:
    png: bytes
    manifest: dict
    processed: dict[str, bytes]


def render_poster(
    spec: PosterSpec, assets: dict[str, bytes], fonts: FontRegistry
) -> RenderResult:
    layouts = preflight(spec, fonts)
    canvas = Image.new("RGB", (720, 1280), spec.background.color)
    draw = ImageDraw.Draw(canvas)
    rows = []
    processed = {}
    for e in sorted(spec.elements, key=lambda item: item.z):
        row = {
            "element_id": e.id,
            "type": e.type,
            "z": e.z,
            "spec_box": e.box.model_dump(),
        }
        if isinstance(e, ImageElement):
            if e.asset_id not in assets:
                raise StageError(
                    "MISSING_ASSET",
                    "Required source asset missing",
                    affected_ids=[e.asset_id],
                )
            prepared = prepare_asset(assets[e.asset_id], e)
            canvas.paste(
                prepared.image, (e.box.x, e.box.y), prepared.image.getchannel("A")
            )
            processed[e.id] = encode_png(prepared.image)
            row.update(
                asset_id=e.asset_id,
                actual_bounds=[
                    e.box.x,
                    e.box.y,
                    e.box.x + e.box.width,
                    e.box.y + e.box.height,
                ],
                asset_to_canvas=[1, 0, 0, 1, e.box.x, e.box.y],
                source_sha256=hashlib.sha256(assets[e.asset_id]).hexdigest(),
                processed_sha256=hashlib.sha256(processed[e.id]).hexdigest(),
                processing=prepared.record,
            )
        else:
            layout = layouts[e.id]
            for placement in layout.placements:
                draw.text(
                    tuple(placement["draw_xy"]),
                    placement["text"],
                    font=layout.font,
                    fill=e.color,
                    anchor="lt",
                )
            row.update(
                text=e.text,
                copy_ref=e.copy_ref,
                lines=layout.lines,
                placements=layout.placements,
                actual_bounds=layout.ink_bounds,
                font_family_key=e.font_family_key,
                font_weight=e.font_weight,
                font_size_px=e.font_size_px,
                line_height_px=e.line_height_px,
                font_sha256=layout.font_sha256,
            )
        rows.append(row)
    png = encode_png(canvas)
    manifest = {
        "renderer_version": RENDERER_VERSION,
        "pillow_version": PIL.__version__,
        "canvas": {"width": 720, "height": 1280, "color_mode": "RGB"},
        "elements": rows,
        "output_sha256": hashlib.sha256(png).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "warnings": [],
    }
    return RenderResult(png, manifest, processed)
