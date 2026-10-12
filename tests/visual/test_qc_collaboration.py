import asyncio, copy, json
from types import SimpleNamespace
from pathlib import Path
import pytest
from poster_framework import AgentRegistration,AgentRole,AgentOutput,AgentSet,PosterWorkflow,WORKFLOW_REQUEST_FORMAT,RevisionAction,VersionDependency
from poster_visual_producer.registration import BRIEF_FORMAT,SPEC_FORMAT,registration,project_budget
from poster_visual_producer.generators import FixtureImageGenerator
from tests.visual.helpers import fonts,png
from tests.visual.test_qc_bridge import report_for
from examples.visual.workflow_demo import DemoCreativeDirector,map_keywords


def workflow(tmp_path,spec_data,*,authorizer=None,decision='unknown',mode='fake',two_assets=False,repeat=False):
    from poster_visual_producer.qc_bridge import qc_registration
    from poster_visual_producer.qc_contracts import LocalManifestV1
    sp=copy.deepcopy(spec_data)
    if two_assets:
        plan=copy.deepcopy(sp['asset_plan'][0]);plan.update(asset_id='accent_image',prompt='blue accent image',target_size={'width':120,'height':20},required_keyword_ids=[])
        sp['asset_plan'].append(plan)
        sp['elements'].append({'id':'accent','type':'image','z':4,'box':{'x':36,'y':224,'width':120,'height':20},'asset_id':'accent_image','fit':'cover','focus_point_norm':[0.5,0.5]})
    class Layout:
        async def run(self,inputs,context):
            return AgentOutput(content=map_keywords(copy.deepcopy(sp),context.original_request),content_format=SPEC_FORMAT)
    observations=[];candidates=[]
    async def audit(path,context):
        path=Path(path)
        m=LocalManifestV1.model_validate_json(path.read_text(encoding='utf-8'))
        link=json.loads((path.parent/'candidate_link.json').read_text(encoding='utf-8'))
        c=json.loads((path.parent/'evidence/candidate.json').read_text(encoding='utf-8'))
        candidates.append(c)
        r=report_for(SimpleNamespace(manifest=m,candidate_link=link),decision=decision if not observations or repeat else 'unknown',mode=mode)
        if not observations or repeat:
            r['repair_hint']={'stage':'asset','element_id':'hero','asset_key':'hero_image','reason':'fixed test defect','preserve':['title','accent']}
        else: r['uncertainties']=['final engineering observation awaits real A4']
        observations.append(r)
        return r
    count=[0]
    def source(req):
        count[0]+=1
        return png('blue' if count[0]<=2 else 'green')
    agents=AgentSet([
        AgentRegistration(role=AgentRole.creative_director,instance_factory=lambda c:DemoCreativeDirector(),input_formats=frozenset({WORKFLOW_REQUEST_FORMAT}),output_formats=frozenset({BRIEF_FORMAT})),
        AgentRegistration(role=AgentRole.layout_planner,instance_factory=lambda c:Layout(),input_formats=frozenset({BRIEF_FORMAT}),output_formats=frozenset({SPEC_FORMAT})),
        registration(FixtureImageGenerator(source),fonts()),qc_registration(audit,bundle_root=tmp_path/'bundles')])
    result=asyncio.run(PosterWorkflow(agents,root=tmp_path/'store',budget=project_budget(),repair_authorizer=authorizer).run(['海洋','宁静','蓝色']))
    return result,candidates,observations


def test_qc_unknown_without_authorization_never_generates_again(tmp_path,spec_data):
    result,candidates,reports=workflow(tmp_path,spec_data)
    assert result.status.value=='needs_review' and result.usage.image_operations==1
    assert len(candidates)==1
    assert result.call_sequence.count(AgentRole.creative_director)==1
    assert result.call_sequence.count(AgentRole.layout_planner)==1
    assert reports[0]['uncertainties']
    assert any(r.uri.endswith('review-feedback.json') for r in result.artifacts)


@pytest.mark.parametrize('decision',['fail','unknown'])
def test_external_authorization_regenerates_only_target_and_keeps_report(tmp_path,spec_data,decision):
    from poster_framework import RepairAuthorization
    invoked=[]
    async def authorize(feedback,context):
        invoked.append(feedback)
        if len(invoked)>1:return None
        c=feedback['current_candidate'];ref=c['assets'][0]['source_ref']
        return RepairAuthorization(authorization_id='approved-1',based_on_candidate=VersionDependency(artifact_id=c['candidate_id'],revision=c['candidate_ref']['revision']),action=RevisionAction.regenerate_asset,target=VersionDependency(artifact_id=ref['artifact_id'],revision=ref['revision']),expected_versions=c['based_on'],constraints=['repair only the hero visual'],preserve=['title','subtitle','accent'])
    result,candidates,reports=workflow(tmp_path,spec_data,authorizer=authorize,decision=decision,two_assets=True)
    assert len(candidates)==2 and result.usage.image_operations==3
    assert candidates[0]['text_manifest']==candidates[1]['text_manifest']
    assert candidates[0]['assets'][1]['source_ref']==candidates[1]['assets'][1]['source_ref']
    assert candidates[0]['assets'][0]['source_ref']['sha256']!=candidates[1]['assets'][0]['source_ref']['sha256']
    assert invoked[0]['report']['decision']==decision
    assert result.call_sequence.count(AgentRole.creative_director)==1 and result.call_sequence.count(AgentRole.layout_planner)==1
    assert any(r.uri.endswith('repair-authorization.json') for r in result.artifacts)


def test_stale_authorization_rejected_before_image_call(tmp_path,spec_data):
    from poster_framework import RepairAuthorization
    async def stale(feedback,context):
        c=feedback['current_candidate'];ref=c['assets'][0]['source_ref']
        return RepairAuthorization(authorization_id='stale',based_on_candidate=VersionDependency(artifact_id=c['candidate_id'],revision='old'),action=RevisionAction.regenerate_asset,target=VersionDependency(artifact_id=ref['artifact_id'],revision=ref['revision']),expected_versions=c['based_on'])
    with pytest.raises(ValueError,match='[Ss]tale|[Cc]andidate'):
        workflow(tmp_path,spec_data,authorizer=stale,decision='fail')
    record=json.loads(next((tmp_path/'store/runs').glob('*/run-record.json')).read_text(encoding='utf-8'))
    assert record['usage']['image_operations']==1


def test_fake_qc_pass_stops_before_release(tmp_path,spec_data):
    result,candidates,reports=workflow(tmp_path,spec_data,decision='pass')
    assert result.status.value=='needs_review'
    assert len(candidates)==1 and reports[0]['decision']=='pass'


def test_rerender_authorization_adds_no_image_request(tmp_path,spec_data):
    from poster_framework import RepairAuthorization
    calls=[0]
    async def authorize(feedback,context):
        calls[0]+=1
        if calls[0]>1:return None
        c=feedback['current_candidate'];ref=c['candidate_ref']
        return RepairAuthorization(authorization_id='render-1',based_on_candidate=VersionDependency(artifact_id=ref['artifact_id'],revision=ref['revision']),action=RevisionAction.rerender,target=VersionDependency(artifact_id=ref['artifact_id'],revision=ref['revision']),expected_versions=c['based_on'])
    result,candidates,_=workflow(tmp_path,spec_data,authorizer=authorize,decision='fail')
    assert len(candidates)==2 and result.usage.image_operations==1
    assert candidates[0]['candidate_id']!=candidates[1]['candidate_id']


def test_same_qc_issue_stops_after_one_authorized_attempt(tmp_path,spec_data):
    from poster_framework import RepairAuthorization
    async def authorize(feedback,context):
        c=feedback['current_candidate'];ref=c['candidate_ref']
        return RepairAuthorization(authorization_id='a-'+c['candidate_id'],based_on_candidate=VersionDependency(artifact_id=ref['artifact_id'],revision=ref['revision']),action=RevisionAction.rerender,target=VersionDependency(artifact_id=ref['artifact_id'],revision=ref['revision']),expected_versions=c['based_on'])
    result,candidates,_=workflow(tmp_path,spec_data,authorizer=authorize,decision='fail',repeat=True)
    assert len(candidates)==2
    assert 'same review issue' in result.reason
