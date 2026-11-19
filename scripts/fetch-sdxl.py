"""Download pinned official FP16 SDXL files. Only this setup script accesses the network."""

import argparse, concurrent.futures, hashlib, json, os, urllib.request
from pathlib import Path

MODEL = "stabilityai/stable-diffusion-xl-base-1.0"
REVISION = "462165984030d82259a11f4367a4eed129e94a7b"


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def official_file_matches(path, row):
    if not path.is_file():
        return False
    expected_size = row.get("size") or row.get("lfs", {}).get("size")
    if expected_size is not None and path.stat().st_size != expected_size:
        return False
    expected_sha = row.get("lfs", {}).get("sha256")
    if expected_sha:
        return sha(path) == expected_sha
    blob = row.get("blobId")
    if not blob:
        return False
    digest = hashlib.sha1()
    digest.update(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest() == blob


def download_parallel(
    url, partial, total, opener, *, workers=4, chunk_size=32 * 1024 * 1024
):
    offset = partial.stat().st_size if partial.exists() else 0
    chunks = [
        (start, min(start + chunk_size, total) - 1)
        for start in range(offset, total, chunk_size)
    ]

    def fetch(bounds):
        start, end = bounds
        part = partial.with_name(partial.name + f".segment-{start}")
        stalls = 0
        while True:
            have = part.stat().st_size if part.exists() else 0
            if have == end - start + 1:
                return part
            if have > end - start + 1:
                raise ValueError("Invalid cached segment size")
            at = start + have
            req = urllib.request.Request(url, headers={"Range": f"bytes={at}-{end}"})
            try:
                with opener(req, timeout=90) as response:
                    if response.status != 206 or not response.headers.get(
                        "Content-Range", ""
                    ).startswith(f"bytes {at}-"):
                        raise ValueError("Invalid parallel range response")
                    with part.open("ab") as out:
                        while chunk := response.read(1024 * 1024):
                            out.write(chunk)
            except (OSError, urllib.error.URLError):
                stalls = (
                    0 if part.exists() and part.stat().st_size > have else stalls + 1
                )
                if stalls >= 6:
                    raise
                continue
            stalls = 0 if part.stat().st_size > have else stalls + 1
            if stalls >= 6:
                raise ValueError("Segment made no progress")

    # pool.map returns range order; a completed segment is appended exactly once.
    with (
        concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool,
        partial.open("ab") as out,
    ):
        for part in pool.map(fetch, chunks):
            with part.open("rb") as inp:
                while chunk := inp.read(1024 * 1024):
                    out.write(chunk)
            out.flush()
            part.unlink()
            print(
                f"Downloaded {partial.name}: {partial.stat().st_size}/{total}",
                flush=True,
            )


def download_component(
    row, root, *, opener=urllib.request.urlopen, url=None, parallel_workers=1
):
    n = row["rfilename"]
    root = root.resolve()
    target = (root / n).resolve()
    if not target.is_relative_to(root):
        raise ValueError("Invalid component path")
    expected = row.get("lfs", {}).get("sha256")
    total = row.get("size") or row.get("lfs", {}).get("size")
    if official_file_matches(target, row):
        return n, sha(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    url = url or f"https://huggingface.co/{MODEL}/resolve/{REVISION}/{n}?download=true"
    print("Downloading " + n, flush=True)
    if parallel_workers > 1 and total and total > 32 * 1024 * 1024:
        download_parallel(url, partial, total, opener, workers=parallel_workers)
        digest = sha(partial)
        if not official_file_matches(partial, row):
            raise ValueError("Remote checksum mismatch: " + n)
        os.replace(partial, target)
        print("Verified " + n, flush=True)
        return n, digest
    stalls = 0
    while True:
        offset = partial.stat().st_size if partial.exists() else 0
        if total and offset >= total:
            if offset == total and (not expected or sha(partial) == expected):
                break
            raise ValueError("Remote checksum mismatch: " + n)
        if total and total > 32 * 1024 * 1024:
            end = min(offset + 32 * 1024 * 1024, total) - 1
            headers = {"Range": f"bytes={offset}-{end}"}
        else:
            headers = {"Range": f"bytes={offset}-"} if offset else {}
        request = urllib.request.Request(url, headers=headers)
        try:
            with opener(request, timeout=90) as response:
                if offset and response.status != 206:
                    raise ValueError("Server does not support safe resume")
                resumed = offset and response.status == 206
                if resumed and not response.headers.get("Content-Range", "").startswith(
                    f"bytes {offset}-"
                ):
                    raise ValueError("Invalid resumed range")
                with partial.open("ab" if resumed else "wb") as out:
                    while chunk := response.read(1024 * 1024):
                        out.write(chunk)
        except (OSError, urllib.error.URLError) as error:
            progress = partial.exists() and partial.stat().st_size > offset
            stalls = 0 if progress else stalls + 1
            if stalls >= 6:
                raise
            print(f"Retry for {n}: {type(error).__name__}", flush=True)
            continue
        if not total or partial.stat().st_size == total:
            break
        if partial.stat().st_size <= offset:
            stalls += 1
        else:
            stalls = 0
        if stalls >= 6:
            raise ValueError("Download made no progress: " + n)
        print(f"Resume {n} at {partial.stat().st_size}/{total}", flush=True)
    if total and partial.stat().st_size != total:
        raise ValueError("Incomplete model component: " + n)
    digest = sha(partial)
    if not official_file_matches(partial, row):
        raise ValueError("Remote checksum mismatch: " + n)
    os.replace(partial, target)
    print("Verified " + n, flush=True)
    return n, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Temporary direct transport; no system proxy changes",
    )
    parser.add_argument(
        "--mirror",
        default="https://huggingface.co",
        help="Public payload mirror; identities/checksums still come from official pinned metadata",
    )
    parser.add_argument("--workers", type=int, choices=range(1, 17), default=4)
    args = parser.parse_args()
    transport = (
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if args.direct
        else urllib.request.build_opener()
    )
    root = args.model_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    info = json.load(
        transport.open(
            f"https://huggingface.co/api/models/{MODEL}/revision/{REVISION}?blobs=true",
            timeout=60,
        )
    )
    rows = []
    for row in info["siblings"]:
        n = row["rfilename"]
        if (
            n in {"model_index.json", "LICENSE.md", "README.md"}
            or n.startswith(("tokenizer/", "tokenizer_2/", "scheduler/"))
            or (
                n.split("/")[0] in {"unet", "vae", "text_encoder", "text_encoder_2"}
                and (n.endswith("/config.json") or n.endswith(".fp16.safetensors"))
            )
        ):
            rows.append(row)
    print(
        json.dumps(
            {"model": MODEL, "revision": REVISION, "components": len(rows)},
            ensure_ascii=False,
        ),
        flush=True,
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        files = dict(
            pool.map(
                lambda row: download_component(
                    row,
                    root,
                    opener=transport.open,
                    url=f"{args.mirror.rstrip('/')}/{MODEL}/resolve/{REVISION}/{row['rfilename']}?download=true",
                    parallel_workers=args.workers,
                ),
                rows,
            )
        )
    manifest = {
        "model_id": MODEL,
        "revision": REVISION,
        "variant": "fp16",
        "files": files,
        "license": "CreativeML Open RAIL++-M",
    }
    (root / "poster-model-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "downloaded": len(files),
                "manifest": str(root / "poster-model-manifest.json"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
