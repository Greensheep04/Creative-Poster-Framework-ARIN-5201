import asyncio, copy, json, shutil
from pathlib import Path
import pytest
from PIL import Image
from poster_framework import AgentRole, ArtifactReference, ExecutionIdentity, ExecutionMode
from poster_visual_producer.agent import VisualProducerAgent
from poster_visual_producer.generators import FixtureImageGenerator
from tests.visual.helpers import make_session, call_context, fonts, png


def produced(tmp_path, spec_data, brief_data):
    session, spec, brief = make_session(tmp_path, spec_data, brief_data)
    inputs, ctx = call_context(session, spec, 'produce')
    out = asyncio.run(VisualProducerAgent(FixtureImageGenerator(lambda r: png()), fonts()).run(inputs, ctx))
    session.memory.publish_artifact('candidate', ArtifactReference.model_validate(out.content['candidate_ref']), out.content, out.content_format, publishing_identity=ctx.identity)
    quality = session.create_context(ExecutionIdentity(run_id=session.run_id, role=AgentRole.quality_critic, invocation_id='qc', execution_mode=ExecutionMode.real))
    return session, out.content, spec, brief, quality


def report_for(bundle, *, decision='pass', mode='real'):
    m = bundle.manifest.model_dump(mode='json')
    copies = [c for c in (m.get('approved_copy') or []) if c['required']]
    for c in m['user_constraints']:
        if c['name'] == 'required_text':
            copies.append({'copy_id':'constraint:'+c['constraint_id'], 'text':c['value']})
    checks = ['keyword_alignment','user_constraints','text_readability','layout_quality','text_accuracy','layout_geometry']
    required = set(m['required_checks'])
    if copies:
        required.add('text_accuracy')
    return {'schema_version':3,'sample_id':m['sample_id'],'image_sha256':bundle.candidate_link['candidate_ref']['sha256'],
        'decision':decision, 'keyword_checks':[{'keyword_id':k['keyword_id'],'status':'pass','evidence':'fixed test observation'} for k in m['keywords']],
        'constraint_checks':[{'constraint_id':c['constraint_id'],'status':'pass','evidence':'fixed test observation'} for c in m['user_constraints']],
        'text_checks':[{'copy_id':c['copy_id'],'observed_text':c['text'],'status':'pass','readability':'pass'} for c in copies],
        'coverage':[{'check_id':c,'required':c in required,'state':'assessed' if c in required else 'not_assessed','status':'pass' if c in required else None,'reason':'test fixture'} for c in checks],
        'issues':[], 'uncertainties':['title needs verification'] if decision=='unknown' else [],'repair_hint':None,
        'model':'test-observation','prompt_version':'test-v1','execution_mode':mode,'calls':1,'vision_calls':1,'extra_record':{'retained':True}}


def test_bundle_uses_original_copy_ids_and_relative_image(tmp_path, spec_data, brief_data):
    from poster_visual_producer.qc_bridge import export_review_bundle
    _, candidate, _, _, quality = produced(tmp_path/'store', spec_data, brief_data)
    bundle = export_review_bundle(quality, candidate, destination=tmp_path/'bundle')
    m = json.loads(bundle.manifest_path.read_text(encoding='utf-8'))
    assert m['image'] == {'path':'poster.png'}
    assert m['layout_context']['elements'][1] == {'element_id':'title','bbox_xyxy':[36,64,684,216],'copy_id':'title'}
    assert m['asset_context']['assets'][0]['element_ids'] == ['hero']
    assert m['asset_context']['assets'][0]['asset_key'] == 'hero_image'
    assert m['approved_copy'][0]['text'] == candidate['text_manifest'][0]['text']
    assert 'run_id' not in m and 'candidate_id' not in m
    assert (tmp_path/'bundle/evidence/artifact_index.json').is_file()
    shutil.copytree(tmp_path/'bundle', tmp_path/'moved')
    assert Image.open(tmp_path/'moved'/m['image']['path']).size == (720,1280)


@pytest.mark.parametrize('change', ['extra','empty_layout','missing_base_check','duplicate_keyword'])
def test_local_manifest_rejects_invalid_contract(tmp_path, spec_data, brief_data, change):
    from poster_visual_producer.qc_bridge import export_review_bundle
    from poster_visual_producer.qc_contracts import LocalManifestV1
    _, c, _, _, ctx = produced(tmp_path/'store', spec_data, brief_data)
    m = export_review_bundle(ctx,c,destination=tmp_path/'bundle').manifest.model_dump(mode='json')
    if change=='extra': m['candidate_id']='wrong'
    if change=='empty_layout': m['layout_context']={}
    if change=='missing_base_check': m['required_checks']=['text_accuracy']
    if change=='duplicate_keyword': m['keywords'][1]=m['keywords'][0]
    with pytest.raises(ValueError): LocalManifestV1.model_validate(m)


def test_manifest_preserves_outside_box_and_constraint_json(tmp_path, spec_data, brief_data):
    from poster_visual_producer.qc_bridge import export_review_bundle
    from poster_visual_producer.qc_contracts import LocalManifestV1
    _, c, _, _, ctx = produced(tmp_path/'store', spec_data, brief_data)
    m = export_review_bundle(ctx,c,destination=tmp_path/'bundle').manifest.model_dump(mode='json')
    m['layout_context']['elements'][0]['bbox_xyxy']=[-5,0,721,1300]
    m['user_constraints']=[{'constraint_id':'c1','name':'palette','value':{'colors':['blue'],'strict':True}}, {'constraint_id':'c2','name':'required_text','value':'EXACT'}]
    result = LocalManifestV1.model_validate(m)
    assert list(result.layout_context.elements[0].bbox_xyxy)==[-5,0,721,1300]
    assert result.user_constraints[0].value=={'colors':['blue'],'strict':True}
    assert 'text_accuracy' in result.required_checks


@pytest.mark.parametrize('change',['sample','hash','revision','based_on','manifest_ref'])
def test_old_report_rejected_even_with_same_pixels(tmp_path,spec_data,brief_data,change):
    from poster_visual_producer.qc_bridge import export_review_bundle, validate_audit_report
    _, c, _, _, ctx = produced(tmp_path/'store',spec_data,brief_data)
    bundle=export_review_bundle(ctx,c,destination=tmp_path/'bundle')
    report=report_for(bundle); current=copy.deepcopy(c)
    if change=='sample': report['sample_id']='old'
    if change=='hash': report['image_sha256']='0'*64
    if change=='revision': current['candidate_ref']['revision']='new-revision'
    if change=='based_on': current['based_on']['spec_version']=2
    if change=='manifest_ref': current['manifest_ref']['revision']='old'
    with pytest.raises(ValueError): validate_audit_report(report,bundle,current)


@pytest.mark.parametrize('change',['fake','coverage','issue','keyword','copy','constraint'])
def test_fake_or_incomplete_pass_cannot_release(tmp_path,spec_data,brief_data,change):
    from poster_visual_producer.qc_bridge import export_review_bundle,validate_audit_report
    _,c,_,_,ctx=produced(tmp_path/'store',spec_data,brief_data)
    bundle=export_review_bundle(ctx,c,destination=tmp_path/'bundle'); report=report_for(bundle)
    if change=='fake': report['execution_mode']='fake'
    if change=='coverage': report['coverage'][0].update(state='unknown',status='unknown')
    if change=='issue': report['issues']=[{'code':'bad','severity':'major','element_id':None,'bbox_xyxy':None,'source':'model','evidence':'fixed evidence','suggestion':'verify'}]
    if change=='keyword': report['keyword_checks']=[]
    if change=='copy': report['text_checks']=[]
    if change=='constraint': report['constraint_checks']=[{'constraint_id':'unexpected','status':'pass','evidence':'test'}]
    result=validate_audit_report(report,bundle,c)
    assert not result.production_pass
    assert result.raw_report['extra_record']=={'retained':True}


def test_complete_report_releases_but_core_excerpt_is_rejected(tmp_path,spec_data,brief_data):
    from poster_visual_producer.qc_bridge import export_review_bundle,validate_audit_report
    _,c,_,_,ctx=produced(tmp_path/'store',spec_data,brief_data)
    bundle=export_review_bundle(ctx,c,destination=tmp_path/'bundle')
    assert validate_audit_report(report_for(bundle),bundle,c).production_pass
    with pytest.raises(ValueError): validate_audit_report({'schema_version':3,'decision':'pass'},bundle,c)
