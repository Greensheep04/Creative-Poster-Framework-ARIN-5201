from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import hashlib
from fontTools.ttLib import TTFont
from PIL import ImageFont
from .contracts import StageError, TextElement


@dataclass(frozen=True)
class FontFace:
    path: Path
    index: int = 0


class FontRegistry:
    """Trusted application configuration, never model-provided font paths."""

    def __init__(self, families: dict[str, dict[int, FontFace]]):
        self.families = families
        self._faces = {}

    def resolve(self, element: TextElement):
        try:
            face = self.families[element.font_family_key][element.font_weight]
        except KeyError:
            raise StageError(
                "MISSING_FONT",
                "Requested font family/weight is not registered",
                affected_ids=[element.id],
            )
        path = Path(face.path)
        if not path.is_file():
            raise StageError("MISSING_FONT", "Registered font file is missing")
        key = (str(path.resolve()), face.index)
        # Cache metadata by bytes hash so replacing a font cannot leave stale glyph evidence.
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if key not in self._faces or self._faces[key][0] != digest:
            try:
                with TTFont(path, fontNumber=face.index) as font:
                    cmap = font.getBestCmap() or {}
                    weight = font["OS/2"].usWeightClass
                self._faces[key] = (digest, set(cmap), weight)
            except Exception as error:
                raise StageError(
                    "MISSING_FONT", "Registered font cannot be parsed"
                ) from error
        digest, cmap, weight = self._faces[key]
        if weight != element.font_weight:
            raise StageError(
                "MISSING_FONT", "Font file does not provide the requested real weight"
            )
        missing = {c for c in element.text if c not in "\n\r" and ord(c) not in cmap}
        if missing:
            raise StageError(
                "MISSING_GLYPH",
                "Font does not cover: " + repr("".join(sorted(missing))),
                affected_ids=[element.id],
            )
        return ImageFont.truetype(
            str(path), element.font_size_px, index=face.index
        ), digest

    @classmethod
    def from_config(cls, config: dict, base: Path):
        families = {}
        for key, weights in config.items():
            families[key] = {}
            for weight, row in weights.items():
                path = Path(row["path"])
                if not path.is_absolute():
                    path = base / path
                families[key][int(weight)] = FontFace(path, int(row.get("index", 0)))
        return cls(families)
