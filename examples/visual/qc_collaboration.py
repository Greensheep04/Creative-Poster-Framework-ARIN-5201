"""Reproducible A3/QC engineering acceptance; fixtures never assert visual quality."""
from pathlib import Path
from types import SimpleNamespace
import argparse, asyncio, copy, hashlib, json, sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from examples.visual.workflow_demo import DemoCreativeDirector,DemoLayoutPlanner,document
from examples.visual.render_fixture import ocean_fixture
from poster_framework import (AgentRegistration,AgentRole,AgentSet,PosterWorkflow,WORKFLOW_REQUEST_FORMAT,
    RepairAuthorization,RevisionAction,VersionDependency)
from poster_visual_producer.fonts import FontRegistry
from poster_visual_producer.generators import FixtureImageGenerator
from poster_visual_producer.registration import BRIEF_FORMAT,SPEC_FORMAT,registration,project_budget
from poster_visual_producer.qc_bridge import qc_registration,validate_audit_report,ReviewBundle
from poster_visual_producer.qc_contracts import LocalManifestV1,ALL_CHECKS,required_copies

SCENARIOS=('no_authorization','regenerate_asset','rerender','revise_layout')

def fixed_report(bundle,reason):
    """Complete Fake transport fixture: no observations or model calls are claimed."""
    m=bundle.manifest
    return {'schema_version':3,'sample_id':m.sample_id,'image_sha256':bundle.candidate_link['candidate_ref']['sha256'],
        'decision':'unknown','keyword_checks':[{'keyword_id':k.keyword_id,'status':'unknown','evidence':'engineering fixture only'} for k in m.keywords],
        'constraint_checks':[{'constraint_id':c.constraint_id,'status':'unknown','evidence':'engineering fixture only'} for c in m.user_constraints],
        'text_checks':[{'copy_id':key,'observed_text':None,'status':'unknown','readability':'unknown'} for key in required_copies(m)],
        'coverage':[{'check_id':key,'required':key in m.required_checks,'state':'unknown' if key in m.required_checks else 'not_assessed','status':'unknown' if key in m.required_checks else None,'reason':'no real A4 model in this demo'} for key in ALL_CHECKS],
        'issues':[],'uncertainties':[reason],'repair_hint':None,'model':None,'prompt_version':'engineering-fixture-v1','execution_mode':'fake','calls':0,'vision_calls':0}

async def run_case(root,scenario):
    if scenario not in SCENARIOS:raise ValueError('Unknown scenario')
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    candidates=[];bundles=[];reports=[];stale_checks=[];generated=[0]
    # Delegate unchanged A1/A2 demo implementations. This fixture publisher adds a second
    # sample asset only for exercising partial reuse; it is not an A2 production change.
    class LayoutFixturePublisher:
        async def run(self,inputs,context):
            output=await DemoLayoutPlanner(scenario).run(inputs,context)
            if scenario=='regenerate_asset':
                sp=output.content
                asset=copy.deepcopy(sp['asset_plan'][0])
                asset.update(asset_id='accent_image',prompt='blue accent',required_keyword_ids=[],target_size={'width':120,'height':20})
                sp['asset_plan'].append(asset)
                sp['elements'].append({'id':'accent','type':'image','z':4,'box':{'x':36,'y':224,'width':120,'height':20},'asset_id':'accent_image','fit':'cover','focus_point_norm':[0.5,0.5]})
            return output
    async def audit(path,context):
        path=Path(path)
        manifest=LocalManifestV1.model_validate_json(path.read_text(encoding='utf-8'))
        link=json.loads((path.parent/'candidate_link.json').read_text(encoding='utf-8'))
        candidate=json.loads((path.parent/'evidence/candidate.json').read_text(encoding='utf-8'))
        bundle=ReviewBundle(path,path.parent/'candidate_link.json',manifest,link)
        report=fixed_report(bundle,'fixture requests one authorized correction' if not candidates else 'real A4 assessment still required')
        old=copy.deepcopy(candidate);old['candidate_ref']['revision']='previous-revision-with-identical-pixels'
        try:validate_audit_report(report,bundle,old)
        except ValueError:stale_checks.append(True)
        else:stale_checks.append(False)
        candidates.append(candidate);bundles.append(str(path));reports.append(report)
        return report
    async def authorize(feedback,context):
        if scenario=='no_authorization' or len(candidates)>1:return None
        c=feedback['current_candidate'];ref=c['candidate_ref']
        action=RevisionAction(scenario)
        target=c['assets'][0]['source_ref'] if action==RevisionAction.regenerate_asset else feedback['layout_dependency'] if action==RevisionAction.revise_layout else ref
        return RepairAuthorization(authorization_id='fixture-approved-'+scenario,
            based_on_candidate=VersionDependency(artifact_id=ref['artifact_id'],revision=ref['revision']),action=action,
            target=VersionDependency(artifact_id=target['artifact_id'],revision=target['revision']),expected_versions=c['based_on'],
            constraints=['Keep approved text and unrelated assets'],preserve=['title','subtitle','accent'] if scenario=='regenerate_asset' else ['title','subtitle'])
    def source(request):
        n=generated[0];generated[0]+=1
        return ocean_fixture(n)
    fonts=FontRegistry.from_config(document('fonts'),ROOT)
    agents=AgentSet([
        AgentRegistration(role=AgentRole.creative_director,instance_factory=lambda c:DemoCreativeDirector(),input_formats=frozenset({WORKFLOW_REQUEST_FORMAT}),output_formats=frozenset({BRIEF_FORMAT})),
        AgentRegistration(role=AgentRole.layout_planner,instance_factory=lambda c:LayoutFixturePublisher(),input_formats=frozenset({BRIEF_FORMAT}),output_formats=frozenset({SPEC_FORMAT})),
        registration(FixtureImageGenerator(source),fonts),qc_registration(audit,bundle_root=root/'bundles')])
    result=await PosterWorkflow(agents,root=root/'runtime',budget=project_budget(),repair_authorizer=authorize).run(['海洋','宁静','蓝色'])
    checks={'stale_report_rejected':all(stale_checks),'unreleased_fake_audit':result.status.value=='needs_review','original_unknown_retained':all(r['decision']=='unknown' for r in reports)}
    if len(candidates)>1:
        a,b=candidates
        checks['approved_text_preserved']=[(t['copy_ref'],t['text']) for t in a['text_manifest']]==[(t['copy_ref'],t['text']) for t in b['text_manifest']]
        if scenario=='regenerate_asset':
            checks['unaffected_asset_preserved']=a['assets'][1]['source_ref']==b['assets'][1]['source_ref']
            checks['target_replaced']=a['assets'][0]['source_ref']['sha256']!=b['assets'][0]['source_ref']['sha256']
            checks['layout_preserved']=a['text_manifest']==b['text_manifest']
        else:
            checks['sources_reused']=[x['source_ref']['sha256'] for x in a['assets']]==[x['source_ref']['sha256'] for x in b['assets']]
        if scenario=='revise_layout':checks['layout_changed']=a['based_on']['spec_version']+1==b['based_on']['spec_version'] and a['text_manifest']!=b['text_manifest']
    checks['passed']=all(checks.values())
    summary={'scenario':scenario,'status':result.status.value,'source_mode':'offline_fixture','visual_quality_evaluated':False,
        'image_operations':result.usage.image_operations,'revision_rounds':result.revision_rounds,
        'call_sequence':[r.value for r in result.call_sequence],'candidates':candidates,'bundles':bundles,
        'record_path':str(result.record_path),'invariants':checks}
    (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    if not checks['passed']:raise AssertionError(checks)
    return summary

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--scenario',choices=(*SCENARIOS,'all'),default='all')
    args=parser.parse_args()
    async def run():
        choices=SCENARIOS if args.scenario=='all' else (args.scenario,)
        return [await run_case(args.root/s,s) for s in choices]
    cases=asyncio.run(run())
    compact=[{k:c[k] for k in ('scenario','status','image_operations','revision_rounds','invariants','record_path')} for c in cases]
    (args.root/'acceptance.json').write_text(json.dumps(compact,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(compact,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
