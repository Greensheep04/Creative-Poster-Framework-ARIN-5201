from __future__ import annotations
import io, warnings
from dataclasses import dataclass
from PIL import Image, ImageOps
from .contracts import StageError, ImageElement


def decode_image(data: bytes, *, max_bytes=32 * 1024 * 1024, max_pixels=20_000_000):
    if not isinstance(data, bytes) or not data or len(data) > max_bytes:
        raise StageError("INVALID_ASSET", "Asset bytes absent or exceed size limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as header:
                if header.format not in {"PNG", "JPEG", "WEBP"}:
                    raise StageError("INVALID_ASSET", "Unsupported source format")
                if header.width * header.height > max_pixels:
                    raise StageError("INVALID_ASSET", "Asset exceeds pixel limit")
                header.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                return ImageOps.exif_transpose(image).convert("RGBA")
    except StageError:
        raise
    except Exception as error:
        raise StageError("INVALID_ASSET", "Image cannot be safely decoded") from error


@dataclass
class PreparedAsset:
    image: Image.Image
    record: dict


def prepare_asset(data: bytes, element: ImageElement) -> PreparedAsset:
    original = decode_image(data)
    sw, sh = original.size
    w, h = element.box.width, element.box.height
    if element.fit == "cover":
        scale = max(w / sw, h / sh)
        cw, ch = w / scale, h / scale
        fx, fy = element.focus_point_norm
        left = max(0.0, min(sw - cw, fx * sw - cw / 2))
        top = max(0.0, min(sh - ch, fy * sh - ch / 2))
        crop = [left, top, left + cw, top + ch]
        image = original.resize((w, h), Image.Resampling.LANCZOS, box=tuple(crop))
        transform = [scale, 0, 0, scale, -left * scale, -top * scale]
        content_box = [0, 0, w, h]
    else:
        scale = min(w / sw, h / sh)
        nw, nh = max(1, round(sw * scale)), max(1, round(sh * scale))
        x, y = (w - nw) // 2, (h - nh) // 2
        image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        image.alpha_composite(
            original.resize((nw, nh), Image.Resampling.LANCZOS), (x, y)
        )
        crop = None
        transform = [nw / sw, 0, 0, nh / sh, x, y]
        content_box = [x, y, nw, nh]
    return PreparedAsset(
        image,
        {
            "original_size": [sw, sh],
            "processed_size": [w, h],
            "fit": element.fit,
            "focus_point_norm": list(element.focus_point_norm),
            "crop_box": crop,
            "content_box": content_box,
            "raw_to_processed": transform,
        },
    )


def encode_png(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, "PNG", compress_level=6)
    return stream.getvalue()
