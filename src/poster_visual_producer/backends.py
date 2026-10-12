"""Trusted backend configuration. GPU dependencies are optional and imported lazily."""

from __future__ import annotations
import asyncio, hashlib, importlib, json, os, secrets, threading, time
from pathlib import Path
from uuid import uuid4
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .contracts import StageError
from .generators import GeneratorCapabilities, GeneratedImage, ProviderError
from .imaging import encode_png


class SDXLConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    model_dir: Path
    model_id: Literal["stabilityai/stable-diffusion-xl-base-1.0"] = (
        "stabilityai/stable-diffusion-xl-base-1.0"
    )
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    steps: int = Field(default=30, strict=True, ge=1, le=100)
    guidance_scale: float = Field(default=6.0, gt=1, le=20, allow_inf_nan=False)
    cpu_offload: bool = Field(default=False, strict=True)


def verify_weights(config):
    root = config.model_dir.resolve()
    if not (root / "model_index.json").is_file():
        raise StageError(
            "MISSING_MODEL",
            "Local model_index.json missing; run scripts/fetch-sdxl.py first",
        )
    manifest_path = root / "poster-model-manifest.json"
    if not manifest_path.is_file():
        raise StageError("MISSING_MODEL", "Pinned model manifest missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("revision") != config.revision
        or manifest.get("model_id") != config.model_id
    ):
        raise StageError("STALE_VERSION", "Local model manifest identity mismatch")
    files = manifest.get("files", {})
    required = {
        "model_index.json",
        "LICENSE.md",
        "README.md",
        "scheduler/scheduler_config.json",
        "unet/config.json",
        "unet/diffusion_pytorch_model.fp16.safetensors",
        "vae/config.json",
        "vae/diffusion_pytorch_model.fp16.safetensors",
        "text_encoder/config.json",
        "text_encoder/model.fp16.safetensors",
        "text_encoder_2/config.json",
        "text_encoder_2/model.fp16.safetensors",
        "tokenizer/merges.txt",
        "tokenizer/vocab.json",
        "tokenizer/tokenizer_config.json",
        "tokenizer/special_tokens_map.json",
        "tokenizer_2/merges.txt",
        "tokenizer_2/vocab.json",
        "tokenizer_2/tokenizer_config.json",
        "tokenizer_2/special_tokens_map.json",
    }
    if not required.issubset(files):
        raise StageError("MISSING_MODEL", "Incomplete FP16 model manifest")
    for name, digest in files.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise StageError("MISSING_MODEL", "Invalid local model component")
        with path.open("rb") as file:
            actual = hashlib.file_digest(file, "sha256").hexdigest()
        if actual != digest:
            raise StageError("INVALID_ASSET", f"Local model hash mismatch: {name}")


class LocalSDXLGenerator:
    def __init__(self, config: SDXLConfig):
        self.config = config
        self.capabilities = GeneratorCapabilities(
            provider="local_diffusers",
            model=config.model_id,
            sizes=((768, 1024), (1024, 1024), (1024, 768)),
            supports_seed=True,
            supports_negative_prompt=True,
            weights_version=config.revision,
            generation_parameters=(
                ("steps", config.steps),
                ("guidance_scale", config.guidance_scale),
                ("dtype", "float16"),
                ("cpu_offload", config.cpu_offload),
                ("pipeline", "sdxl-base-v1"),
            ),
        )
        self._pipeline = None
        self._torch = None
        self._lock = threading.RLock()

    def _load(self):
        verify_weights(self.config)
        try:
            import torch
            from diffusers import StableDiffusionXLPipeline
        except ImportError as error:
            raise StageError(
                "MISSING_DEPENDENCY", "Install the local-gpu optional dependencies"
            ) from error
        if not torch.cuda.is_available():
            raise StageError(
                "GPU_UNAVAILABLE", "CUDA GPU is unavailable; no silent CPU fallback"
            )
        pipe = StableDiffusionXLPipeline.from_pretrained(
            str(self.config.model_dir),
            torch_dtype=torch.float16,
            variant="fp16",
            use_safetensors=True,
            local_files_only=True,
            add_watermarker=False,
        )
        pipe.enable_vae_slicing()
        if self.config.cpu_offload:
            pipe.enable_model_cpu_offload()
        else:
            pipe.to("cuda")
        self._torch = torch
        self._pipeline = pipe

    async def generate(self, request):
        return await asyncio.to_thread(self._generate, request)

    def _generate(self, request):
        if request.size not in self.capabilities.sizes:
            raise StageError(
                "UNSUPPORTED_CAPABILITY", "Unsupported local generation size"
            )
        with self._lock:
            started = time.perf_counter()
            if self._pipeline is None:
                self._load()
            torch = self._torch
            seed = request.seed if request.seed is not None else secrets.randbits(32)
            rng = torch.Generator(device="cpu").manual_seed(seed)
            cuda = getattr(torch, "cuda", None)
            if cuda:
                cuda.reset_peak_memory_stats()
            try:
                with torch.inference_mode():
                    result = self._pipeline(
                        prompt=request.prompt,
                        negative_prompt=request.negative_prompt,
                        width=request.size[0],
                        height=request.size[1],
                        generator=rng,
                        num_inference_steps=self.config.steps,
                        guidance_scale=self.config.guidance_scale,
                    )
                png = encode_png(result.images[0].convert("RGB"))
            except Exception as error:
                # Preserve no-success semantics; A3 records the attempted image operation.
                raise ProviderError(
                    f"Local GPU generation failed ({type(error).__name__})",
                    submission_status="unknown",
                ) from error
            usage = {
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "api_cost_usd": 0,
                "peak_allocated_vram_bytes": cuda.max_memory_allocated()
                if cuda
                else None,
            }
            return GeneratedImage(
                png,
                request_id="local-" + uuid4().hex,
                usage=usage,
                provenance={
                    "source_mode": "local_gpu",
                    "model_id": self.config.model_id,
                    "model_revision": self.config.revision,
                    "actual_seed": seed,
                    "steps": self.config.steps,
                    "guidance_scale": self.config.guidance_scale,
                    "dtype": "float16",
                    "cpu_offload": self.config.cpu_offload,
                    "torch_version": str(torch.__version__),
                    "visual_quality_evaluated": False,
                },
            )


def create_generator(config: dict):
    """Config comes from the application/user, never from model output."""
    config = dict(config)
    kind = config.pop("kind", None)
    if kind == "cloud_api":
        from .cloud_backend import CloudAPIConfig, CloudImageGenerator
        return CloudImageGenerator(CloudAPIConfig.model_validate(config))
    if kind == "qwen_image":
        from .qwen_backend import QwenImageConfig, LocalQwenImageGenerator
        raw = str(config.get("model_dir", ""))
        expanded = os.path.expandvars(raw)
        if "$" in expanded or not expanded:
            raise ValueError("Model directory environment variable is missing")
        config["model_dir"] = expanded
        return LocalQwenImageGenerator(QwenImageConfig.model_validate(config))
    if kind == "sdxl":
        raw = str(config.get("model_dir", ""))
        expanded = os.path.expandvars(raw)
        if "$" in expanded or not expanded:
            raise ValueError("Model directory environment variable is missing")
        config["model_dir"] = expanded
        return LocalSDXLGenerator(SDXLConfig.model_validate(config))
    if kind == "factory":
        if set(config) - {"factory", "options"}:
            raise ValueError("Unknown factory configuration fields")
        module, name = config["factory"].split(":", 1)
        generator = getattr(importlib.import_module(module), name)(
            config.get("options", {})
        )
        if not callable(getattr(generator, "generate", None)) or not isinstance(
            getattr(generator, "capabilities", None), GeneratorCapabilities
        ):
            raise TypeError("External factory must return an ImageGenerator")
        return generator
    raise ValueError("Backend kind must be sdxl, qwen_image, cloud_api or factory")
