"""Validate scoped A1/A2 identity and the inner provenance of visual contracts."""
import json
from poster_framework.types import ArtifactPurpose
from poster_framework.intent_mapping import extract_visual_requirements
from poster_framework.visual_requirements import CONTRACT_MEDIA_TYPE,VisualContract,digest
from .contracts import StageError

def _pointer(value,pointer):
    for part in pointer.lstrip('/').split('/'):
        part=part.replace('~1','/').replace('~0','~')
        value=value[int(part)] if isinstance(value,list) else value[part]
    return value

def _verify_requirements(contract,envelope,source_bytes):
    raw=json.loads(source_bytes)
    expected=extract_visual_requirements(raw,source_bytes=source_bytes)
    baseline={r.requirement_id:r for r in expected.requirements}
    if expected.issues or not contract.requirements:
        raise StageError('CONSTRAINT_CONFLICT','Missing concrete visual requirements')
    if set(contract.source_fields)!=set(expected.source_fields) or contract.keyword_evidence!=expected.keyword_evidence:
        raise StageError('CONSTRAINT_CONFLICT','A1 field/keyword evidence coverage changed')
    ids=[r.requirement_id for r in contract.requirements]
    if len(ids)!=len(set(ids)) or not set(baseline)<=set(ids):
        raise StageError('CONSTRAINT_CONFLICT','Visual requirement identity/coverage incomplete')
    for req in contract.requirements:
        if req.source.sha256!=contract.source_sha256 or _pointer(raw,req.source.pointer)!=req.source.quote:
            raise StageError('CONSTRAINT_CONFLICT','Visual requirement quote differs from original A1')
        if req.requirement_id in baseline:
            origin=baseline[req.requirement_id]
            if req.category!=origin.category or (origin.priority in ('required','forbidden') and req.priority!=origin.priority):
                raise StageError('CONSTRAINT_CONFLICT','Core/forbidden requirement changed priority or category')
        else:
            suffix='-visibility' if req.requirement_id.endswith('-visibility') else '-quantity'
            origin=baseline.get(req.requirement_id.removesuffix(suffix))
            if origin is None or req.source!=origin.source:
                raise StageError('CONSTRAINT_CONFLICT','Unknown derived visual requirement')
            if suffix=='-quantity' and req.resolution_id!='HELD_PROPS_FOREGROUND':
                raise StageError('CONSTRAINT_CONFLICT','Quantity restriction lacks approved clarification')
        if not set(req.entities)<=set(origin.entities):
            raise StageError('CONSTRAINT_CONFLICT','Visual requirement invents unapproved source entities')
        if req.disposition=='superseded' and not any(r['code']==req.resolution_id and (r.get('authorization') or {}).get('source_sha256')==contract.source_sha256 for r in contract.resolutions):
            raise StageError('CONSTRAINT_CONFLICT','Superseded evidence lacks source-bound authorization')
    receipts=envelope['compilation']['receipts']
    if len(receipts)!=len(ids) or {r['requirement_id'] for r in receipts}!=set(ids):
        raise StageError('CONSTRAINT_CONFLICT','Prompt receipt coverage incomplete')
    by_id={r['requirement_id']:r for r in receipts}
    for req in contract.requirements:
        receipt=by_id[req.requirement_id]
        if receipt['source']!=req.source.model_dump() or receipt['priority']!=req.priority:
            raise StageError('CONSTRAINT_CONFLICT','Receipt provenance differs from requirement')
        if req.disposition=='active' and req.priority in ('required','forbidden'):
            status='emitted_negative' if req.priority=='forbidden' else 'emitted_positive'
            if receipt['status']!=status:
                raise StageError('CONSTRAINT_CONFLICT','Core evidence not compiled to generation request')
        channel=envelope['compilation']['negative' if req.priority=='forbidden' else 'positive']
        if any(fragment not in channel for fragment in receipt.get('fragments',[])):
            raise StageError('CONSTRAINT_CONFLICT','Compiled fragment absent from actual prompt')

def load_contract(inputs,context,brief):
    files=[f for f in inputs.metadata.files if f.media_type==CONTRACT_MEDIA_TYPE]
    if not files:return None
    if len(files)!=1:raise StageError('CONSTRAINT_CONFLICT','Ambiguous visual contracts')
    ref=context.files.resolve_file(files[0])
    if ref.role.value!='layout_planner' or ref.purpose!=ArtifactPurpose.evidence:
        raise StageError('CONSTRAINT_CONFLICT','Contract must be A2 evidence')
    deps={(d.artifact_id,d.revision) for d in inputs.metadata.version_dependencies}
    if (ref.artifact_id,ref.revision) not in deps:
        raise StageError('STALE_VERSION','Missing visual contract dependency')
    try:
        envelope=json.loads(context.files.read(ref));contract=VisualContract.model_validate(envelope['contract'])
        if contract.issues:raise StageError('CONSTRAINT_CONFLICT','Unresolved visual intent')
        if envelope['spec_sha256']!=digest(inputs.content) or envelope['brief_sha256']!=digest(brief):
            raise StageError('STALE_VERSION','Visual contract belongs to another Brief/Spec')
        source=envelope['source_ref']
        matches=[f for f in inputs.metadata.files if f.file_id==source['artifact_id']]
        if len(matches)!=1:raise StageError('MISSING_INPUT','Original A1 source dependency missing')
        original=context.files.resolve_file(matches[0])
        if original.model_dump(mode='json')!=source or original.role.value!='creative_director' or original.purpose!=ArtifactPurpose.evidence or original.sha256!=contract.source_sha256:
            raise StageError('STALE_VERSION','Original A1 source identity changed')
        if (original.artifact_id,original.revision) not in deps:
            raise StageError('STALE_VERSION','Missing original A1 version dependency')
        _verify_requirements(contract,envelope,context.files.read(original))
        for plan in inputs.content['asset_plan']:
            if plan['prompt']!=envelope['compilation']['positive'] or plan['negative_prompt']!=envelope['compilation']['negative']:
                raise StageError('CONSTRAINT_CONFLICT','Spec prompt diverges from A2 contract')
    except StageError:raise
    except (KeyError,IndexError,TypeError,ValueError,AttributeError) as error:
        raise StageError('SCHEMA_INVALID','Malformed visual contract provenance') from error
    return envelope
