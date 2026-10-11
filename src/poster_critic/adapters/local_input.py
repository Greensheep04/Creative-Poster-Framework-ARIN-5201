"""Load a critic-owned JSON manifest and the actual local image bytes."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from ..contracts import AuditInputV1, CopyItem, ImagePayload, LocalManifest
from ..errors import InputError

MAX_IMAGE_BYTES = 20 * 1024 * 1024


def load_input(path: str | Path) -> AuditInputV1:
    manifest_path = Path(path).expanduser().resolve()
    try:
        manifest = LocalManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        image_path = Path(manifest.image.path).expanduser()
        if not image_path.is_absolute():
            image_path = manifest_path.parent / image_path
        with image_path.open("rb") as source:
            data = source.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise InputError("image exceeds the 20 MiB input limit")
        with Image.open(BytesIO(data)) as image:
            if image.format not in ("PNG", "JPEG"):
                raise InputError("image must be a PNG or JPEG")
            if image.size != (720, 1280):
                raise InputError(f"image must be 720x1280, got {image.size}")
            if getattr(image, "n_frames", 1) != 1:
                raise InputError("animated images are not supported")
            media_type = "image/png" if image.format == "PNG" else "image/jpeg"
            image.load()
        fields = manifest.model_dump(exclude={"image"})
        copy_items = list(manifest.approved_copy or ())
        for constraint in manifest.user_constraints:
            if constraint.name != "required_text":
                continue
            item = CopyItem(
                copy_id=f"constraint:{constraint.constraint_id}",
                text=constraint.value,
                required=True,
            )
            existing = next((copy for copy in copy_items if copy.copy_id == item.copy_id), None)
            if existing is not None and existing != item:
                raise InputError(f"copy ID conflicts with required_text: {item.copy_id}")
            if existing is None:
                copy_items.append(item)
        if copy_items:
            fields["approved_copy"] = tuple(copy_items)
        payload = ImagePayload(data=data, media_type=media_type, sha256=hashlib.sha256(data).hexdigest())
        return AuditInputV1.model_validate({**fields, "image": payload})
    except InputError:
        raise
    except (OSError, UnicodeError, ValidationError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise InputError(f"cannot load audit input: {exc}") from exc
