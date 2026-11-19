"""Fetch public Noto fonts; verify locked hashes before installing them."""

from pathlib import Path
import hashlib, json, urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    directory = ROOT / "resources/fonts"
    directory.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for row in manifest["files"]:
        target = directory / row["filename"]
        if (
            target.is_file()
            and hashlib.sha256(target.read_bytes()).hexdigest() == row["sha256"]
        ):
            print("Verified:", row["filename"])
            continue
        request = urllib.request.Request(
            row["url"], headers={"User-Agent": "CreativePosterFontSetup/0.1"}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(24 * 1024 * 1024 + 1)
        if (
            len(data) > 24 * 1024 * 1024
            or hashlib.sha256(data).hexdigest() != row["sha256"]
        ):
            raise ValueError(
                "Font download hash mismatch; keep existing file and inspect upstream: "
                + row["filename"]
            )
        temporary = target.with_suffix(target.suffix + ".download")
        temporary.write_bytes(data)
        temporary.replace(target)
        print("Installed:", row["filename"])


if __name__ == "__main__":
    main()
