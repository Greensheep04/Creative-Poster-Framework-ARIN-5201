import hashlib, importlib.util, io
from pathlib import Path


def module():
    p = Path(__file__).resolve().parents[2] / "scripts/fetch-sdxl.py"
    spec = importlib.util.spec_from_file_location("fetch_sdxl_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert hasattr(m, "download_component"), "resumable downloader required"
    return m


def test_truncated_download_resumes_and_verifies(tmp_path):
    m = module()
    data = b"valid weights" * 100
    row = {
        "rfilename": "unet/weights.safetensors",
        "size": len(data),
        "lfs": {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)},
    }
    calls = []

    class Response(io.BytesIO):
        def __init__(self, body, status, headers):
            super().__init__(body)
            self.status = status
            self.headers = headers

    def opener(req, timeout):
        calls.append(req.get_header("Range"))
        if len(calls) == 1:
            return Response(data[:100], 200, {})
        return Response(
            data[100:], 206, {"Content-Range": f"bytes 100-{len(data) - 1}/{len(data)}"}
        )

    name, digest = m.download_component(row, tmp_path, opener=opener)
    assert (tmp_path / name).read_bytes() == data
    assert calls == [None, "bytes=100-"]
    assert digest == row["lfs"]["sha256"]


def test_corrupt_completed_download_is_not_published(tmp_path):
    m = module()
    import pytest

    row = {
        "rfilename": "unet/bad.safetensors",
        "size": 3,
        "lfs": {"sha256": "a" * 64, "size": 3},
    }

    class Response(io.BytesIO):
        status = 200
        headers = {}

    with pytest.raises(ValueError, match="checksum"):
        m.download_component(
            row, tmp_path, opener=lambda req, timeout: Response(b"bad")
        )
    assert not (tmp_path / row["rfilename"]).exists()


def test_parallel_ranges_assemble_in_order(tmp_path):
    m = module()
    assert hasattr(m, "download_parallel"), "parallel range transport required"
    data = b"0123456789abcdef"
    target = tmp_path / "model.part"

    class Response(io.BytesIO):
        status = 206

        def __init__(self, start, end):
            super().__init__(data[start : end + 1])
            self.headers = {"Content-Range": f"bytes {start}-{end}/{len(data)}"}

    def opener(req, timeout):
        start, end = map(int, req.get_header("Range").removeprefix("bytes=").split("-"))
        return Response(start, end)

    m.download_parallel(
        "https://trusted.example/file",
        target,
        len(data),
        opener,
        workers=3,
        chunk_size=4,
    )
    assert target.read_bytes() == data
    assert not list(tmp_path.glob("*.segment-*"))
