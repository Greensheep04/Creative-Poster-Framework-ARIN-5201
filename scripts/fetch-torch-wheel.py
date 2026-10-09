"""Resume the official pinned Windows CUDA wheel and verify its uv.lock SHA256."""

import argparse, importlib.util, json, urllib.request
from pathlib import Path

NAME = "torch-2.8.0+cu128-cp311-cp311-win_amd64.whl"
URL = "https://download.pytorch.org/whl/cu128/torch-2.8.0%2Bcu128-cp311-cp311-win_amd64.whl"
SHA = "34c55443aafd31046a7963b63d30bc3b628ee4a704f826796c865fdfd05bb596"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--directory", type=Path, required=True)
parser.add_argument("--direct", action="store_true")
parser.add_argument("--workers", type=int, choices=range(1, 17), default=4)
parser.add_argument("--mirror", default="https://download.pytorch.org/whl")
args = parser.parse_args()
URL = args.mirror.rstrip("/") + "/cu128/torch-2.8.0%2Bcu128-cp311-cp311-win_amd64.whl"
transport = (
    urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if args.direct
    else urllib.request.build_opener()
)
request = urllib.request.Request(URL, headers={"Range": "bytes=0-0"})
with transport.open(request, timeout=60) as response:
    total = int(response.headers["Content-Range"].split("/")[-1])
spec = importlib.util.spec_from_file_location(
    "sdxl_downloader", Path(__file__).with_name("fetch-sdxl.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
name, digest = module.download_component(
    {"rfilename": NAME, "size": total, "lfs": {"sha256": SHA}},
    args.directory,
    url=URL,
    opener=transport.open,
    parallel_workers=args.workers,
)
print(json.dumps({"wheel": str(args.directory / name), "sha256": digest}), flush=True)
