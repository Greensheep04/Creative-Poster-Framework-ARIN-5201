"""Verify A2 sidecar against current registered A1 bytes and approved Spec."""
import json
from poster_framework.visual_requirements import CONTRACT_MEDIA_TYPE,VisualContract,digest
from .contracts import StageError

def load_contract(inputs,context,brief):
    files=[f for f in inputs.metadata.files if f.media_type==CONTRACT_MEDIA_TYPE]
    if not files:return None
    if len(files)!=1:raise StageError('CONSTRAINT_CONFLICT','Ambiguous visual contracts')
    ref=context.files.resolve_file(files[0])
    if ref.role.value!='layout_planner':raise StageError('CONSTRAINT_CONFLICT','Contract must be authored by A2')
    if (ref.artifact_id,ref.revision) not in {(d.artifact_id,d.revision) for d in inputs.metadata.version_dependencies}:
        raise StageError('STALE_VERSION','Missing visual contract dependency')
    envelope=json.loads(context.files.read(ref));contract=VisualContract.model_validate(envelope['contract'])
    if contract.issues:raise StageError('CONSTRAINT_CONFLICT','Unresolved visual intent')
    if envelope['spec_sha256']!=digest(inputs.content) or envelope['brief_sha256']!=digest(brief):
        raise StageError('STALE_VERSION','Visual contract belongs to another Brief/Spec')
    source=envelope['source_ref']
    matches=[f for f in inputs.metadata.files if f.file_id==source['artifact_id']]
    if len(matches)!=1:raise StageError('MISSING_INPUT','Original A1 source dependency missing')
    original=context.files.resolve_file(matches[0])
    if original.model_dump(mode='json')!=source or original.role.value!='creative_director' or original.sha256!=contract.source_sha256:
        raise StageError('STALE_VERSION','Original A1 source identity changed')
    for plan in inputs.content['asset_plan']:
        if plan['prompt']!=envelope['compilation']['positive'] or plan['negative_prompt']!=envelope['compilation']['negative']:
            raise StageError('CONSTRAINT_CONFLICT','Spec prompt diverges from A2 contract')
    return envelope
