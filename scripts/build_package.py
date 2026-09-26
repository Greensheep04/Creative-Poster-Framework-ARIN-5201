from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "scripts" / "package-manifest.txt"
ARCHIVE_PATH = ROOT / "creative-poster-framework.zip"
FORBIDDEN_PARTS = {".git", ".scratch", ".venv", "__pycache__", "runs"}
FORBIDDEN_SUFFIXES = {".pdf", ".xlsx", ".xls", ".env"}


def manifest_paths() -> tuple[PurePosixPath, ...]:
    names = tuple(
        line.strip()
        for line in MANIFEST_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if len(names) != len(set(names)):
        raise ValueError("package manifest contains duplicate paths")
    paths = tuple(PurePosixPath(name) for name in names)
    for relative_path in paths:
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"package manifest path must stay inside the project: {relative_path}")
        if FORBIDDEN_PARTS.intersection(relative_path.parts):
            raise ValueError(f"package manifest contains a forbidden directory: {relative_path}")
        if relative_path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise ValueError(f"package manifest contains a forbidden file type: {relative_path}")
        source_path = ROOT.joinpath(*relative_path.parts)
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
    return paths


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    paths = manifest_paths()
    with ZipFile(ARCHIVE_PATH, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for relative_path in paths:
            archive.write(
                ROOT.joinpath(*relative_path.parts),
                arcname=relative_path.as_posix(),
            )
    expected_members = tuple(sorted(path.as_posix() for path in paths))
    with ZipFile(ARCHIVE_PATH) as archive:
        actual_members = tuple(sorted(archive.namelist()))
    if actual_members != expected_members:
        raise AssertionError("created archive does not match the explicit package manifest")
    print(
        json.dumps(
            {
                "archive": str(ARCHIVE_PATH),
                "sha256": sha256(ARCHIVE_PATH),
                "member_count": len(actual_members),
                "members": list(actual_members),
            },
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
