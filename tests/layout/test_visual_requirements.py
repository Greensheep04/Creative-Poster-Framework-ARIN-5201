import hashlib,json
import pytest
from poster_framework.intent_mapping import extract_visual_requirements
from poster_framework.visual_prompt_compiler import compile_visual_prompts
from poster_framework.visual_requirements import IntentError

def raw():
 return {"core_message":"Community repairs tomorrow", "concept":{"central_idea":"Shared work"},"visual_direction":{"main_subject":"A half-restored brick wall with fresh mortar", "visual_relationship":"Tools laid nearby, hands and feet in foreground suggesting action", "supporting_elements":["A hammer"],"style":"documentary photography","mood":"hopeful"},"avoid":["crowds","logos"],"copy":{"headline":"The Wall We Built","subtitle":"","call_to_action":""}}

def extract(value=None,**kw):
 value=raw() if value is None else value
 data=json.dumps(value).encode()
 return extract_visual_requirements(value,source_bytes=data,**kw)

def test_source_priorities_and_actual_compilation():
 value=raw(); before=json.dumps(value)
 c=extract(value,priorities={"/visual_direction/supporting_elements/0":"required"})
 result=compile_visual_prompts(c,count_tokens=lambda t:[len(t.split())]*2,limit=120)
 assert "hammer" in result['positive'] and "hands" in result['positive']
 assert "crowds" in result['negative']
 assert {r.priority for r in c.requirements}=={'required','preferred','optional','forbidden'}
 assert all(r.source.sha256==c.source_sha256 for r in c.requirements)
 assert len(result['receipts'])==len(c.requirements)
 assert json.dumps(value)==before

def test_abstract_goal_never_invents_objects():
 value={'concept':{'central_idea':'A bottle and watch represent time'},'core_message':'Hope'}
 c=extract(value)
 assert not any(r.entities for r in c.requirements)
 with pytest.raises(IntentError,match='MISSING_CONCRETE'):compile_visual_prompts(c,count_tokens=lambda t:[1,1])

def test_unknown_required_relation_is_not_silently_dropped():
 value=raw();value['visual_direction']['visual_relationship']='An unspecified metaphysical dependency'
 with pytest.raises(IntentError,match='UNMAPPED_REQUIRED'):compile_visual_prompts(extract(value),count_tokens=lambda t:[1,1])

def test_required_and_forbidden_overflow_block():
 with pytest.raises(IntentError,match='OVERFLOW'):compile_visual_prompts(extract(),count_tokens=lambda t:[100,100])

def test_optional_budget_deferral_is_recorded():
 result=compile_visual_prompts(extract(),count_tokens=lambda t:[100 if 'hopeful' in t else 1]*2)
 assert any(r['status']=='deferred_token_budget' for r in result['receipts'])


def test_legacy_visual_requirements_pointer_is_truthful():
 value=raw();value['visual_requirements']=value.pop('visual_direction')
 c=extract(value)
 assert any(r.source.pointer=='/visual_requirements/main_subject' for r in c.requirements)
 assert not any(r.source.pointer.startswith('/visual_direction') for r in c.requirements)
