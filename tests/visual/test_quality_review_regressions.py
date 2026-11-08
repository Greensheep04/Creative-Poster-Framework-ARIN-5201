"""Final-review reproductions: no GPU or critic calls."""
import asyncio,copy,hashlib,json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from poster_framework import *
from poster_framework.intent_mapping import extract_visual_requirements
from poster_framework.intent_conflicts import resolve_intent
from poster_framework.visual_prompt_compiler import compile_visual_prompts
from poster_framework.visual_requirements import digest,CONTRACT_MEDIA_TYPE
from poster_framework.quality_planner import SOURCE_MEDIA_TYPE
from poster_framework.quality_layout import QualityPlanningPolicy
from poster_framework.layout_planner import plan_poster_spec
from poster_framework.layout_fonts import FontFace,FontRegistry as LF
from poster_visual_producer.a1_adapter import adapt_a1
from poster_visual_producer.contracts import StageError
from tests.layout.test_visual_requirements import raw
from tests.visual.helpers import fonts,png,ROOT
from tests.visual.test_agent import setup,run

def extract(value):
    return extract_visual_requirements(value,source_bytes=json.dumps(value).encode())
def compile(c):return compile_visual_prompts(c,count_tokens=lambda t:[1,1])
def test_review_prohibition_scope_does_not_hide_wall_ban():
    v=raw();v['avoid']=['walls and logos']
    assert any(x['code']=='REQUIRED_OBJECT_FORBIDDEN' for x in resolve_intent(extract(v)).issues)
def test_review_partial_phrase_preserves_clock_and_does_not_invent_bricks():
    v=raw();v['visual_direction']['main_subject']='A half-restored wall with mortar and a prominent clock above it'
    c=compile(resolve_intent(extract(v)))
    assert 'clock' in c['positive'] and 'bricks' not in c['positive']
def test_review_abstract_keyword_evidence_gap_requires_confirmation():
    v=raw();v['keywords']=['migration'];v['concept']['central_idea']='A family is migrating carrying their luggage'
    v['visual_direction']={'main_subject':'A house'}
    assert any(x['status']=='needs_confirmation' for x in extract(v).issues)
def test_review_03_restriction_compiled_explicitly():
    path=ROOT/'examples/creative-director-samples/Qwen3-4B-Instruct-2507/time-consumption-ocean/human-story.json'
    data=path.read_bytes();c=extract_visual_requirements(json.loads(data),source_bytes=data)
    approved={'code':'HELD_PROPS_FOREGROUND','source_sha256':c.source_sha256,'authorization_id':'user-approval'}
    result=compile(resolve_intent(c,[approved]))
    assert 'exactly one plastic bottle' in result['positive'] and 'one broken watch' in result['positive']
    assert 'bottles on ground' in result['negative'] and 'watches on ground' in result['negative']

def layout_inputs():
    from tests.layout.test_quality_layout import inputs
    return inputs()
def test_review_quality_fitter_not_gated_by_legacy_two_lines():
    v,req,registry=layout_inputs();v['copy']['headline']=' '.join(['Window']*7)
    spec,_=QualityPlanningPolicy().plan(v,req,registry,'noto_sans_sc')
    assert spec['elements'][1]['text']==v['copy']['headline']
def test_review_explicit_left_alignment_is_preserved():
    v,req,registry=layout_inputs();spec,_=QualityPlanningPolicy().plan(v,req,registry,'noto_sans_sc')
    assert spec['elements'][1]['align']=='left'

def fake_contract(spec):
    return {'contract':{'source_sha256':'source','requirements':[]},'spec_sha256':digest(spec)}
def authorize(s,first,target,*,plan=None):
    auth=RepairAuthorization(authorization_id='explicit-quality-repair',
        based_on_candidate=VersionDependency(**{k:first['candidate_ref'][k] for k in ('artifact_id','revision')}),
        action=RevisionAction.regenerate_asset,target=target,expected_versions=first['based_on'],
        constraints=('Only the authorized detail',),preserve=('copy','fonts','colors','layout','untargeted_assets'))
    details={'authorization':auth.model_dump(mode='json')}
    if plan is not None:details['repair_plan']=plan
    s.memory.add_revision(role=None,revision_id='authorized',changes={'details':details},unresolved_issues=[])
    return auth
@pytest.mark.parametrize('divergent',[False,True])
def test_review_authorization_is_source_of_repair_constraints(tmp_path,spec_data,brief_data,monkeypatch,divergent):
    a,s,sp,b,p=setup(tmp_path,spec_data,brief_data);first=run(a,p,s,sp,'first').content
    monkeypatch.setattr('poster_visual_producer.agent.load_contract',lambda *args:fake_contract(sp))
    target=VersionDependency(**{k:first['assets'][0]['source_ref'][k] for k in ('artifact_id','revision')})
    auth=authorize(s,first,target,plan={'expected_versions':first['based_on'],'constraints':['Unapproved instruction']} if divergent else None)
    before=s.usage.image_operations
    if divergent:
        with pytest.raises(StageError):run(a,p,s,sp,'repair',RevisionAction.regenerate_asset,target)
        assert s.usage.image_operations==before
    else:
        result=run(a,p,s,sp,'repair',RevisionAction.regenerate_asset,target).content
        assert result['assets'][0]['provenance']['request']['repair_constraints']==list(auth.constraints)

def test_review_unaffected_asset_setting_change_blocks_before_any_call(tmp_path,spec_data,brief_data,monkeypatch):
    accent=copy.deepcopy(spec_data['asset_plan'][0]);accent.update(asset_id='accent_image',target_size={'width':120,'height':20},required_keyword_ids=[],prompt='blue accent')
    spec_data['asset_plan'].append(accent)
    spec_data['elements'].append({'id':'accent','type':'image','z':4,'box':{'x':36,'y':224,'width':120,'height':20},'asset_id':'accent_image','fit':'cover','focus_point_norm':[.5,.5]})
    a,s,sp,b,p=setup(tmp_path,spec_data,brief_data)
    p.generator.capabilities=replace(p.generator.capabilities,supports_seed=True)
    p.seed=42;first=run(a,p,s,sp,'first').content
    monkeypatch.setattr('poster_visual_producer.agent.load_contract',lambda *args:fake_contract(sp))
    target=VersionDependency(**{k:first['assets'][0]['source_ref'][k] for k in ('artifact_id','revision')})
    authorize(s,first,target);p.seed=7;before=s.usage.image_operations
    with pytest.raises(StageError):run(a,p,s,sp,'repair',RevisionAction.regenerate_asset,target)
    assert s.usage.image_operations==before

def test_review_quality_sdxl_requires_final_validator(tmp_path,spec_data,brief_data,monkeypatch):
    a,s,sp,b,p=setup(tmp_path,spec_data,brief_data)
    monkeypatch.setattr('poster_visual_producer.agent.load_contract',lambda *args:fake_contract(sp))
    p.require_visual_contract=True;p.generator.capabilities=replace(p.generator.capabilities,model='stabilityai/stable-diffusion-xl-base-1.0')
    with pytest.raises(StageError,match='validator'):run(a,p,s,sp,'missing-validator')
    assert s.usage.image_operations==0

def test_review_forbidden_requirements_appear_in_pending_checklist():
    from poster_visual_producer.quality_checks import pending_evidence
    c={'contract':{'source_sha256':'x','requirements':[{'requirement_id':'forbidden-logo','priority':'forbidden','disposition':'active'}]},'spec_sha256':'s'}
    checks=pending_evidence(c,{'hero':png()},{'elements':[]})['visual_checks']
    assert checks and checks[0]['requirement_id']=='forbidden-logo'

def test_review_observation_binds_to_poster_spec_and_requirement(spec_data):
    from poster_visual_producer.quality_checks import evaluate_observation
    from poster_visual_producer.imaging import prepare_asset
    from poster_visual_producer.contracts import ImageElement
    data=png();poster=png('green');processing=prepare_asset(data,ImageElement.model_validate(spec_data['elements'][0])).record
    observation={'source_sha256':hashlib.sha256(data).hexdigest(),'poster_sha256':'old-poster',
        'spec_sha256':digest(spec_data),'requirement_id':'r','source_status':'pass','poster_status':'pass','object_boxes_norm':[]}
    with pytest.raises(ValueError,match='poster'):
        evaluate_observation(data=data,poster_data=poster,spec=spec_data,requirement_id='r',processing=processing,observation=observation)

@pytest.mark.parametrize('mutation',['empty','quote','receipts','source_dependency'])
def test_review_contract_verifies_inner_provenance(tmp_path,mutation):
    from poster_visual_producer.contract_io import load_contract
    v=raw();data=json.dumps(v).encode();request=OriginalRequest.from_keywords('review-contract',['repair','community','tomorrow'])
    f=fonts();lf=LF({(k,w):FontFace(k,w,x.path,x.index,'test') for k,weights in f.families.items() for w,x in weights.items()})
    session=RunSession(request,root=tmp_path)
    a1=ExecutionIdentity(run_id=request.run_id,role=AgentRole.creative_director,invocation_id='a1',execution_mode=ExecutionMode.real)
    src=session.create_context(a1).files.write_bytes('original.json',data,purpose=ArtifactPurpose.evidence,media_type=SOURCE_MEDIA_TYPE)
    # Use a full original remote source for the strict adapter.
    source=ROOT/'examples/creative-director-samples/Qwen3-4B-Instruct-2507/repair-community-tomorrow/human-story.json'
    data=source.read_bytes();v=json.loads(data)
    src=session.create_context(a1).files.write_bytes('remote.json',data,purpose=ArtifactPurpose.evidence,media_type=SOURCE_MEDIA_TYPE)
    brief=adapt_a1(data,request,'source')
    c=resolve_intent(extract_visual_requirements(v,source_bytes=data));compiled=compile(c)
    spec=plan_poster_spec(v,request=request,registry=lf,font_family_key='noto_sans_sc',template_id='split_stack_v1')
    spec['asset_plan'][0].update(prompt=compiled['positive'],negative_prompt=compiled['negative'])
    envelope={'contract':c.model_dump(mode='json'),'compilation':compiled,'spec_sha256':digest(spec),'brief_sha256':digest(brief),'source_ref':src.model_dump(mode='json')}
    if mutation=='empty':envelope['contract']['requirements']=[]
    if mutation=='quote':envelope['contract']['requirements'][0]['source']['quote']='Invented false quote'
    if mutation=='receipts':envelope['compilation']['receipts']=[]
    a2=ExecutionIdentity(run_id=request.run_id,role=AgentRole.layout_planner,invocation_id='a2',execution_mode=ExecutionMode.real)
    ref=session.create_context(a2).files.write_bytes('contract.json',json.dumps(envelope).encode(),purpose=ArtifactPurpose.evidence,media_type=CONTRACT_MEDIA_TYPE)
    identity=ExecutionIdentity(run_id=request.run_id,role=AgentRole.visual_producer,invocation_id='a3',execution_mode=ExecutionMode.real)
    def file(r):return FileReference(file_id=r.artifact_id,**{k:getattr(r,k) for k in ('run_id','uri','revision','sha256','media_type')})
    deps=[ref] if mutation=='source_dependency' else [src,ref]
    meta=FrameworkMetadata(identity=identity,original_request=request,files=(file(src),file(ref)),version_dependencies=tuple(VersionDependency(artifact_id=r.artifact_id,revision=r.revision) for r in deps))
    context=session.create_context(identity,meta);inputs=AgentInput(content=spec,content_format=ContentFormat(name='poster.spec',version='0.1.0'),metadata=meta)
    with pytest.raises(StageError):load_contract(inputs,context,brief)

def test_review_budget_scope_is_authorization_not_output_directory(tmp_path,spec_data):
    from poster_visual_producer.image_budget import authorization_ledger
    from poster_visual_producer.generators import FixtureImageGenerator,compile_request
    auth={'authorization_id':'approved-first-round','round':1,'limit':6,'sources':{str(i):str(i) for i in range(6)}}
    first=authorization_ledger(tmp_path,auth);second=authorization_ledger(tmp_path,dict(auth))
    generator=FixtureImageGenerator(lambda r:png());req=compile_request(spec_data['asset_plan'][0],generator.capabilities)
    for i in range(2):first.reserve(str(i),req,(),source_sha256=str(i),run_dir=str(tmp_path/'first'))
    for i in range(2,6):second.reserve(str(i),req,(),source_sha256=str(i),run_dir=str(tmp_path/'second'))
    with pytest.raises(ValueError):first.reserve('0',req,(),source_sha256='0',run_dir=str(tmp_path/'third'))
    assert len(json.loads(first.path.read_text())['attempts'])==6

def test_review_comparison_failure_does_not_hide_formal_poster(tmp_path):
    from examples.visual.quality_regression import comparison_for_case
    from PIL import ImageFont
    poster=tmp_path/'poster.png';poster.write_bytes(png())
    row={'status':'rendered','source':'missing-source','poster':str(poster)}
    result=comparison_for_case(row,[],tmp_path,ImageFont.load_default())
    assert row['status']=='rendered' and result['status']=='unavailable'
