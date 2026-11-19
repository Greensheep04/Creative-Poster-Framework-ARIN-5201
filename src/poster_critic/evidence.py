"""Prepare local image evidence with explicit parent and coordinate bindings.

Cropping is not a quality observation. This module never calls OCR or a model,
and a crop is a separate evidence object, not an AuditInputV1 full poster.
"""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import stat
from typing import Annotated, Literal, Self

from PIL import Image, UnidentifiedImageError
from pydantic import Field, StringConstraints, ValidationError, field_validator, model_validator

from .adapters.local_input import MAX_IMAGE_BYTES, load_input
from .contracts import AuditInputV1, Contract, Text
from .dispatch import _validated_input, _validate_image_bytes
from .errors import InputError
from .identity import input_fingerprint
from .reporting import write_bytes, write_json

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Dimension = Annotated[int, Field(ge=1, le=4096)]
MAX_EVIDENCE_PIXELS = 4 * 1024 * 1024


class CropEvidenceV1(Contract):
    schema_version: Literal[1] = 1
    evidence_kind: Literal["crop"] = "crop"
    sample_id: Text
    parent_image_sha256: Digest
    input_fingerprint: Digest
    parent_width: Literal[720] = 720
    parent_height: Literal[1280] = 1280
    crop_bbox_xyxy: tuple[int, int, int, int]
    width: Dimension
    height: Dimension
    scale_x: float
    scale_y: float
    media_type: Literal["image/png"] = "image/png"
    sha256: Digest
    data: bytes = Field(exclude=True, repr=False, min_length=1)

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("evidence schema version must be an integer")
        return value

    @model_validator(mode="after")
    def validate_transform_and_pixels(self) -> Self:
        x0, y0, x1, y1 = self.crop_bbox_xyxy
        if not 0 <= x0 < x1 <= 720 or not 0 <= y0 < y1 <= 1280:
            raise ValueError("crop must be inside the full poster canvas")
        if self.width * self.height > MAX_EVIDENCE_PIXELS:
            raise ValueError("crop evidence exceeds the pixel limit")
        if self.scale_x != self.width / (x1 - x0) or self.scale_y != self.height / (y1 - y0):
            raise ValueError("crop scale must match the exact coordinate transform")
        if len(self.data) > MAX_IMAGE_BYTES or hashlib.sha256(self.data).hexdigest() != self.sha256:
            raise ValueError("crop bytes exceed the limit or differ from their digest")
        try:
            with Image.open(BytesIO(self.data)) as image:
                if image.format != "PNG" or image.size != (self.width, self.height) or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("crop bytes must be a single PNG of the declared size")
                image.load()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise ValueError("cannot decode crop evidence") from exc
        return self


def _bbox(value: object, width: int, height: int) -> tuple[float, float, float, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise InputError("evidence coordinates require an xyxy box")
    if any(type(item) not in (int, float) for item in value):
        raise InputError("evidence coordinates must be finite numbers")
    x0, y0, x1, y1 = value
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise InputError("evidence coordinates are outside their canvas")
    return tuple(float(item) for item in value)


def _checked(evidence: CropEvidenceV1) -> CropEvidenceV1:
    if not isinstance(evidence, CropEvidenceV1):
        raise InputError("crop evidence requires CropEvidenceV1")
    try:
        return CropEvidenceV1.model_validate({**evidence.model_dump(mode="python"), "data": evidence.data})
    except (ValidationError, ValueError, TypeError, AttributeError) as exc:
        raise InputError("invalid crop evidence") from exc


def crop_evidence(audit: AuditInputV1, bbox_xyxy: tuple[int, int, int, int], *,
                  output_size: tuple[int, int] | None = None) -> CropEvidenceV1:
    """Make a bounded PNG crop; resizing is explicit and uses LANCZOS."""
    audit = _validated_input(audit)
    _validate_image_bytes(audit)
    if not isinstance(bbox_xyxy, tuple) or len(bbox_xyxy) != 4 or any(type(item) is not int for item in bbox_xyxy):
        raise InputError("crop bbox must be four integer pixel coordinates")
    _bbox(bbox_xyxy, 720, 1280)
    x0, y0, x1, y1 = bbox_xyxy
    size = output_size if output_size is not None else (x1 - x0, y1 - y0)
    if (not isinstance(size, tuple) or len(size) != 2
            or any(type(item) is not int or not 1 <= item <= 4096 for item in size)
            or size[0] * size[1] > MAX_EVIDENCE_PIXELS):
        raise InputError("crop output size must be bounded positive integer pixels")
    with Image.open(BytesIO(audit.image.data)) as source:
        image = source.crop(bbox_xyxy)
        if size != image.size:
            image = image.resize(size, Image.Resampling.LANCZOS)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
    data = buffer.getvalue()
    return CropEvidenceV1(
        sample_id=audit.sample_id, parent_image_sha256=audit.image.sha256,
        input_fingerprint=input_fingerprint(audit), crop_bbox_xyxy=bbox_xyxy,
        width=size[0], height=size[1], scale_x=size[0] / (x1 - x0), scale_y=size[1] / (y1 - y0),
        sha256=hashlib.sha256(data).hexdigest(), data=data,
    )


def crop_to_canvas_bbox(evidence: CropEvidenceV1, box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    evidence = _checked(evidence)
    x0, y0, x1, y1 = _bbox(box, evidence.width, evidence.height)
    left, top, _, _ = evidence.crop_bbox_xyxy
    return (left + x0 / evidence.scale_x, top + y0 / evidence.scale_y,
            left + x1 / evidence.scale_x, top + y1 / evidence.scale_y)


def canvas_to_crop_bbox(evidence: CropEvidenceV1, box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    evidence = _checked(evidence)
    x0, y0, x1, y1 = _bbox(box, 720, 1280)
    left, top, right, bottom = evidence.crop_bbox_xyxy
    if not (left <= x0 < x1 <= right and top <= y0 < y1 <= bottom):
        raise InputError("canvas box is not wholly inside this crop")
    return ((x0 - left) * evidence.scale_x, (y0 - top) * evidence.scale_y,
            (x1 - left) * evidence.scale_x, (y1 - top) * evidence.scale_y)


def validate_crop_evidence(evidence: CropEvidenceV1, audit: AuditInputV1) -> CropEvidenceV1:
    """Verify pixel derivation against the current complete source, not metadata."""
    evidence = _checked(evidence)
    expected = crop_evidence(audit, evidence.crop_bbox_xyxy, output_size=(evidence.width, evidence.height))
    if (evidence.model_dump(mode="json") != expected.model_dump(mode="json")
            or evidence.data != expected.data):
        raise InputError("crop evidence does not match its current parent input and pixels")
    return evidence


def write_crop_evidence(evidence: CropEvidenceV1, output_dir: str | Path) -> Path:
    """New directory only; evidence.json is the last-written completion marker."""
    evidence = _checked(evidence)
    output = Path(output_dir).expanduser()
    try:
        output.mkdir(parents=True, exist_ok=False)
        write_bytes(output / "crop.png", evidence.data)
        write_json(output / "evidence.json", {
            "schema_version": 1, "image_path": "crop.png", "evidence": evidence.model_dump(mode="json"),
        })
    except OSError as exc:
        raise InputError("cannot publish crop evidence; existing or partial output must not be overwritten") from exc
    return output / "evidence.json"


def read_crop_evidence(output_dir: str | Path, audit: AuditInputV1) -> CropEvidenceV1:
    output = Path(output_dir).expanduser().absolute()
    try:
        if any(path.is_symlink() for path in (output, *output.parents)):
            raise InputError("crop evidence references must not be symlinks")
        if not output.is_dir():
            raise InputError("crop evidence must be a complete directory")
        def read_regular(name: str, limit: int) -> bytes:
            path = output / name
            if path.is_symlink() or not path.is_file():
                raise InputError("crop metadata and image must be regular files")
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as file:
                if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                    raise InputError("crop metadata and image must be regular files")
                data = file.read(limit + 1)
            if len(data) > limit:
                raise InputError("crop evidence file exceeds its size limit")
            return data
        packet = json.loads(read_regular("evidence.json", 128 * 1024))
        if (not isinstance(packet, dict) or set(packet) != {"schema_version", "image_path", "evidence"}
                or type(packet["schema_version"]) is not int or packet["schema_version"] != 1
                or packet["image_path"] != "crop.png" or not isinstance(packet["evidence"], dict)):
            raise InputError("invalid crop evidence manifest")
        data = read_regular("crop.png", MAX_IMAGE_BYTES)
        if "data" in packet["evidence"]:
            raise InputError("crop bytes belong in the referenced image file")
        fields = dict(packet["evidence"])
        if isinstance(fields.get("crop_bbox_xyxy"), list):
            fields["crop_bbox_xyxy"] = tuple(fields["crop_bbox_xyxy"])
        evidence = CropEvidenceV1.model_validate({**fields, "data": data})
    except InputError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
        raise InputError("cannot read a complete crop evidence bundle") from exc
    return validate_crop_evidence(evidence, audit)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--bbox", type=int, nargs=4, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--size", type=int, nargs=2)
    args = parser.parse_args(argv)
    try:
        evidence = crop_evidence(load_input(args.input), tuple(args.bbox), output_size=tuple(args.size) if args.size else None)
        print(write_crop_evidence(evidence, args.out).resolve())
        return 0
    except (InputError, OSError, ValueError) as exc:
        print(f"evidence error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
