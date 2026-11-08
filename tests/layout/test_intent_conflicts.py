import hashlib,json
import pytest
from poster_framework.intent_mapping import extract_visual_requirements
from poster_framework.intent_conflicts import resolve_intent
from poster_framework.visual_prompt_compiler import compile_visual_prompts
from poster_framework.visual_requirements import IntentError
from tests.layout.test_visual_requirements import raw

def contract(value):
    data=json.dumps(value).encode();return extract_visual_requirements(value,source_bytes=data)
def approve(c,code):
    return {'source_sha256':c.source_sha256,'code':code,'authorization_id':'user-phase2-20261108'}
def test_held_ground_conflict_blocks_then_separate_approval_resolves():
    v=raw();v['visual_direction']['main_subject']='A lone figure hand holding a plastic bottle and broken watch'
    v['visual_direction']['supporting_elements']=['A bottle half-buried in sand','A watch lying on the sand']
    c=contract(v)
    with pytest.raises(IntentError,match='OBJECT_LOCATION_CONFLICT'):
        compile_visual_prompts(resolve_intent(c),count_tokens=lambda s:[1,1])
    resolved=resolve_intent(c,[approve(c,'HELD_PROPS_FOREGROUND')])
    out=compile_visual_prompts(resolved,count_tokens=lambda s:[1,1])
    assert 'foreground close' in out['positive'] and 'buried' not in out['positive']
    assert len([r for r in resolved.requirements if r.disposition=='superseded'])==2
    assert c.source_sha256==resolved.source_sha256
def test_real_objects_are_distinct_from_icons():
    v=raw();v['visual_direction']['supporting_elements']=['Icons of shopping bags, plastic bottles and electronic devices']
    v['avoid']=['Icons of individual products or brands']
    c=contract(v)
    assert resolve_intent(c).issues[0]['code']=='REQUIRED_REPRESENTATION_FORBIDDEN'
    c=resolve_intent(c,[approve(c,'REAL_UNBRANDED_WASTE')])
    assert not c.issues
    out=compile_visual_prompts(c,count_tokens=lambda s:[1,1])
    assert 'real unbranded shopping bags' in out['positive']
    assert 'Icons' in out['negative']
def test_no_permission_by_stale_hash():
    c=contract(raw());a=approve(c,'REAL_UNBRANDED_WASTE');a['source_sha256']='wrong'
    assert resolve_intent(c,[a]).issues
def test_direct_ban_never_overridden():
    v=raw();v['avoid']=['brick walls'];c=resolve_intent(contract(v))
    assert any(x['code']=='REQUIRED_OBJECT_FORBIDDEN' for x in c.issues)
def test_mosaic_density_keeps_core_evidence():
    v=raw();v['visual_direction']['main_subject']='A fragmented mosaic of chair and photographs spiraling into doorway'
    v['avoid']=['crowded or chaotic arrangements'];c=resolve_intent(contract(v))
    assert not c.issues
    assert any(x['code']=='DENSITY_CONTROL' for x in c.resolutions)
    assert {'chair','photographs','doorway'}<=set(c.requirements[2].entities)
