import asyncio,json
from pathlib import Path
import pytest
from poster_framework import *
from poster_framework.quality_planner import QualityLayoutPlanner,SOURCE_MEDIA_TYPE
from poster_framework.layout_fonts import FontFace,FontRegistry as LayoutFonts
from poster_framework.layout_planner import plan_poster_spec
from poster_visual_producer.a1_adapter import adapt_a1
from poster_visual_producer.registration import BRIEF_FORMAT,SPEC_FORMAT,registration
from poster_visual_producer.generators import FixtureImageGenerator
from poster_visual_producer.contracts import CreativeBrief
from tests.visual.helpers import fonts,png

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'examples/creative-director-samples/Qwen3-4B-Instruct-2507/repair-community-tomorrow/human-story.json'

def test_lossless_a1_adapter():
    data=SOURCE.read_bytes();raw=json.loads(data)
    request=OriginalRequest.from_keywords('adapter',raw['keywords'])
    brief=adapt_a1(data,request,str(SOURCE))
    CreativeBrief.model_validate(brief)
    assert brief['reference_notes'][0]['original_a1']==raw
    assert brief['copy']['title']['text']==raw['copy']['headline']

def test_real_a2_contract_reaches_a3_request(tmp_path):
    data=SOURCE.read_bytes();raw=json.loads(data)
    request=OriginalRequest.from_keywords('quality',raw['keywords']);f=fonts()
    lf=LayoutFonts({(k,w):FontFace(k,w,v.path,v.index,'test') for k,weights in f.families.items() for w,v in weights.items()})
    class Publish:
        async def run(self,inputs,context):
            ref=context.files.write_bytes(context.files.invocation_path('a1.json'),data,purpose=ArtifactPurpose.evidence,media_type=SOURCE_MEDIA_TYPE)
            return AgentOutput(content=adapt_a1(data,request,'source'),content_format=BRIEF_FORMAT,artifacts=(ref,))
    class Policy:
        def plan(self,raw,request,registry,family):
            return plan_poster_spec(raw,request=request,registry=registry,font_family_key=family,template_id='split_stack_v1'),{'actual_subject_positions':'unknown'}
    async def execute():
        session=RunSession(request,root=tmp_path/'store')
        identity=ExecutionIdentity(run_id=request.run_id,role=AgentRole.creative_director,invocation_id='source',execution_mode=ExecutionMode.real)
        ctx=session.create_context(identity)
        inputs=AgentInput(content=request.model_dump(mode='json'),content_format=WORKFLOW_REQUEST_FORMAT,metadata=ctx.metadata,provenance=ContentProvenance(run_id=request.run_id,source_id=request.request_id))
        a1=AgentRegistration(role=AgentRole.creative_director,instance_factory=lambda c:Publish(),input_formats=frozenset({WORKFLOW_REQUEST_FORMAT}),output_formats=frozenset({BRIEF_FORMAT}))
        await run_single_agent(a1,inputs,session=session,record_path=tmp_path/'a1.json')
        planner=QualityLayoutPlanner(registry=lf,font_family_key='noto_sans_sc',count_tokens=lambda t:[1,1],planning_policy=Policy())
        a2=AgentRegistration(role=AgentRole.layout_planner,instance_factory=lambda c:planner,input_formats=frozenset({BRIEF_FORMAT}),output_formats=frozenset({SPEC_FORMAT}))
        layout=await run_single_agent_from_upstream(a2,tmp_path/'a1.json',root=tmp_path/'store',record_path=tmp_path/'a2.json')
        seen=[]
        def generate(req):seen.append(req);return png()
        candidate=await run_single_agent_from_upstream(registration(FixtureImageGenerator(generate),f),tmp_path/'a2.json',root=tmp_path/'store',record_path=tmp_path/'a3.json')
        assert 'hands and feet' in seen[0].prompt
        assert 'Crowds' in seen[0].prompt  # fixture backend appends unsupported negatives
        assert seen[0].prompt.startswith(layout.output.content['asset_plan'][0]['prompt'])
        assert candidate.output.content['text_manifest'][0]['text']==raw['copy']['headline']
    asyncio.run(execute())
