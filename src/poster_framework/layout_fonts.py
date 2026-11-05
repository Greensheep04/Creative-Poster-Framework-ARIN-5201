"""Font registry and text measurement for the layout planner.

Measurement and rendering must share one font file and one layout engine, so
this module owns the only place where a font file is resolved.  The renderer in
the visual producer can import the same registry instead of guessing a font by
family name.  Every measurement is real: Pillow renders the ink bounds, nothing
is estimated from a character count.
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence

from PIL import ImageFont

from .layout_errors import MissingFontError

DEFAULT_FAMILY_KEY = "noto_sans_sc"
FONT_WEIGHT_NAMES = {
    "thin": 100,
    "extralight": 200,
    "ultralight": 200,
    "light": 300,
    "regular": 400,
    "normal": 400,
    "book": 400,
    "medium": 500,
    "semibold": 600,
    "demibold": 600,
    "bold": 700,
    "extrabold": 800,
    "ultrabold": 800,
    "black": 900,
    "heavy": 900,
}

#: Codepoint that no ordinary text font defines; its rendered mask is the
#: FreeType ``.notdef`` glyph, which lets us detect missing characters.
_NOTDEF_PROBE = "\U0010FFFD"

_PLATFORM_CANDIDATES: dict[str, dict[int, tuple[tuple[str, int], ...]]] = {
    "noto_sans_sc": {
        400: (
            ("C:/Windows/Fonts/msyh.ttc", 0),
            ("C:/Windows/Fonts/msyhl.ttc", 0),
            ("C:/Windows/Fonts/simhei.ttf", 0),
            ("C:/Windows/Fonts/simsun.ttc", 0),
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
            ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 0),
            ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
            ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
            ("/System/Library/Fonts/PingFang.ttc", 0),
            ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
        ),
        700: (
            ("C:/Windows/Fonts/msyhbd.ttc", 0),
            ("C:/Windows/Fonts/simhei.ttf", 0),
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
            ("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc", 0),
            ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
            ("/System/Library/Fonts/PingFang.ttc", 0),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
        ),
    },
    "latin_sans": {
        400: (
            ("C:/Windows/Fonts/arial.ttf", 0),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
            ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
        ),
        700: (
            ("C:/Windows/Fonts/arialbd.ttf", 0),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
            ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
        ),
    },
}

_CLOSE_PUNCTUATION = "、。，．；：？！）〕］｝〉》」』】’”%‰·…"
_OPEN_PUNCTUATION = "（〔［｛〈《「『【‘“"
_LATIN_BREAK_AFTER = "-–—/:"


@dataclass(frozen=True)
class FontFace:
    family_key: str
    weight: int
    path: Path
    index: int = 0
    source: str = "platform"

    def as_json(self) -> dict[str, object]:
        return {
            "family_key": self.family_key,
            "weight": self.weight,
            "file": self.path.name,
            "path": str(self.path),
            "index": self.index,
            "sha256": _file_sha256(str(self.path)),
            "source": self.source,
        }


@lru_cache(maxsize=128)
def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=256)
def _load_font(path: str, index: int, size_px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size_px, index=index)


class FontRegistry:
    """Resolved font faces for this environment."""

    def __init__(
        self,
        faces: Mapping[tuple[str, int], FontFace],
        *,
        searched: Sequence[str] = (),
    ) -> None:
        self._faces = dict(faces)
        self._searched = tuple(searched)

    @classmethod
    def from_environment(
        cls,
        *,
        config_path: str | Path | None = None,
        font_dir: str | Path | None = None,
    ) -> FontRegistry:
        faces: dict[tuple[str, int], FontFace] = {}
        searched: list[str] = []
        configured = config_path or os.environ.get("POSTER_FONT_REGISTRY")
        if configured:
            searched.append(str(configured))
            faces.update(_faces_from_config(Path(configured)))
        directory = font_dir or os.environ.get("POSTER_FONT_DIR")
        if directory:
            searched.append(str(directory))
            faces.update(_faces_from_directory(Path(directory)))
        for family_key, per_weight in _PLATFORM_CANDIDATES.items():
            for weight, candidates in per_weight.items():
                if (family_key, weight) in faces:
                    continue
                for raw_path, index in candidates:
                    searched.append(raw_path)
                    path = Path(raw_path)
                    if path.is_file():
                        faces[(family_key, weight)] = FontFace(
                            family_key=family_key,
                            weight=weight,
                            path=path,
                            index=index,
                            source="platform",
                        )
                        break
        return cls(faces, searched=searched)

    @property
    def searched(self) -> tuple[str, ...]:
        return self._searched

    def faces(self, family_key: str | None = None) -> tuple[FontFace, ...]:
        return tuple(
            face
            for (key, _weight), face in sorted(self._faces.items())
            if family_key is None or key == family_key
        )

    def families(self) -> tuple[str, ...]:
        return tuple(sorted({family for family, _weight in self._faces}))

    def weights(self, family_key: str) -> tuple[int, ...]:
        return tuple(
            sorted(weight for family, weight in self._faces if family == family_key)
        )

    def resolve(
        self,
        family_key: str,
        weight: int,
        *,
        allow_nearest: bool = False,
    ) -> FontFace:
        if isinstance(weight, bool) or not isinstance(weight, int):
            raise TypeError("font weight must be an integer")
        face = self._faces.get((family_key, weight))
        if face is not None:
            return face
        available = self.weights(family_key)
        if allow_nearest and available:
            nearest = min(available, key=lambda item: (abs(item - weight), item))
            return replace(self._faces[(family_key, nearest)])
        if not available:
            raise MissingFontError(
                f"font family {family_key!r} is not installed; "
                f"available families: {', '.join(self.families()) or 'none'}"
            )
        raise MissingFontError(
            f"font family {family_key!r} has no weight {weight}; "
            f"available weights: {', '.join(str(item) for item in available)}"
        )

    def load(self, family_key: str, weight: int, size_px: int) -> ImageFont.FreeTypeFont:
        face = self.resolve(family_key, weight)
        return _load_font(str(face.path), face.index, size_px)

    def missing_glyphs(self, face: FontFace, text: str) -> tuple[str, ...]:
        """Characters rendered as ``.notdef`` by this font file.

        This is a deterministic rendering comparison, not an OS-level coverage
        database: a character is reported missing only when its mask is
        byte-identical to the mask of U+10FFFD, a codepoint no text font maps.
        """

        font = _load_font(str(face.path), face.index, 48)
        reference = _mask_signature(font, _NOTDEF_PROBE)
        missing: list[str] = []
        seen: set[str] = set()
        for char in text:
            if char in seen or char.isspace():
                continue
            seen.add(char)
            if _mask_signature(font, char) == reference:
                missing.append(char)
        return tuple(missing)

    def describe(self) -> dict[str, object]:
        families: dict[str, object] = {}
        for family_key in self.families():
            families[family_key] = [face.as_json() for face in self.faces(family_key)]
        return {
            "families": families,
            "searched": list(self._searched),
            "coverage_probe": _NOTDEF_PROBE,
        }


@lru_cache(maxsize=4)
def default_font_registry() -> FontRegistry:
    return FontRegistry.from_environment()


def default_family_key() -> str:
    """Family key the planner requests; override with ``POSTER_FONT_FAMILY``."""

    configured = os.environ.get("POSTER_FONT_FAMILY")
    return configured.strip() if configured and configured.strip() else DEFAULT_FAMILY_KEY


def _faces_from_config(path: Path) -> dict[tuple[str, int], FontFace]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MissingFontError(f"cannot read font registry config {path}: {error}") from error
    if not isinstance(payload, dict):
        raise MissingFontError("font registry config must be a JSON object")
    raw_families = payload.get("families", payload)
    if not isinstance(raw_families, dict):
        raise MissingFontError("font registry config requires a families object")
    faces: dict[tuple[str, int], FontFace] = {}
    for family_key, raw_weights in raw_families.items():
        if not isinstance(raw_weights, dict):
            raise MissingFontError(f"font family {family_key!r} must map weights to faces")
        for raw_weight, raw_face in raw_weights.items():
            try:
                weight = int(raw_weight)
            except (TypeError, ValueError) as error:
                raise MissingFontError(
                    f"font family {family_key!r} has a non-numeric weight"
                ) from error
            if isinstance(raw_face, str):
                raw_path, index = raw_face, 0
            elif isinstance(raw_face, dict):
                raw_path = raw_face.get("path")
                index = raw_face.get("index", 0)
            else:
                raise MissingFontError(f"font family {family_key!r} has an invalid entry")
            if not isinstance(raw_path, str) or not isinstance(index, int):
                raise MissingFontError(f"font family {family_key!r} requires path and index")
            candidate = Path(raw_path)
            if not candidate.is_file():
                raise MissingFontError(f"font file does not exist: {raw_path}")
            faces[(str(family_key), weight)] = FontFace(
                family_key=str(family_key),
                weight=weight,
                path=candidate,
                index=index,
                source="config",
            )
    return faces


def _faces_from_directory(directory: Path) -> dict[tuple[str, int], FontFace]:
    if not directory.is_dir():
        raise MissingFontError(f"font directory does not exist: {directory}")
    faces: dict[tuple[str, int], FontFace] = {}
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
            continue
        family_key, weight = _split_font_filename(path.stem)
        faces[(family_key, weight)] = FontFace(
            family_key=family_key,
            weight=weight,
            path=path,
            index=0,
            source="env_dir",
        )
    return faces


def _split_font_filename(stem: str) -> tuple[str, int]:
    tokens = [token for token in stem.lower().replace("_", "-").split("-") if token]
    weight = 400
    if tokens:
        last = tokens[-1]
        if last in FONT_WEIGHT_NAMES:
            weight = FONT_WEIGHT_NAMES[last]
            tokens = tokens[:-1]
    family = "_".join(tokens) if tokens else "custom"
    return family, weight


def _mask_signature(font: ImageFont.FreeTypeFont, text: str) -> tuple[tuple[int, int], bytes]:
    mask = font.getmask(text)
    return (mask.size, bytes(mask))


def is_cjk(char: str) -> bool:
    return bool(char) and unicodedata.east_asian_width(char) in {"W", "F"}


def can_break_between(previous: str, following: str) -> bool:
    """Whether a line break may sit between two characters."""

    if previous.isspace() or following.isspace():
        return True
    if following in _CLOSE_PUNCTUATION:
        return False
    if previous in _OPEN_PUNCTUATION:
        return False
    if previous in _LATIN_BREAK_AFTER:
        return True
    return is_cjk(previous) or is_cjk(following)


@dataclass(frozen=True)
class TextMeasurement:
    text: str
    font_family_key: str
    font_weight: int
    font_size_px: int
    line_height_px: int
    max_width_px: int
    max_lines: int
    box_height_px: int | None
    lines: tuple[str, ...]
    line_widths_px: tuple[float, ...]
    line_ink_boxes: tuple[tuple[float, float, float, float], ...]
    widest_line_px: float
    required_height_px: int
    fits_width: bool
    fits_height: bool
    fits_lines: bool
    missing_glyphs: tuple[str, ...]

    @property
    def fits(self) -> bool:
        return self.fits_width and self.fits_height and self.fits_lines

    def as_json(self) -> dict[str, object]:
        return {
            "text": self.text,
            "font_family_key": self.font_family_key,
            "font_weight": self.font_weight,
            "font_size_px": self.font_size_px,
            "line_height_px": self.line_height_px,
            "max_width_px": self.max_width_px,
            "max_lines": self.max_lines,
            "box_height_px": self.box_height_px,
            "lines": list(self.lines),
            "line_widths_px": [round(value, 2) for value in self.line_widths_px],
            "line_ink_boxes": [[round(value, 2) for value in box] for box in self.line_ink_boxes],
            "widest_line_px": round(self.widest_line_px, 2),
            "required_height_px": self.required_height_px,
            "fits_width": self.fits_width,
            "fits_height": self.fits_height,
            "fits_lines": self.fits_lines,
            "fits": self.fits,
            "missing_glyphs": list(self.missing_glyphs),
        }


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width_px: float) -> tuple[str, ...]:
    """Greedy wrapper that respects CJK rules and never splits a Latin word
    unless the word alone is wider than the block."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("text to measure must be a non-empty string")
    if max_width_px <= 0:
        raise ValueError("max_width_px must be positive")
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        start = 0
        length = len(paragraph)
        while start < length:
            if font.getlength(paragraph[start:]) <= max_width_px:
                lines.append(paragraph[start:].rstrip())
                break
            last_fit = start
            last_break = -1
            cursor = start + 1
            while cursor <= length:
                if font.getlength(paragraph[start:cursor]) > max_width_px:
                    break
                last_fit = cursor
                if cursor < length and can_break_between(
                    paragraph[cursor - 1], paragraph[cursor]
                ):
                    last_break = cursor
                cursor += 1
            end = last_break if last_break > start else last_fit
            if end <= start:
                end = start + 1
            segment = paragraph[start:end].rstrip()
            if segment:
                lines.append(segment)
            start = end
            while start < length and paragraph[start] == " ":
                start += 1
    return tuple(lines)


def measure_text_block(
    text: str,
    *,
    font_family_key: str = DEFAULT_FAMILY_KEY,
    font_weight: int = 400,
    font_size_px: int,
    line_height_px: int,
    max_width_px: int,
    max_lines: int,
    box_height_px: int | None = None,
    registry: FontRegistry | None = None,
    check_glyphs: bool = True,
) -> TextMeasurement:
    for name, value in (
        ("font_size_px", font_size_px),
        ("line_height_px", line_height_px),
        ("max_width_px", max_width_px),
        ("max_lines", max_lines),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    active_registry = registry or default_font_registry()
    face = active_registry.resolve(font_family_key, font_weight)
    font = _load_font(str(face.path), face.index, font_size_px)
    lines = wrap_text(text, font, max_width_px)
    widths = tuple(float(font.getlength(line)) for line in lines)
    ink_boxes = tuple(tuple(float(value) for value in font.getbbox(line)) for line in lines)
    tops = [box[1] for box in ink_boxes]
    bottoms = [box[3] for box in ink_boxes]
    span = max(bottoms) - min(tops) if lines and any(bottoms) else 0.0
    required_height = int(round((len(lines) - 1) * line_height_px + span))
    widest = max(widths, default=0.0)
    fits_width = widest <= max_width_px + 0.5
    fits_lines = len(lines) <= max_lines
    fits_height = box_height_px is None or required_height <= box_height_px
    missing = active_registry.missing_glyphs(face, text) if check_glyphs else ()
    return TextMeasurement(
        text=text,
        font_family_key=font_family_key,
        font_weight=font_weight,
        font_size_px=font_size_px,
        line_height_px=line_height_px,
        max_width_px=max_width_px,
        max_lines=max_lines,
        box_height_px=box_height_px,
        lines=lines,
        line_widths_px=widths,
        line_ink_boxes=ink_boxes,
        widest_line_px=widest,
        required_height_px=required_height,
        fits_width=fits_width,
        fits_height=fits_height,
        fits_lines=fits_lines,
        missing_glyphs=missing,
    )


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    if not isinstance(color, str) or len(color) != 7 or color[0] != "#":
        raise ValueError(f"color must use #RRGGBB: {color!r}")
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def relative_luminance(color: str) -> float:
    def channel(value: int) -> float:
        scaled = value / 255
        return scaled / 12.92 if scaled <= 0.04045 else ((scaled + 0.055) / 1.055) ** 2.4

    red, green, blue = hex_to_rgb(color)
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast_ratio(first: str, second: str) -> float:
    lighter, darker = sorted(
        (relative_luminance(first), relative_luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def pick_text_color(
    background: str,
    candidates: Sequence[str] = (),
    *,
    minimum_ratio: float = 4.5,
) -> str:
    """Prefer a readable palette color; fall back to pure black or white.

    The palette direction from the creative brief must survive layout: a pure
    black text color is only used when no palette color reaches the contrast
    floor.
    """

    palette: list[str] = []
    for color in candidates:
        normalized = color.upper()
        if normalized not in palette:
            palette.append(normalized)
    scored = [(contrast_ratio(background, color), color) for color in palette]
    passing = [item for item in scored if item[0] >= minimum_ratio]
    if passing:
        return max(passing, key=lambda item: item[0])[1]
    black = contrast_ratio(background, "#000000")
    white = contrast_ratio(background, "#FFFFFF")
    return "#000000" if black >= white else "#FFFFFF"


