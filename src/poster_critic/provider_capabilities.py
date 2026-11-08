"""Explicit model/endpoint capabilities and actual-byte request preparation.

Profiles are caller-selected evidence, not account permissions or quality claims.
No profile widens the legacy ProviderConfig vendor/key policies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Annotated, Literal, Self, TYPE_CHECKING
from urllib.parse import urlsplit

from pydantic import Field, StringConstraints, field_validator, model_validator

from .contracts import AuditInputV1, Contract, Text, TokenUsage
from .dispatch import _validated_input, _validate_image_bytes
from .errors import InputError
from .identity import fingerprint, input_fingerprint

if TYPE_CHECKING:
    from .providers.chat import ProviderConfig

Digest = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]
PathPart = Annotated[str, StringConstraints(pattern=r'^[A-Za-z_][A-Za-z0-9_]*$')] | Annotated[int, Field(ge=0, le=10000)]
JSONPath = Annotated[tuple[PathPart, ...], Field(min_length=1, max_length=16)]
ResponseMode = Literal['prompt_json', 'json_object', 'json_schema']


class LimitEntryV1(Contract):
    name: Literal['image_bytes', 'data_uri_bytes', 'request_bytes', 'output_tokens']
    value: Annotated[int, Field(gt=0)] | None
    status: Literal['known', 'unverified', 'not_declared']
    required_for_send: bool
    origin: Literal['official', 'local_policy']
    documented_value: Text
    unit_basis: Literal['bytes', 'decimal_mb', 'binary_mib', 'tokens', 'unknown']
    source_url: Text | None

    @model_validator(mode='after')
    def coherent_limit(self) -> Self:
        if (self.status == 'known') != (self.value is not None):
            raise ValueError('only known limits declare a normalized value')
        if self.status == 'known' and ((self.name == 'output_tokens') != (self.unit_basis == 'tokens')):
            raise ValueError('output uses tokens; image/data URI/body use normalized bytes')
        if self.status == 'known' and self.unit_basis == 'unknown':
            raise ValueError('known limits require a documented unit basis')
        if self.origin == 'official' and self.source_url is None:
            raise ValueError('official limits require a source URL')
        return self


class RequestCombinationV1(Contract):
    response_mode: ResponseMode
    thinking_mode: Literal['disabled', 'enabled', 'adaptive', 'omit']
    reasoning_effort: Literal['low', 'medium', 'high', 'xhigh', 'max'] | None
    output_token_parameter: Literal['max_tokens', 'max_completion_tokens']
    output_limit_scope: Literal['answer', 'thinking_and_answer']


class UsageDetailV1(Contract):
    name: Annotated[str, StringConstraints(pattern=r'^[A-Za-z_][A-Za-z0-9_.]{0,99}$')]
    path: JSONPath
    relation: Literal['subset_of_input', 'subset_of_output', 'independent', 'unknown']


class UsageMappingV1(Contract):
    input_total_path: JSONPath
    output_total_path: JSONPath
    details: tuple[UsageDetailV1, ...]

    @model_validator(mode='after')
    def distinct_details(self) -> Self:
        if len({item.name for item in self.details}) != len(self.details):
            raise ValueError('usage detail names must be unique')
        if self.input_total_path == self.output_total_path:
            raise ValueError('usage input and output total paths must differ')
        return self


class ResponseFlagV1(Contract):
    path: JSONPath
    meaning: Literal['finish', 'refusal', 'business_error', 'sensitive']
    value_type: Literal['string', 'bool', 'integer']
    nullable: bool = False
    allowed_values: Annotated[tuple[str | bool | int | None, ...], Field(min_length=1)]
    missing_policy: Literal['allow', 'reject']

    @model_validator(mode='after')
    def typed_whitelist(self) -> Self:
        expected = {'string': str, 'bool': bool, 'integer': int}[self.value_type]
        if (None in self.allowed_values) != self.nullable:
            raise ValueError('nullable flags must explicitly whitelist null')
        if any(value is not None and type(value) is not expected for value in self.allowed_values):
            raise ValueError('flag whitelist must match its declared exact JSON type')
        pairs = [(type(value).__name__, value) for value in self.allowed_values]
        if len(pairs) != len(set(pairs)):
            raise ValueError('flag success whitelist must be unique')
        return self


class CapabilityEvidenceV1(Contract):
    level: Literal['documented', 'offline_contract', 'live_connectivity', 'frozen_quality']
    evidence_ref: Text
    evidence_sha256: Digest | None

    @model_validator(mode='after')
    def receipt_required(self) -> Self:
        if self.level != 'documented' and self.evidence_sha256 is None:
            raise ValueError('non-documentary evidence requires its receipt hash')
        url = urlsplit(self.evidence_ref)
        if url.scheme:
            if (url.scheme != 'https' or not url.hostname or url.username or url.password
                    or url.query or url.fragment):
                raise ValueError('evidence URLs require safe HTTPS references')
        else:
            relative = PurePosixPath(self.evidence_ref)
            if (relative.is_absolute() or '\\' in self.evidence_ref or '..' in relative.parts
                    or str(relative) != self.evidence_ref or self.evidence_ref == '.'):
                raise ValueError('evidence receipts require portable relative paths')
        return self


class ProviderCapabilityV1(Contract):
    schema_version: Literal[1] = 1
    kind: Literal['a4_provider_capability'] = 'a4_provider_capability'
    capability_id: Text
    revision: Text
    provider_profile: Literal['qwen', 'glm', 'minimax', 'compatible']
    model_id: Text
    endpoint_family: Text
    region: Text | None
    allowed_base_urls: Annotated[tuple[Text, ...], Field(min_length=1)]
    input_media_types: Annotated[tuple[Literal['image/png', 'image/jpeg'], ...], Field(min_length=1)]
    input_transport: Literal['data_uri']
    limits: tuple[LimitEntryV1, ...]
    request_combinations: Annotated[tuple[RequestCombinationV1, ...], Field(min_length=1)]
    usage_mapping: UsageMappingV1
    response_flags: tuple[ResponseFlagV1, ...]
    official_sources: tuple[Text, ...]
    checked_at: Text
    evidence: tuple[CapabilityEvidenceV1, ...]
    capability_sha256: Digest

    @field_validator('schema_version', mode='before')
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int:
            raise ValueError('capability version must be an integer')
        return value

    @model_validator(mode='after')
    def coherent_profile(self) -> Self:
        if len({item.name for item in self.limits}) != 4 or len(self.limits) != 4:
            raise ValueError('capabilities require exactly the four distinct limit names')
        if len(set(self.allowed_base_urls)) != len(self.allowed_base_urls) or len(set(self.input_media_types)) != len(self.input_media_types):
            raise ValueError('capability endpoint/media lists must be unique')
        for endpoint in self.allowed_base_urls:
            url = urlsplit(endpoint)
            if (url.scheme != 'https' or not url.hostname or url.username or url.password or url.query
                    or url.fragment or url.port not in (None, 443) or endpoint.rstrip('/').endswith('/chat/completions')):
                raise ValueError('capability bases require explicit safe HTTPS API URLs')
        for url_value in (*self.official_sources, *(item.source_url for item in self.limits if item.source_url)):
            url = urlsplit(url_value)
            if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError('capability source references require safe HTTPS URLs')
        try:
            checked = datetime.fromisoformat(self.checked_at.replace('Z', '+00:00'))
            if checked.utcoffset() is None or checked.utcoffset().total_seconds() != 0:
                raise ValueError
        except ValueError:
            raise ValueError('capability checked_at requires a UTC timestamp') from None
        combinations = [item.model_dump_json() for item in self.request_combinations]
        if len(combinations) != len(set(combinations)):
            raise ValueError('capability request combinations must be unique')
        if len({item.path for item in self.response_flags}) != len(self.response_flags):
            raise ValueError('response flag paths must be unique')
        if fingerprint(self.model_dump(mode='json', exclude={'capability_sha256'})) != self.capability_sha256:
            raise ValueError('capability content differs from its digest')
        return self


def capability_fingerprint(capability: ProviderCapabilityV1) -> str:
    if not isinstance(capability, ProviderCapabilityV1):
        raise InputError('capability requires ProviderCapabilityV1')
    try:
        return ProviderCapabilityV1.model_validate_json(capability.model_dump_json()).capability_sha256
    except (ValueError, TypeError, AttributeError):
        raise InputError('invalid capability content or binding') from None


def make_capability(**fields) -> ProviderCapabilityV1:
    """Construct a fully specified profile; no inferred model/default capabilities."""
    serialized = json.loads(json.dumps(fields, default=lambda value: value.model_dump(mode='json')))
    serialized.pop('capability_sha256', None)
    serialized.setdefault('schema_version', 1)
    serialized.setdefault('kind', 'a4_provider_capability')
    for flag in serialized.get('response_flags', ()):
        flag.setdefault('nullable', False)
    return ProviderCapabilityV1.model_validate_json(json.dumps({
        **serialized, 'capability_sha256': fingerprint(serialized),
    }))


def _strict_object(raw: bytes, limit: int) -> dict:
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        raise ValueError('capability JSON must be bounded bytes')
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('duplicate JSON field')
            result[key] = value
        return result
    def reject(value):
        raise ValueError('non-finite JSON')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=reject)
    if not isinstance(value, dict):
        raise ValueError('capability JSON must be an object')
    return value


def load_capability(path: str | Path) -> ProviderCapabilityV1:
    try:
        selected = Path(path).expanduser().absolute()
        if any(item.is_symlink() for item in (selected, *selected.parents)):
            raise ValueError('capability files must be regular')
        fd = os.open(selected, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise ValueError('capability files must be regular')
            raw = file.read(1024 * 1024 + 1)
        value = _strict_object(raw, 1024 * 1024)
        return ProviderCapabilityV1.model_validate_json(json.dumps(value))
    except (OSError, ValueError, TypeError, UnicodeError):
        raise InputError('invalid capability profile') from None


@dataclass(frozen=True, slots=True)
class PreparedRequestV1:
    input_fingerprint: str
    image_sha256: str
    config_sha256: str
    capability_sha256: str | None
    prompt_sha256: str
    body_sha256: str
    image_bytes: int
    data_uri_bytes: int
    request_bytes: int
    body: bytes = field(repr=False)
    endpoint: str

    def __post_init__(self) -> None:
        digests = (self.input_fingerprint, self.image_sha256, self.config_sha256,
                   self.prompt_sha256, self.body_sha256)
        if self.capability_sha256 is not None:
            digests = (*digests, self.capability_sha256)
        try:
            endpoint = urlsplit(self.endpoint) if isinstance(self.endpoint, str) else None
            port = endpoint.port if endpoint is not None else None
        except ValueError:
            raise InputError('prepared_request_mismatch') from None
        if (any(type(value) is not str or re.fullmatch(r'[0-9a-f]{64}', value) is None for value in digests)
                or any(type(value) is not int or value <= 0 for value in (
                    self.image_bytes, self.data_uri_bytes, self.request_bytes))
                or type(self.body) is not bytes or self.request_bytes != len(self.body)
                or sha256(self.body).hexdigest() != self.body_sha256
                or endpoint is None or endpoint.scheme != 'https' or not endpoint.hostname
                or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
                or port not in (None, 443)):
            raise InputError('prepared_request_mismatch')


def _effective_combination(body: dict, capability: ProviderCapabilityV1) -> RequestCombinationV1:
    response = body.get('response_format', {}).get('type', 'prompt_json')
    thinking = ('enabled' if body['enable_thinking'] else 'disabled') if 'enable_thinking' in body else body.get('thinking', {}).get('type', 'omit')
    parameter = 'max_completion_tokens' if 'max_completion_tokens' in body else 'max_tokens'
    candidates = [item for item in capability.request_combinations
                  if (item.response_mode, item.thinking_mode, item.reasoning_effort, item.output_token_parameter)
                  == (response, thinking, body.get('reasoning_effort'), parameter)]
    if len(candidates) != 1:
        raise InputError('unsupported_request_combination')
    return candidates[0]


def prepare_request(audit: AuditInputV1, prompt: str, config: ProviderConfig,
                    capability: ProviderCapabilityV1 | None, *,
                    endpoint_family: str = 'chat_completions', region: str | None = None) -> PreparedRequestV1:
    from .providers.chat import ProviderConfig, build_chat_request
    try:
        if not isinstance(config, ProviderConfig) or type(config.schema_version) is not int:
            raise InputError('prepared_request_mismatch')
        config = ProviderConfig.model_validate_json(config.model_dump_json())
        audit = _validated_input(audit)
        _validate_image_bytes(audit)
        if not isinstance(prompt, str) or not prompt.strip():
            raise InputError('prepared_request_mismatch')
        if capability is not None:
            capability_fingerprint(capability)
            if (config.profile != capability.provider_profile or config.model != capability.model_id
                    or config.base_url not in capability.allowed_base_urls
                    or endpoint_family != capability.endpoint_family or region != capability.region
                    or audit.image.media_type not in capability.input_media_types):
                raise InputError('capability_mismatch')
        body_value = build_chat_request(config, audit, prompt)
        body = json.dumps(body_value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        uri = body_value['messages'][1]['content'][0]['image_url']['url'].encode('utf-8')
        sizes = {'image_bytes': len(audit.image.data), 'data_uri_bytes': len(uri),
                 'request_bytes': len(body), 'output_tokens': config.max_output_tokens}
        if capability is not None:
            _effective_combination(body_value, capability)
            for limit in capability.limits:
                if limit.required_for_send and limit.status != 'known':
                    raise InputError('capability_limit_unverified')
                if limit.status == 'known' and sizes[limit.name] > limit.value:
                    code = {'image_bytes': 'image_limit_exceeded', 'data_uri_bytes': 'data_uri_limit_exceeded',
                            'request_bytes': 'request_limit_exceeded', 'output_tokens': 'unsupported_request_combination'}[limit.name]
                    raise InputError(code)
        return PreparedRequestV1(
            input_fingerprint(audit), audit.image.sha256, fingerprint(config.model_dump(mode='json')),
            capability.capability_sha256 if capability is not None else None, sha256(prompt.encode('utf-8')).hexdigest(),
            sha256(body).hexdigest(), sizes['image_bytes'], sizes['data_uri_bytes'], sizes['request_bytes'],
            body, config.base_url.rstrip('/') + '/chat/completions',
        )
    except InputError:
        raise
    except (ValueError, TypeError, AttributeError):
        raise InputError('prepared_request_mismatch') from None


def validate_prepared(prepared: PreparedRequestV1, audit: AuditInputV1, prompt: str, config: ProviderConfig,
                      capability: ProviderCapabilityV1 | None, *, endpoint_family: str, region: str | None) -> None:
    if not isinstance(prepared, PreparedRequestV1):
        raise InputError('prepared_request_mismatch')
    prepared.__post_init__()
    expected = prepare_request(audit, prompt, config, capability, endpoint_family=endpoint_family, region=region)
    if prepared != expected:
        raise InputError('prepared_request_mismatch')


MISSING = object()


def _at(value, path):
    for part in path:
        if isinstance(part, str) and isinstance(value, dict):
            value = value.get(part, MISSING)
        elif type(part) is int and isinstance(value, list):
            value = value[part] if part < len(value) else MISSING
        elif value is not MISSING:
            raise ValueError('invalid mapped response container')
        if value is MISSING:
            return MISSING
    return value


def mapped_usage(data: dict, mapping: UsageMappingV1) -> tuple[TokenUsage | None, dict[str, int]]:
    counts, details = [], {}
    for path in (mapping.input_total_path, mapping.output_total_path):
        try:
            value = _at(data, path)
        except ValueError:
            value = MISSING
        counts.append(value)
    usage = TokenUsage(input_tokens=counts[0], output_tokens=counts[1]) if all(
        type(item) is int and item >= 0 for item in counts) else None
    for item in mapping.details:
        try:
            value = _at(data, item.path)
        except ValueError:
            value = MISSING
        if type(value) is int and value >= 0:
            details[item.name] = value
    return usage, details


def response_flag_problem(data: dict, flags: tuple[ResponseFlagV1, ...]) -> tuple[str | None, bool]:
    for rule in flags:
        try:
            value = _at(data, rule.path)
        except ValueError:
            return 'capability_flag_invalid', False
        if value is MISSING:
            if rule.missing_policy == 'reject':
                return 'capability_flag_missing', False
            continue
        expected = {'string': str, 'bool': bool, 'integer': int}[rule.value_type]
        if value is None:
            if not rule.nullable:
                return 'capability_flag_invalid', False
        elif type(value) is not expected:
            return 'capability_flag_invalid', False
        if not any(type(value) is type(allowed) and value == allowed for allowed in rule.allowed_values):
            return 'capability_' + rule.meaning + '_flag', rule.meaning in ('refusal', 'sensitive')
    return None, False


def capability_template(config: ProviderConfig, *, checked_at: str,
                        endpoint_family: str = 'chat_completions', region: str | None = None) -> ProviderCapabilityV1:
    """Explicit model-bound starting template; unresolved required limits block send.

    The template records existing A4 policies and documentary references. It
    never claims live/offline/quality receipts or adds a model to ProviderConfig.
    """
    from .providers.chat import ProviderConfig
    from .adapters.local_input import MAX_IMAGE_BYTES
    if not isinstance(config, ProviderConfig):
        raise InputError('capability_mismatch')
    config = ProviderConfig.model_validate_json(config.model_dump_json())
    sources = {
        'qwen': 'https://www.alibabacloud.com/help/en/model-studio/vision',
        'glm': 'https://docs.bigmodel.cn/llms.txt',
        'minimax': 'https://platform.minimax.cn/docs/api-reference/text-openai-api',
        'compatible': None,
    }
    source = sources[config.profile]
    minimax = config.profile == 'minimax'
    entries = []
    for name in ('image_bytes', 'data_uri_bytes', 'request_bytes', 'output_tokens'):
        required = name != 'data_uri_bytes'
        value = ({'image_bytes': 10_000_000, 'request_bytes': 64_000_000, 'output_tokens': 8192}.get(name)
                 if minimax else {'image_bytes': MAX_IMAGE_BYTES, 'output_tokens': 8192}.get(name))
        if config.profile == 'glm' and name == 'image_bytes':
            value = None
        status = 'known' if value is not None else 'not_declared' if not required else 'unverified'
        entries.append(LimitEntryV1(
            name=name, value=value, status=status, required_for_send=required, origin='local_policy',
            documented_value=('A4 conservative decimal interpretation of documented MB, not a verified vendor unit.'
                              if minimax and name in ('image_bytes', 'request_bytes')
                              else 'Existing A4 bounded policy; unresolved model/endpoint limits require explicit verification.'),
            unit_basis='tokens' if name == 'output_tokens' else 'decimal_mb' if minimax and value is not None else 'bytes' if value is not None else 'unknown',
            source_url=source,
        ))
    thinking = config.thinking_mode or {'qwen':'disabled','glm':'disabled','minimax':'adaptive','compatible':'omit'}[config.profile]
    parameter = config.output_token_parameter or ('max_completion_tokens' if config.profile in ('qwen','minimax') else 'max_tokens')
    effort = config.reasoning_effort or ('low' if config.profile == 'minimax' and config.model == 'MiniMax-M3.1-Flash-Preview' else None)
    combo = RequestCombinationV1(response_mode=config.response_format, thinking_mode=thinking,
                                reasoning_effort=effort, output_token_parameter=parameter,
                                output_limit_scope='thinking_and_answer' if thinking in ('enabled','adaptive') else 'answer')
    details = tuple(UsageDetailV1(name=group+'.'+name, path=('usage',group,name),
        relation='subset_of_input' if group == 'prompt_tokens_details' else 'subset_of_output')
        for group in ('prompt_tokens_details','completion_tokens_details')
        for name in ('cached_tokens','reasoning_tokens','image_tokens','text_tokens'))
    return make_capability(
        capability_id=config.profile+'-explicit-model-template', revision='a4-policy-template-v1',
        provider_profile=config.profile, model_id=config.model, endpoint_family=endpoint_family, region=region,
        allowed_base_urls=(config.base_url,), input_media_types=('image/png','image/jpeg'), input_transport='data_uri',
        limits=tuple(entries), request_combinations=(combo,),
        usage_mapping=UsageMappingV1(input_total_path=('usage','prompt_tokens'),output_total_path=('usage','completion_tokens'),details=details),
        response_flags=(
            ResponseFlagV1(path=('choices',0,'finish_reason'),meaning='finish',value_type='string',
                           nullable=False,allowed_values=('stop',),missing_policy='reject'),
            ResponseFlagV1(path=('choices',0,'message','refusal'),meaning='refusal',value_type='string',
                           nullable=True,allowed_values=(None,''),missing_policy='allow'),
        ), official_sources=(source,) if source else (), checked_at=checked_at,
        evidence=(CapabilityEvidenceV1(level='documented',evidence_ref=source,evidence_sha256=None),) if source else (),
    )


