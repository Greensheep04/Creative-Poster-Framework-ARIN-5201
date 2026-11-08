"""Six source-exact A1 -> actual A2 -> CUDA SDXL/A3 regressions; no A4.

python -m examples.visual.quality_regression --model-dir ... --font-config ...
  --output-dir runs/.../preflight --dry-run
Use a NEW output directory for round 1. Each case has exactly one operation.
There is deliberately no round-2 switch: show round 1 and obtain user decision.
"""
import argparse,asyncio,hashlib,json,os,time
from pathlib import Path
from uuid import uuid4
from PIL import ImageFont
from poster_framework import (AgentRegistration,AgentRole,AgentOutput,AgentInput,
    OriginalRequest,RunSession,ExecutionIdentity,ExecutionMode,ContentProvenance,
    ArtifactPurpose,RunBudget,WORKFLOW_REQUEST_FORMAT,run_single_agent,run_single_agent_from_upstream)
from poster_framework.quality_planner import QualityLayoutPlanner,SOURCE_MEDIA_TYPE
from poster_framework.quality_layout import QualityPlanningPolicy
from poster_framework.layout_fonts import FontRegistry as LayoutFonts,FontFace
from poster_visual_producer.a1_adapter import adapt_a1
from poster_visual_producer.registration import BRIEF_FORMAT,SPEC_FORMAT,registration
from poster_visual_producer.contracts import CreativeBrief,PosterSpec,validate_pair
from poster_visual_producer.cases import artifact_path
from poster_visual_producer.fonts import FontRegistry
from poster_visual_producer.renderer import preflight
from poster_visual_producer.backends import create_generator
from poster_visual_producer.generators import compile_request
from poster_visual_producer.prompt_validation import SDXLPromptValidator
from poster_visual_producer.image_budget import FirstRoundLedger,BudgetedGenerator
from poster_visual_producer.exports import export_formal,export_comparison

ROOT=Path(__file__).resolve().parents[2]
def save(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

class ReplayA1:
    def __init__(self,data,request,source):self.data=data;self.request=request;self.source=source
    async def run(self,inputs,context):
        ref=context.files.write_bytes(context.files.invocation_path('original-a1.json'),self.data,
            purpose=ArtifactPurpose.evidence,media_type=SOURCE_MEDIA_TYPE)
        return AgentOutput(content=adapt_a1(self.data,self.request,self.source),content_format=BRIEF_FORMAT,artifacts=(ref,))

async def prepare(case,folder,generator,fonts,lfonts,validator,font_policy,clarifications):
    folder.mkdir()
    source=ROOT/case['source'];data=source.read_bytes()
    if hashlib.sha256(data).hexdigest()!=case['source_sha256']:raise ValueError('Original A1 source changed')
    raw=json.loads(data);(folder/'a1-original.json').write_bytes(data)
    request=OriginalRequest.from_keywords('quality-'+case['id']+'-'+uuid4().hex[:8],raw['keywords'])
    session=RunSession(request,root=folder/'store',budget=RunBudget(max_image_operations=1,max_revision_rounds=0,max_critic_vision_calls=0),
        configuration={'a4_included':False,'source_sha256':case['source_sha256'],'design_goal':'independent_concept_exploration','round':1})
    identity=ExecutionIdentity(run_id=request.run_id,role=AgentRole.creative_director,invocation_id='a1-source-replay',execution_mode=ExecutionMode.real)
    ctx=session.create_context(identity)
    inputs=AgentInput(content=request.model_dump(mode='json'),content_format=WORKFLOW_REQUEST_FORMAT,metadata=ctx.metadata,
        provenance=ContentProvenance(run_id=request.run_id,source_id=request.request_id))
    a1=AgentRegistration(role=AgentRole.creative_director,instance_factory=lambda c:ReplayA1(data,request,case['source']),
        input_formats=frozenset({WORKFLOW_REQUEST_FORMAT}),output_formats=frozenset({BRIEF_FORMAT}))
    await run_single_agent(a1,inputs,session=session,record_path=folder/'a1-record.json')
    planner=QualityLayoutPlanner(registry=lfonts,font_family_key=font_policy['default_family'],
        count_tokens=validator.count,token_limit=validator.limit,priorities=case['priorities'],
        clarifications=[r for r in clarifications if r['source_sha256']==case['source_sha256']],
        planning_policy=QualityPlanningPolicy(role_families=font_policy['role_families']))
    a2=AgentRegistration(role=AgentRole.layout_planner,instance_factory=lambda c:planner,
        input_formats=frozenset({BRIEF_FORMAT}),output_formats=frozenset({SPEC_FORMAT}))
    layout=await run_single_agent_from_upstream(a2,folder/'a1-record.json',root=folder/'store',record_path=folder/'a2-record.json')
    spec=PosterSpec.model_validate(layout.output.content)
    validate_pair(spec,CreativeBrief.model_validate(adapt_a1(data,request,case['source'])),[k.keyword_id for k in request.keywords])
    preflight(spec,fonts)
    req=compile_request(spec.asset_plan[0],generator.capabilities,seed=42)
    checks=validator(req)
    contract_ref=next(r for r in layout.output.artifacts if r.media_type.endswith('visual-contract+json'))
    envelope=json.loads(artifact_path(folder/'store',contract_ref.model_dump(mode='json')).read_text(encoding='utf-8'))
    save(folder/'a2-visual-contract.json',envelope);save(folder/'poster-spec.json',layout.output.content)
    save(folder/'generation-request.json',{'request':req.as_json(),'backend':dict(generator.capabilities.generation_parameters),'model':generator.capabilities.model,'weights_version':generator.capabilities.weights_version,'tokenizer_checks':checks})
    return {**case,'folder':str(folder),'status':'ready','tokenizer_checks':checks,'template':spec.template_id,
       'a1_execution':'replay of existing remote A1 model output','a2_execution':'actual deterministic A2 quality planner',
       'a3_execution':'existing local CUDA SDXL backend then Pillow','a4_included':False}

async def main(args):
    if not args.model_dir:raise ValueError('--model-dir or POSTER_SDXL_MODEL_DIR required')
    output=Path(args.output_dir).resolve()
    if output.exists():raise ValueError('Use a new run directory; existing results are immutable and never regenerated automatically')
    output.mkdir(parents=True)
    font_policy=json.loads(Path(args.font_config).read_text(encoding='utf-8'))
    fonts=FontRegistry.from_config(font_policy['families'],ROOT)
    lfonts=LayoutFonts({(k,w):FontFace(k,w,f.path,f.index,'trusted-application-config') for k,weights in fonts.families.items() for w,f in weights.items()})
    backend=json.loads((ROOT/'examples/visual/backend-sdxl.json').read_text(encoding='utf-8'))
    backend.update(model_dir=str(Path(args.model_dir).resolve()),cpu_offload=True)
    generator=create_generator(backend);validator=SDXLPromptValidator(Path(args.model_dir))
    save(output/'backend.json',backend);save(output/'font-policy.json',font_policy)
    cases=json.loads((ROOT/'examples/visual/quality-cases.json').read_text(encoding='utf-8'))
    clarifications=json.loads((ROOT/'examples/visual/quality-clarifications.json').read_text(encoding='utf-8'))
    ready=[];errors=[]
    for case in cases:
        try:
            row=await prepare(case,output/case['id'],generator,fonts,lfonts,validator,font_policy,clarifications)
            ready.append(row);print('PREFLIGHT '+case['id']+' '+str(row['tokenizer_checks']),flush=True)
        except Exception as error:
            errors.append({'id':case['id'],'error_type':type(error).__name__,'error':str(error)})
    save(output/'preflight.json',{'ready':ready,'errors':errors,'image_operations':0})
    if errors:raise ValueError('Preflight blocked; zero image operations. See preflight.json')
    if args.dry_run:return
    ledger=FirstRoundLedger(output/'budget.json');rows=[]
    baseline=json.loads((Path(args.baseline_dir)/'report.json').read_text(encoding='utf-8')) if args.baseline_dir else []
    for row in ready:
        folder=Path(row['folder']);started=time.perf_counter()
        try:
            print('GPU GENERATING '+row['id'],flush=True)
            result=await run_single_agent_from_upstream(registration(BudgetedGenerator(generator,ledger,row['id']),fonts,
                seed=42,max_safe_retries=0,request_validator=validator,require_visual_contract=True),
                folder/'a2-record.json',root=folder/'store',record_path=folder/'a3-record.json')
            c=result.output.content;save(folder/'candidate.json',c)
            png=artifact_path(folder/'store',c['candidate_ref']).read_bytes()
            exported=export_formal(png,folder/'poster.png')
            raw_ref=c['assets'][0]['source_ref'];(folder/'raw.png').write_bytes(artifact_path(folder/'store',raw_ref).read_bytes())
            manifest=json.loads(artifact_path(folder/'store',c['manifest_ref']).read_text(encoding='utf-8'));save(folder/'render-manifest.json',manifest)
            expected=adapt_a1((folder/'a1-original.json').read_bytes(),OriginalRequest.from_keywords('verify',json.loads((folder/'a1-original.json').read_bytes())['keywords']),row['source'])['copy']
            assert {k:v['text'] for k,v in expected.items()}=={t['copy_ref']:t['text'] for t in c['text_manifest']}
            evidence_ref=next(r for r in result.output.artifacts if r.uri.endswith('visual-evidence.json'))
            evidence=json.loads(artifact_path(folder/'store',evidence_ref.model_dump(mode='json')).read_text(encoding='utf-8'));save(folder/'visual-evidence-pending.json',evidence)
            row.update(status='rendered',poster=exported['path'],raw=str(folder/'raw.png'),elapsed_seconds=round(time.perf_counter()-started,3),exact_copy_verified=True,semantic_status='unknown',design_status='unknown')
            if baseline:
                before=next(r for r in baseline if r['source']==row['source'])
                label_font=ImageFont.truetype(str(fonts.families[font_policy['default_family']][400].path),20)
                export_comparison([{'poster':before['poster'],'label':'BEFORE','details':'Original baseline | seed 42'},{'poster':row['poster'],'label':'AFTER','details':'Round 1 | seed 42'}],folder/'before-after.png',label_font,columns=2)
        except Exception as error:
            row.update(status='failed',error_type=type(error).__name__,error=str(error),elapsed_seconds=round(time.perf_counter()-started,3))
        rows.append(row);save(output/'report.json',rows)
        print('RESULT '+row['id']+' '+row['status'],flush=True)
    rendered=[r for r in rows if r['status']=='rendered']
    if rendered:
        label_font=ImageFont.truetype(str(fonts.families[font_policy['default_family']][400].path),18)
        export_comparison([{'poster':r['poster'],'label':r['id']+' | '+r['title'],'details':'Round 1 | seed 42 | inspect evidence'} for r in rendered],output/'contact-sheet.png',label_font)
    save(output/'round-status.json',{'status':'awaiting_user_round1_review','rendered':len(rendered),'attempts':json.loads(ledger.path.read_text()) if ledger.path.exists() else {'attempts':[]},'remaining_total_ceiling':15-len(json.loads(ledger.path.read_text())['attempts']) if ledger.path.exists() else 15,'further_generation_authorized':False})
    print('FIRST ROUND COMPLETE; stop for visual evidence and user decision',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--model-dir',default=os.environ.get('POSTER_SDXL_MODEL_DIR'))
    parser.add_argument('--font-config',required=True)
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--baseline-dir')
    parser.add_argument('--dry-run',action='store_true')
    asyncio.run(main(parser.parse_args()))
