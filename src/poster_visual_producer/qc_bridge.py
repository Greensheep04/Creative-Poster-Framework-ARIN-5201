"""Version-bound QC handoff using current shared artifacts and a trusted A4 callback."""
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4, uuid5, NAMESPACE_URL
import copy, hashlib, io, json
from PIL import Image
from poster_framework import (AgentOutput, AgentRegistration, AgentRole, ArtifactReference,
    ArtifactPurpose, ContentFormat, ReviewRecord, ReviewDecision, KeywordReviewResult, KeywordReviewStatus)
from .contracts import CreativeBrief, PosterSpec, TextElement, ImageElement, validate_pair, StageError
from .registration import CANDIDATE_FORMAT
from .qc_contracts import LocalManifestV1, AuditReportV3, required_copies

QC_CONTRACT='local-manifest-v1/audit-report-v3'
QC_FORMAT=ContentFormat(name='poster.audit-report',version='3')

@dataclass(frozen=True)
class ReviewBundle:
    manifest_path: Path
    candidate_link_path: Path
    manifest: LocalManifestV1
    candidate_link: dict

@dataclass(frozen=True)
class ValidatedAudit:
    decision: str
    production_pass: bool
    raw_report: dict
    feedback: dict


def build_local_manifest(request,brief,spec,candidate,*,sample_id,image_path):
    validate_pair(spec,brief,[k.keyword_id for k in request.keywords])
    if candidate['run_id']!=request.run_id or candidate['based_on']!={'brief_version':brief.brief_version,'spec_version':spec.spec_version}:
        raise StageError('STALE_VERSION','Candidate does not use current Brief/Spec')
    return LocalManifestV1.model_validate({
        'sample_id':sample_id,'keywords':[{'keyword_id':k.keyword_id,'text':k.text} for k in request.keywords],
        'user_constraints':[{'constraint_id':'constraint-'+str(uuid5(NAMESPACE_URL,request.request_id+':'+str(i))), 'name':c.name,'value':c.value} for i,c in enumerate(request.constraints)],
        'image':{'path':image_path},
        'approved_copy':[{'copy_id':key,'text':c.text,'required':c.must_preserve,'match_policy':'exact'} for key,c in brief.copy_blocks.items()],
        'layout_context':{'elements':[{'element_id':e.id,'bbox_xyxy':[e.box.x,e.box.y,e.box.x+e.box.width,e.box.y+e.box.height],'copy_id':e.copy_ref if isinstance(e,TextElement) else None} for e in spec.elements]},
        'asset_context':{'assets':[{'asset_key':p.asset_id,'element_ids':[e.id for e in spec.elements if isinstance(e,ImageElement) and e.asset_id==p.asset_id], 'description':'Design intent: '+p.prompt} for p in spec.asset_plan]},
        'design_context':str(brief.intent.get('concept','')) or None,
        'render_context':{'messages':list(candidate.get('warnings',[]))},
    })


def _json(data):
    return json.loads(data.decode('utf-8-sig'))


def _current_inputs(context,candidate):
    items={i['name']:i for i in context.memory.context()['artifacts']}
    if 'brief' not in items or 'layout' not in items:
        raise StageError('MISSING_INPUT','QC requires current Brief and layout')
    for name in ('brief','layout'):
        context.files.read(ArtifactReference.model_validate(items[name]['reference']))
    if 'candidate' not in items or items['candidate']['reference']!=candidate['candidate_ref']:
        raise StageError('STALE_VERSION','QC candidate is not currently published')
    return items,CreativeBrief.model_validate(items['brief']['content']),PosterSpec.model_validate(items['layout']['content'])


def _link(candidate,dependencies):
    keys=('run_id','candidate_id','candidate_ref','manifest_ref','based_on')
    result={key:copy.deepcopy(candidate[key]) for key in keys}
    result['framework_dependencies']=copy.deepcopy(dependencies)
    return result


def export_review_bundle(context,candidate,*,destination):
    items,brief,spec=_current_inputs(context,candidate)
    poster_ref=ArtifactReference.model_validate(candidate['candidate_ref'])
    image=context.files.read(poster_ref)
    if len(image)>20*1024*1024:
        raise StageError('INVALID_ASSET','QC image exceeds 20 MiB')
    with Image.open(io.BytesIO(image)) as im:
        if im.format not in ('PNG','JPEG') or im.size!=(720,1280) or getattr(im,'n_frames',1)!=1:
            raise StageError('INVALID_ASSET','QC requires a single 720x1280 PNG/JPEG')
        im.verify()
    # A3 publishes PNG; avoid disguising a JPEG as poster.png.
    if poster_ref.media_type!='image/png':
        raise StageError('INVALID_ASSET','Review bundle requires the A3 PNG candidate')
    manifest_data=context.files.read(ArtifactReference.model_validate(candidate['manifest_ref']))
    render=_json(manifest_data)
    deps=[{'artifact_id':items[n]['reference']['artifact_id'],'revision':items[n]['reference']['revision']} for n in ('brief','layout')]
    if {tuple(sorted(d.items())) for d in render['framework_dependencies']}!={tuple(sorted(d.items())) for d in deps}:
        raise StageError('STALE_VERSION','Render evidence uses stale dependencies')
    if render['output_sha256']!=poster_ref.sha256:
        raise StageError('STALE_VERSION','Render evidence does not identify candidate bytes')
    if not candidate.get('index_ref'):
        raise StageError('MISSING_INPUT','Candidate needs issued index_ref; legacy producers require an explicit adapter')
    index_data=context.files.read(ArtifactReference.model_validate(candidate['index_ref']))
    index=_json(index_data)
    for row in index['artifacts']:
        context.files.read(ArtifactReference.model_validate(row))
    if candidate['candidate_ref'] not in index['artifacts'] or candidate['manifest_ref'] not in index['artifacts']:
        raise StageError('STALE_VERSION','Artifact index omits candidate or render manifest')
    destination=Path(destination)
    if destination.exists() and (destination.is_symlink() or any(destination.iterdir())):
        raise ValueError('Review destination must be a new or empty trusted directory')
    m=build_local_manifest(context.original_request,brief,spec,candidate,sample_id='sample-'+uuid4().hex,image_path='poster.png')
    link=_link(candidate,deps)
    destination.mkdir(parents=True,exist_ok=True)
    (destination/'evidence').mkdir(exist_ok=True)
    def save(path,value):
        path.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8')
    save(destination/'manifest.json',m.model_dump(mode='json'))
    save(destination/'candidate_link.json',link)
    save(destination/'evidence/candidate.json',candidate)
    (destination/'poster.png').write_bytes(image)
    (destination/'evidence/render_manifest.json').write_bytes(manifest_data)
    (destination/'evidence/artifact_index.json').write_bytes(index_data)
    return ReviewBundle(destination/'manifest.json',destination/'candidate_link.json',m,link)


def validate_audit_report(report,bundle,current_candidate):
    r=AuditReportV3.model_validate(report)
    link=bundle.candidate_link
    for key in ('run_id','candidate_id','candidate_ref','manifest_ref','based_on'):
        if current_candidate.get(key)!=link[key]:
            raise StageError('STALE_VERSION','Audit belongs to an old candidate: '+key)
    image=(bundle.manifest_path.parent/bundle.manifest.image.path).read_bytes()
    if r.sample_id!=bundle.manifest.sample_id or r.image_sha256!=link['candidate_ref']['sha256'] or hashlib.sha256(image).hexdigest()!=r.image_sha256:
        raise StageError('STALE_VERSION','Audit sample/image identity mismatch')
    m=bundle.manifest
    required=set(m.required_checks)
    copies=required_copies(m)
    coverage={c.check_id:c for c in r.coverage}
    kw={c.keyword_id:c for c in r.keyword_checks}; constraints={c.constraint_id:c for c in r.constraint_checks}; text={c.copy_id:c for c in r.text_checks}
    reasons=[]
    if r.execution_mode!='real': reasons.append('Fake/non-production audit cannot release a candidate')
    if r.decision!='pass': reasons.append('A4 decision is '+r.decision)
    if set(kw)!={k.keyword_id for k in m.keywords} or any(c.status!='pass' for c in kw.values()): reasons.append('Keyword checks are incomplete or unsuccessful')
    if set(constraints)!={c.constraint_id for c in m.user_constraints} or any(c.status!='pass' for c in constraints.values()): reasons.append('Constraint checks are incomplete or unsuccessful')
    if any(key not in text or text[key].status!='pass' or text[key].readability!='pass' or text[key].observed_text is None for key in copies): reasons.append('Required copy lacks readable successful observation')
    if any(not coverage[key].required or coverage[key].state!='assessed' or coverage[key].status!='pass' for key in required): reasons.append('Required coverage is incomplete or unsuccessful')
    if any(i.severity in ('blocking','major') for i in r.issues): reasons.append('Audit contains blocking/major issues')
    if r.uncertainties: reasons.append('Audit has unresolved uncertainty')
    # The independent A4 owns match_policy interpretation. Its status remains authoritative.
    raw=copy.deepcopy(report)
    feedback={'decision':r.decision,'release_blockers':reasons,'report':raw,'candidate_link':copy.deepcopy(link)}
    return ValidatedAudit(r.decision,not reasons,raw,feedback)


class QCBridgeAgent:
    def __init__(self,audit,bundle_root):
        self.audit=audit; self.bundle_root=Path(bundle_root)
    async def run(self,inputs,context):
        candidate=inputs.content
        bundle=export_review_bundle(context,candidate,destination=self.bundle_root/context.original_request.run_id/context.identity.invocation_id)
        try:
            report=await self.audit(bundle.manifest_path,context)
            items,brief,spec=_current_inputs(context,candidate)
            current_deps=[{'artifact_id':items[n]['reference']['artifact_id'],'revision':items[n]['reference']['revision']} for n in ('brief','layout')]
            if current_deps!=bundle.candidate_link['framework_dependencies']:
                raise StageError('STALE_VERSION','QC dependencies changed during audit')
            validate_pair(spec,brief,[k.keyword_id for k in context.original_request.keywords])
            if candidate['based_on']!={'brief_version':brief.brief_version,'spec_version':spec.spec_version}:
                raise StageError('STALE_VERSION','QC business versions changed during audit')
            result=validate_audit_report(report,bundle,candidate)
        except Exception as error:
            context.files.write_bytes(context.files.invocation_path('qc-error.json'),json.dumps({'error_type':type(error).__name__,'sample_id':bundle.manifest.sample_id}).encode(),purpose=ArtifactPurpose.evidence,media_type='application/json')
            raise
        details={'qc_contract':QC_CONTRACT,'report':result.raw_report,'candidate_link':bundle.candidate_link,'production_pass':result.production_pass,'feedback':result.feedback}
        ref=context.files.write_bytes(context.files.invocation_path('audit-report.json'),json.dumps(report,ensure_ascii=False,allow_nan=False).encode('utf-8'),purpose=ArtifactPurpose.evidence,media_type='application/json')
        review=ReviewRecord(candidate_id=candidate['candidate_id'],candidate_revision=candidate['candidate_ref']['revision'],decision=ReviewDecision(result.decision),keyword_results=tuple(KeywordReviewResult(keyword_id=k['keyword_id'],status=KeywordReviewStatus(k['status'])) for k in report['keyword_checks']),details=details)
        return AgentOutput(content=report,content_format=QC_FORMAT,artifacts=(ref,),review=review)


def qc_registration(audit,*,bundle_root):
    return AgentRegistration(role=AgentRole.quality_critic,instance_factory=lambda config:QCBridgeAgent(audit,bundle_root),input_formats=frozenset({CANDIDATE_FORMAT}),output_formats=frozenset({QC_FORMAT}))
