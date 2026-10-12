from __future__ import annotations
import copy, json, io
from PIL import Image
from poster_framework import (
    AgentInput,
    AgentOutput,
    ArtifactPurpose,
    ArtifactReference,
    RevisionAction,
)
from poster_framework.generation import GenerationFingerprint
from .contracts import (
    PosterSpec,
    CreativeBrief,
    StageError,
    validate_pair,
    TextElement,
    ImageElement,
)
from .generators import compile_request, ProviderError
from .renderer import preflight, render_poster
from .registration import SPEC_FORMAT, CANDIDATE_FORMAT

_NOTE = "visual-production-state-v1"


class VisualProducerAgent:
    def __init__(
        self,
        generator,
        fonts,
        *,
        seed=None,
        negative_policy="append",
        max_safe_retries=1,
    ):
        if max_safe_retries not in (0, 1):
            raise ValueError("MVP permits zero or one safe retry")
        self.generator, self.fonts = generator, fonts
        self.seed, self.negative_policy, self.max_safe_retries = (
            seed,
            negative_policy,
            max_safe_retries,
        )

    async def run(self, inputs: AgentInput, context) -> AgentOutput:
        if inputs.content_format != SPEC_FORMAT:
            raise StageError(
                "SCHEMA_INVALID", "Visual Producer requires poster.spec@0.1.0"
            )
        if inputs.metadata.identity != context.identity:
            raise StageError("STALE_VERSION", "Invocation identity mismatch")
        spec = PosterSpec.model_validate(inputs.content)
        memory = context.memory.context()
        upstream = {
            item["name"]: item
            for item in memory["artifacts"]
            if item["name"] in {"brief", "layout"}
        }
        if "brief" not in upstream or "layout" not in upstream:
            raise StageError(
                "MISSING_INPUT",
                "Current read-only Brief and Spec must be in framework memory",
            )
        if upstream["layout"]["content"] != inputs.content:
            raise StageError(
                "STALE_VERSION", "Input Spec is not the current published layout"
            )
        deps = {
            (d.artifact_id, d.revision) for d in inputs.metadata.version_dependencies
        }
        for name, item in upstream.items():
            ref = item["reference"]
            context.files.read(ArtifactReference.model_validate(ref))
            if name == "layout" and (ref["artifact_id"], ref["revision"]) not in deps:
                raise StageError(
                    "STALE_VERSION", "Current upstream dependency is not declared"
                )
        brief = CreativeBrief.model_validate(upstream["brief"]["content"])
        if spec.run_id != context.original_request.run_id:
            raise StageError("STALE_VERSION", "Spec belongs to another run")
        validate_pair(
            spec, brief, [k.keyword_id for k in context.original_request.keywords]
        )
        preflight(spec, self.fonts)  # fail before any costly image submission
        notes = [n for n in memory["notes"] if n["note_id"].startswith(_NOTE + ":")]
        previous = copy.deepcopy(notes[-1]["content"]) if notes else {}
        action = context.metadata.revision_action
        if action in {RevisionAction.rerender, RevisionAction.regenerate_asset}:
            if not previous or previous.get("spec") != inputs.content:
                raise StageError(
                    "STALE_VERSION",
                    "Asset repair/rerender requires unchanged current Spec",
                )
        target = context.metadata.revision_target
        target_asset = None
        if action == RevisionAction.rerender:
            old = previous["candidate_ref"]
            if (old["artifact_id"], old["revision"]) != (
                target.artifact_id,
                target.revision,
            ):
                raise StageError(
                    "STALE_VERSION", "Rerender target is not the latest candidate"
                )
        if action == RevisionAction.regenerate_asset:
            matches = [
                key
                for key, row in previous["bindings"].items()
                if (row["source_ref"]["artifact_id"], row["source_ref"]["revision"])
                == (target.artifact_id, target.revision)
            ]
            if len(matches) != 1:
                raise StageError(
                    "STALE_VERSION",
                    "Repair target does not identify the current source asset",
                )
            target_asset = matches[0]
        constraints = ()
        if (
            action in {RevisionAction.regenerate_asset, RevisionAction.rerender}
            and memory["revision_history"]
        ):
            changes = memory["revision_history"][-1]["changes"]
            details = changes.get("details", {}) if isinstance(changes, dict) else {}
            repair = details.get("repair_plan", {}) if isinstance(details, dict) else {}
            if repair:
                expected = repair.get("expected_versions")
                if expected != {
                    "brief_version": brief.brief_version,
                    "spec_version": spec.spec_version,
                }:
                    raise StageError(
                        "STALE_VERSION", "Business repair versions do not match"
                    )
                raw_constraints = repair.get("constraints", [])
                if not isinstance(raw_constraints, list) or len(raw_constraints) > 16:
                    raise StageError(
                        "SCHEMA_INVALID",
                        "Repair constraints must be a bounded list of strings",
                    )
                constraints = tuple(raw_constraints)
                if any(
                    not isinstance(c, str) or not c.strip() or len(c) > 1000
                    for c in constraints
                ):
                    raise StageError(
                        "SCHEMA_INVALID", "Repair constraints must be text"
                    )
        start = context.metadata.usage.image_operations
        bindings = {}
        source_data = {}
        refs = []
        assets = []
        decisions = []
        warnings = []
        caps = self.generator.capabilities
        for plan in spec.asset_plan:
            request = compile_request(
                plan,
                caps,
                seed=self.seed,
                negative_policy=self.negative_policy,
                repair_constraints=constraints if plan.asset_id == target_asset else (),
            )
            request_json = request.as_json()
            fingerprint = GenerationFingerprint.build(
                provider=caps.provider,
                model=caps.model,
                weights=caps.weights_version,
                prompt=request.prompt,
                parameters={
                    "size": list(request.size),
                    "negative_prompt": request.negative_prompt,
                    "seed": request.seed,
                    "backend": dict(caps.generation_parameters),
                },
                reference_materials=(),
                processing_config={"source_pipeline": "validated-source-v1"},
            )
            base_request = compile_request(
                plan, caps, seed=self.seed, negative_policy=self.negative_policy
            )
            base_fingerprint = GenerationFingerprint.build(
                provider=caps.provider,
                model=caps.model,
                weights=caps.weights_version,
                prompt=base_request.prompt,
                parameters={
                    "size": list(base_request.size),
                    "negative_prompt": base_request.negative_prompt,
                    "seed": base_request.seed,
                    "backend": dict(caps.generation_parameters),
                },
                reference_materials=(),
                processing_config={"source_pipeline": "validated-source-v1"},
            )
            old = previous.get("bindings", {}).get(plan.asset_id)
            force = plan.asset_id == target_asset
            reusable = (
                old
                and old.get("base_fingerprint_key", old["fingerprint_key"])
                == base_fingerprint.key
                and not force
            )
            cache_hit = False
            generated = None
            if action == RevisionAction.rerender:
                if not reusable:
                    raise StageError(
                        "CONSTRAINT_CONFLICT",
                        "Rerender cannot change generation conditions",
                    )
            if reusable:
                ref = ArtifactReference.model_validate(old["source_ref"])
                data = context.files.read(ref)
                version = old["asset_version"]
                cache_hit = True
                # Compatibility uses the base request; provenance retains the
                # actual request which may include approved repair constraints.
                fingerprint = GenerationFingerprint.model_validate(old["fingerprint"])
                context.files.register_generation(ref, fingerprint)
                provenance = old["provenance"]
            else:
                hit = None if force else context.cache.get(fingerprint)
                if hit:
                    cached_ref = hit.artifacts[0]
                    data = context.files.read(cached_ref)
                    old_ref = (
                        ArtifactReference.model_validate(old["source_ref"])
                        if old
                        else None
                    )
                    version = (
                        old["asset_version"] + 1
                        if old and old_ref != cached_ref
                        else (old["asset_version"] if old else 1)
                    )
                    provenance = copy.deepcopy(hit.value)
                    cache_hit = True
                    if (
                        (old_ref is not None and old_ref != cached_ref and action == RevisionAction.revise_layout)
                        or any(row["source_ref"]["artifact_id"] == cached_ref.artifact_id for row in bindings.values())
                    ):
                        # Cached bytes replace the currently selected source,
                        # without another image request. Publish explicit lineage.
                        with Image.open(io.BytesIO(data)) as header:
                            fmt = header.format
                        suffix, mime = {
                            "PNG": ("png", "image/png"),
                            "JPEG": ("jpg", "image/jpeg"),
                            "WEBP": ("webp", "image/webp"),
                        }[fmt]
                        ref = context.files.write_bytes(
                            context.files.invocation_path(
                                "assets/" + plan.asset_id + "-source." + suffix
                            ),
                            data,
                            purpose=ArtifactPurpose.asset,
                            media_type=mime,
                            generation_fingerprint=fingerprint,
                            replaces=old_ref,
                        )
                        provenance["cache_reused_from"] = cached_ref.model_dump(
                            mode="json"
                        )
                    else:
                        ref = cached_ref
                        context.files.register_generation(ref, fingerprint)
                else:

                    async def invoke(_provider, _model, _request, _parameters):
                        return await self.generator.generate(request)

                    for attempt in range(self.max_safe_retries + 1):
                        try:
                            generated = await context.call_model(
                                provider=caps.provider,
                                model=caps.model,
                                request_data=request_json,
                                parameters={
                                    "size": list(request.size),
                                    "seed": request.seed,
                                    "backend": dict(caps.generation_parameters),
                                    "negative_prompt": request.negative_prompt,
                                },
                                kind="image_operation",
                                invoke=invoke,
                            )
                            break
                        except ProviderError as error:
                            if not (
                                error.retry_safe
                                and error.submission_status
                                in {"not_submitted", "rejected"}
                                and attempt < self.max_safe_retries
                            ):
                                raise
                    data = generated.data
                    with Image.open(io.BytesIO(data)) as header:
                        fmt = header.format
                    suffix, mime = {
                        "PNG": ("png", "image/png"),
                        "JPEG": ("jpg", "image/jpeg"),
                        "WEBP": ("webp", "image/webp"),
                    }[fmt]
                    replaced = (
                        ArtifactReference.model_validate(old["source_ref"])
                        if old and (force or action == RevisionAction.revise_layout)
                        else None
                    )
                    ref = context.files.write_bytes(
                        context.files.invocation_path(
                            "assets/" + plan.asset_id + "-source." + suffix
                        ),
                        data,
                        purpose=ArtifactPurpose.asset,
                        media_type=mime,
                        generation_fingerprint=fingerprint,
                        replaces=replaced,
                    )
                    version = old["asset_version"] + 1 if old else 1
                    provenance = {
                        "provider": caps.provider,
                        "model": caps.model,
                        "weights_version": caps.weights_version,
                        "request": request_json,
                        "request_id": generated.request_id,
                        "provider_usage": generated.usage,
                        "cost": None,
                        "source_mode": "offline_fixture"
                        if caps.offline_fixture
                        else "provider",
                        "source_metadata": generated.provenance,
                    }
                    context.cache.put(fingerprint, provenance, artifacts=(ref,))
            # Files are decoded again by the renderer, including reused/cache sources.
            source_data[plan.asset_id] = data
            bindings[plan.asset_id] = {
                "source_ref": ref.model_dump(mode="json"),
                "asset_version": version,
                "fingerprint_key": fingerprint.key,
                "base_fingerprint_key": base_fingerprint.key,
                "fingerprint": fingerprint.model_dump(mode="json"),
                "provenance": provenance,
            }
            decisions.append({
                "asset_id": plan.asset_id,
                "action": "reuse_current" if reusable else ("cache" if cache_hit else ("regenerate" if force else "generate")),
                "reason": "compatible current source" if reusable else ("verified fingerprint cache" if cache_hit else ("authorized target repair" if force else "no compatible source")),
                "fingerprint_key": fingerprint.key,
                "target": target.model_dump(mode="json") if force else None,
            })
            refs.append(ref)
            warnings.extend(request.adaptations)
            assets.append(
                {
                    "asset_id": plan.asset_id,
                    "asset_version": version,
                    "element_ids": [e.id for e in spec.elements if isinstance(e, ImageElement) and e.asset_id == plan.asset_id],
                    "processed_bindings": [],
                    "source_ref": ref.model_dump(mode="json"),
                    "file_ref": self._relative(ref, context),
                    "processed_size": plan.target_size.model_dump(),
                    "cache_hit": cache_hit,
                    "provenance": provenance,
                }
            )
        rendered = render_poster(spec, source_data, self.fonts)
        processed_refs = {}
        for element_id, data in rendered.processed.items():
            processed_refs[element_id] = context.files.write_bytes(
                context.files.invocation_path("processed/" + element_id + ".png"),
                data,
                purpose=ArtifactPurpose.evidence,
                media_type="image/png",
            )
            refs.append(processed_refs[element_id])
        rendered.manifest.update(
            schema_version="0.1.0",
            run_id=spec.run_id,
            based_on={
                "brief_version": brief.brief_version,
                "spec_version": spec.spec_version,
            },
            framework_dependencies=[
                {
                    "artifact_id": item["reference"]["artifact_id"],
                    "revision": item["reference"]["revision"],
                }
                for item in upstream.values()
            ],
            warnings=warnings,
            execution_decisions=decisions,
            source_mode="offline_fixture" if caps.offline_fixture else "provider",
        )
        for row in rendered.manifest["elements"]:
            if row["type"] == "image":
                row["processed_ref"] = processed_refs[row["element_id"]].model_dump(
                    mode="json"
                )
                asset = next(a for a in assets if a["asset_id"] == row["asset_id"])
                asset["source_size"] = dict(zip(("width", "height"), row["processing"]["original_size"]))
                asset["processed_bindings"].append({
                    "element_id": row["element_id"], "processed_ref": row["processed_ref"],
                    "processed_size": dict(zip(("width", "height"), row["processing"]["processed_size"])),
                    "asset_to_canvas": row["asset_to_canvas"],
                })
                if len(asset["processed_bindings"]) == 1:
                    asset["asset_to_canvas"] = row["asset_to_canvas"]
                    asset["processed_ref"] = row["processed_ref"]
        poster = context.files.write_bytes(
            context.files.invocation_path("poster.png"),
            rendered.png,
            purpose=ArtifactPurpose.candidate,
            media_type="image/png",
        )
        manifest = self._save_json(context, "render_manifest.json", rendered.manifest)
        refs.extend((poster, manifest))
        index = self._save_json(
            context,
            "artifact_index.json",
            {
                "schema_version": "0.1.0",
                "run_id": spec.run_id,
                "artifacts": [r.model_dump(mode="json") for r in refs],
                "sources": {key: row["provenance"] for key, row in bindings.items()},
            },
        )
        refs.append(index)
        content = {
            "schema_version": "0.1.0",
            "run_id": spec.run_id,
            "candidate_id": poster.artifact_id,
            "status": "rendered",
            "based_on": {
                "brief_version": brief.brief_version,
                "spec_version": spec.spec_version,
            },
            "candidate_ref": poster.model_dump(mode="json"),
            "poster_ref": self._relative(poster, context),
            "render_manifest_ref": self._relative(manifest, context),
            "manifest_ref": manifest.model_dump(mode="json"),
            "artifact_index_ref": self._relative(index, context),
            "index_ref": index.model_dump(mode="json"),
            "canvas": {"width": 720, "height": 1280},
            "assets": assets,
            "execution_decisions": decisions,
            "text_manifest": [
                {
                    k: row[k]
                    for k in (
                        "element_id",
                        "copy_ref",
                        "text",
                        "font_family_key",
                        "font_weight",
                        "font_size_px",
                        "spec_box",
                    )
                }
                for row in rendered.manifest["elements"]
                if row["type"] == "text"
            ],
            "usage_delta": {
                "image_ops": context.metadata.usage.image_operations - start,
                "render_calls": 1,
                "cache_hits": sum(a["cache_hit"] for a in assets),
            },
            "source_mode": "offline_fixture" if caps.offline_fixture else "provider",
            "warnings": warnings,
        }
        context.memory.add_note(
            note_id=_NOTE + ":" + context.identity.invocation_id,
            content={
                "spec": copy.deepcopy(inputs.content),
                "bindings": bindings,
                "candidate_ref": poster.model_dump(mode="json"),
            },
            based_on={},
        )
        return AgentOutput(
            content=content, content_format=CANDIDATE_FORMAT, artifacts=tuple(refs)
        )

    @staticmethod
    def _relative(reference, context):
        prefix = "runs/" + context.original_request.run_id + "/"
        if not reference.uri.startswith(prefix):
            raise StageError("INVALID_ASSET", "Reference outside current run")
        return reference.uri[len(prefix) :]

    @staticmethod
    def _save_json(context, name, value):
        return context.files.write_bytes(
            context.files.invocation_path(name),
            json.dumps(
                value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2
            ).encode("utf-8"),
            purpose=ArtifactPurpose.evidence,
            media_type="application/json",
        )
