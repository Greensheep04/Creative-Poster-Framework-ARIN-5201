"""Frozen existing text comparison and optional local diagnostic presentation.

Profiles describe policy and evaluation scope, not recognition quality. They
never translate copy, change a report, or provide hints to a model request.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Annotated, Literal, Self, get_args
import unicodedata

from pydantic import Field, StringConstraints, field_validator, model_validator

from .checks import text_matches
from .contracts import Contract, CopyItem, Text
from .errors import InputError
from .identity import fingerprint
from .structured_diagnostics import DiagnosticCode, DiagnosticsEnvelopeV1

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
TextLocale = Literal["zh-Hans", "zh-Hant", "en", "mixed", "und"]
DiagnosticLocale = Literal["zh-Hans", "en", "legacy"]
COMPARISON_POLICY_VERSION = "legacy-nfc-typographic-v1"
MESSAGE_CATALOG_REVISION = 1

# English templates intentionally retain the existing typed projection's
# semantic distinctions. Observed evidence stays in the original machine data.
_EN = {
    "review_incomplete": ("No successful, protocol-valid observation is recorded for this candidate.", "A valid recorded observation covering the current audit requirements.", "A1 should inspect the actual call records, verify the candidate and requirements, and request a new audit."),
    "keyword_unknown": ("The keyword's visual expression is unconfirmed.", "Recognizable candidate evidence supporting the original keyword.", "A1 should compare this keyword with the current candidate and obtain evidence before choosing a production change."),
    "keyword_failed": ("The recorded keyword check failed.", "Recognizable candidate evidence supporting the original keyword.", "A1 should compare this keyword with the current candidate and obtain evidence before choosing a production change."),
    "constraint_unknown": ("The user constraint is unconfirmed.", "Candidate evidence that can be compared with this original constraint.", "A1 should retain the original constraint and verify candidate evidence before assigning a change."),
    "constraint_failed": ("The recorded user-constraint check failed.", "Candidate evidence that can be compared with this original constraint.", "A1 should retain the original constraint and verify candidate evidence before assigning a change."),
    "required_text_mapping_missing": ("The fixed-text constraint has no matching required approved-copy entry.", "A required approved-copy entry preserving the original fixed text and exact match policy.", "A1 should verify the original fixed-text requirement, supply its approved-copy mapping, and request a new audit."),
    "copy_unknown": ("Copy accuracy is unconfirmed.", "A reliable transcription of this copy from the current candidate.", "A1 should compare the transcription with the approved copy and matching policy, then request a new audit after evidence or production changes."),
    "copy_failed": ("The recorded copy-accuracy check failed.", "A reliable transcription of this copy from the current candidate.", "A1 should compare the transcription with the approved copy and matching policy, then request a new audit after evidence or production changes."),
    "copy_readability_unknown": ("Normal-viewing copy readability is unconfirmed.", "Readable visual evidence of this copy at normal viewing size.", "A1 should verify this copy in the current candidate before deciding whether to change layout or rendering."),
    "copy_readability_failed": ("The recorded copy-readability check failed.", "Readable visual evidence of this copy at normal viewing size.", "A1 should verify this copy in the current candidate before deciding whether to change layout or rendering."),
    "approved_copy_missing": ("The declared audit context for this check was not supplied.", "Current approved copy with IDs and matching policy.", "A1 should supply and verify current context before requesting this check again."),
    "layout_context_missing": ("The declared audit context for this check was not supplied.", "Current declared layout elements, boxes, and copy references.", "A1 should supply and verify current context before requesting this check again."),
    "check_unknown": ("This check lacks conclusive evidence.", "Check-specific evidence of the current candidate.", "A1 should compare the typed check and its preserved human reason with the current candidate, then obtain evidence before selecting a change."),
    "check_failed": ("The recorded check failed.", "Check-specific evidence of the current candidate.", "A1 should compare the typed check and its preserved human reason with the current candidate, then obtain evidence before selecting a change."),
    "check_not_assessed": ("This check was not assessed.", "Check-specific evidence of the current candidate.", "A1 should compare the typed check and its preserved human reason with the current candidate, then obtain evidence before selecting a change."),
    "report_uncertainty_unlocated": ("The report records an uncertainty with no separately typed source or object.", "Additional evidence identifying the affected requirement or check.", "A1 should inspect the preserved uncertainty and current candidate, identify its scope, and request a new audit when evidence is available."),
}
_ZH = {
    "review_incomplete": ("当前候选没有成功且通过协议校验的观察记录。", "覆盖当前审核要求的有效观察及其调用记录。", "A1 应检查实际调用记录，核对候选和原始要求后重新审核。"),
    "keyword_unknown": ("原始关键词的画面表达尚未确认。", "当前候选中能够支持该原始关键词的可辨认证据。", "A1 应对照当前候选核实该关键词，取得证据后再决定产出需要何种修改。"),
    "keyword_failed": ("已记录的关键词检查未通过。", "当前候选中能够支持该原始关键词的可辨认证据。", "A1 应对照当前候选核实该关键词，取得证据后再决定产出需要何种修改。"),
    "constraint_unknown": ("原始用户约束尚未确认。", "能够与该原始约束对照的当前候选证据。", "A1 应保留原始约束，核实候选证据后再分派修改。"),
    "constraint_failed": ("已记录的用户约束检查未通过。", "能够与该原始约束对照的当前候选证据。", "A1 应保留原始约束，核实候选证据后再分派修改。"),
    "required_text_mapping_missing": ("固定文字约束缺少对应的必需正式文案记录。", "保留原始固定文字且采用 exact 匹配的必需正式文案记录。", "A1 应核对原始固定文字要求，补充其正式文案映射后重新审核。"),
    "copy_unknown": ("正式文案的文字准确性尚未确认。", "从当前候选取得的该文案可靠逐字转录。", "A1 应按正式文案及匹配政策核对转录，补充证据或修改产出后重新审核。"),
    "copy_failed": ("已记录的正式文案文字准确性检查未通过。", "从当前候选取得的该文案可靠逐字转录。", "A1 应按正式文案及匹配政策核对转录，补充证据或修改产出后重新审核。"),
    "copy_readability_unknown": ("该文案在正常观看尺寸下的可读性尚未确认。", "当前候选中该文案在正常观看尺寸下可读的视觉证据。", "A1 应先核实当前候选的该文案，再决定是否修改布局或渲染。"),
    "copy_readability_failed": ("已记录的正常观看尺寸下文案可读性检查未通过。", "当前候选中该文案在正常观看尺寸下可读的视觉证据。", "A1 应先核实当前候选的该文案，再决定是否修改布局或渲染。"),
    "approved_copy_missing": ("未提供该检查声明需要的审核上下文。", "当前正式文案及其 ID 和匹配政策。", "A1 应补充并核实当前上下文，再请求该检查。"),
    "layout_context_missing": ("未提供该检查声明需要的审核上下文。", "当前声明的布局元素、坐标框和文案引用。", "A1 应补充并核实当前上下文，再请求该检查。"),
    "check_unknown": ("该检查缺少确定证据。", "能够核实当前候选该项检查的证据。", "A1 应将该结构化检查及保留的原始原因与当前候选对照，取得证据后再选择修改。"),
    "check_failed": ("已记录的该项检查未通过。", "能够核实当前候选该项检查的证据。", "A1 应将该结构化检查及保留的原始原因与当前候选对照，取得证据后再选择修改。"),
    "check_not_assessed": ("该项检查尚未执行。", "能够核实当前候选该项检查的证据。", "A1 应将该结构化检查及保留的原始原因与当前候选对照，取得证据后再选择修改。"),
    "report_uncertainty_unlocated": ("报告记录了一项不确定性，其来源或对象未单独以结构化字段定位。", "能够确定受影响要求或检查的补充证据。", "A1 应检查保留的原始不确定项和当前候选，确定其范围，在有证据后重新审核。"),
}


def message_catalog_fingerprint() -> str:
    codes = set(get_args(DiagnosticCode))
    if set(_EN) != codes or set(_ZH) != codes:
        raise InputError("diagnostic message catalog does not cover the current typed codes")
    return fingerprint({"revision": MESSAGE_CATALOG_REVISION, "en": _EN, "zh-Hans": _ZH})


class LanguageProfileV1(Contract):
    schema_version: Literal[1] = 1
    kind: Literal["a4_language_profile"] = "a4_language_profile"
    profile_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
    revision: Annotated[int, Field(ge=1)]
    text_locales: Annotated[tuple[TextLocale, ...], Field(min_length=1, max_length=5)]
    diagnostic_locale: DiagnosticLocale
    comparison_policy_version: Literal["legacy-nfc-typographic-v1"]
    unicode_data_version: Annotated[str, StringConstraints(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    message_catalog_sha256: Digest
    profile_sha256: Digest

    @field_validator("schema_version", "revision", mode="before")
    @classmethod
    def integer_versions(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("language profile versions must be integers")
        return value

    @model_validator(mode="after")
    def valid_identity(self) -> Self:
        if len(set(self.text_locales)) != len(self.text_locales):
            raise ValueError("language profile locales must be unique")
        if self.profile_sha256 != fingerprint(self.model_dump(mode="json", exclude={"profile_sha256"})):
            raise ValueError("language profile content does not match its hash")
        return self


def language_profile(*, diagnostic_locale: DiagnosticLocale = "legacy",
                     text_locales: tuple[TextLocale, ...] = ("zh-Hans", "zh-Hant", "en", "mixed", "und"),
                     profile_id: str = "a4-legacy-comparison", revision: int = 1) -> LanguageProfileV1:
    fields = dict(schema_version=1, kind="a4_language_profile", profile_id=profile_id, revision=revision,
                  text_locales=text_locales, diagnostic_locale=diagnostic_locale,
                  comparison_policy_version=COMPARISON_POLICY_VERSION,
                  unicode_data_version=unicodedata.unidata_version,
                  message_catalog_sha256=message_catalog_fingerprint())
    try:
        canonical = {**fields, "text_locales": list(text_locales)}
        return LanguageProfileV1(**fields, profile_sha256=fingerprint(canonical))
    except (ValueError, TypeError):
        raise InputError("invalid explicit A4 language profile") from None


def legacy_language_profile() -> LanguageProfileV1:
    return language_profile()


def validate_language_profile(profile: LanguageProfileV1) -> LanguageProfileV1:
    if not isinstance(profile, LanguageProfileV1):
        raise InputError("language policy requires LanguageProfileV1")
    try:
        checked = LanguageProfileV1.model_validate_json(profile.model_dump_json())
    except (ValueError, TypeError, AttributeError):
        raise InputError("invalid A4 language profile identity") from None
    if (checked.message_catalog_sha256 != message_catalog_fingerprint()
            or checked.unicode_data_version != unicodedata.unidata_version):
        raise InputError("language profile differs from the current catalog or Unicode runtime")
    return checked


def read_language_profile(path: str | Path) -> LanguageProfileV1:
    """Strict bounded regular JSON with duplicate keys rejected, no fallback."""
    try:
        path = Path(path).expanduser()
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError
            raw = handle.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise ValueError
        def pairs(values):
            result = {}
            for key, value in values:
                if key in result:
                    raise ValueError
                result[key] = value
            return result
        json.loads(raw, object_pairs_hook=pairs)
        return validate_language_profile(LanguageProfileV1.model_validate_json(raw))
    except (OSError, ValueError, TypeError):
        raise InputError("invalid explicit language profile; no translation or fallback selected") from None


def text_matches_with_profile(observed: str, copy: CopyItem, profile: LanguageProfileV1) -> bool:
    """Delegate to unchanged checks, with an explicit verified policy binding."""
    validate_language_profile(profile)
    if not isinstance(observed, str) or not isinstance(copy, CopyItem):
        raise InputError("text comparison requires literal observed text and CopyItem")
    try:
        copy = CopyItem.model_validate_json(copy.model_dump_json())
    except (ValueError, TypeError, AttributeError):
        raise InputError("invalid approved-copy comparison policy") from None
    return text_matches(observed, copy)


class DiagnosticDisplayItemV1(Contract):
    locale: DiagnosticLocale
    code: DiagnosticCode
    subject_id: Text | None
    doubt: Text
    missing_evidence: Text
    suggestion: Text


class RenderedDiagnosticsV1(Contract):
    schema_version: Literal[1] = 1
    kind: Literal["a4_diagnostics_display"] = "a4_diagnostics_display"
    locale: DiagnosticLocale
    language_profile_sha256: Digest
    machine: DiagnosticsEnvelopeV1
    items: tuple[DiagnosticDisplayItemV1, ...]

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("diagnostic display version must be an integer")
        return value

    @model_validator(mode="after")
    def preserve_subjects(self) -> Self:
        if len(self.items) != len(self.machine.items):
            raise ValueError("diagnostic display must preserve all original items")
        for shown, original in zip(self.items, self.machine.items, strict=True):
            if shown.locale != self.locale or shown.code != original.code or shown.subject_id != original.subject_id:
                raise ValueError("diagnostic display must preserve locale, code and subject identities")
        return self


def render_diagnostics(envelope: DiagnosticsEnvelopeV1, profile: LanguageProfileV1) -> RenderedDiagnosticsV1:
    """Local presentation only. The complete original machine fields survive."""
    selected = validate_language_profile(profile)
    if not isinstance(envelope, DiagnosticsEnvelopeV1):
        raise InputError("diagnostic presentation requires DiagnosticsEnvelopeV1")
    try:
        original = DiagnosticsEnvelopeV1.model_validate_json(envelope.model_dump_json())
    except (ValueError, TypeError, AttributeError):
        raise InputError("invalid typed diagnostic envelope") from None
    rendered = []
    for item in original.items:
        if selected.diagnostic_locale == "legacy":
            doubt, missing, suggestion = item.doubt, item.missing_evidence, item.suggestion
        else:
            catalog = _ZH if selected.diagnostic_locale == "zh-Hans" else _EN
            doubt, missing, suggestion = catalog[item.code]
        rendered.append(DiagnosticDisplayItemV1(locale=selected.diagnostic_locale, code=item.code,
            subject_id=item.subject_id, doubt=doubt, missing_evidence=missing, suggestion=suggestion))
    return RenderedDiagnosticsV1(locale=selected.diagnostic_locale, language_profile_sha256=selected.profile_sha256,
                                 machine=original, items=tuple(rendered))
