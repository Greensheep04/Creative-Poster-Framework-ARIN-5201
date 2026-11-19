"""Opt-in A2 planning. Public Brief/Spec schemas remain unchanged."""
import json
from .types import AgentOutput, ArtifactPurpose
from .layout_planner import plan_poster_spec, POSTER_SPEC_FORMAT
from .intent_mapping import extract_visual_requirements
from .visual_prompt_compiler import compile_visual_prompts
from .visual_requirements import CONTRACT_MEDIA_TYPE, digest

SOURCE_MEDIA_TYPE='application/vnd.poster.a1-source+json'

def read_a1_source(inputs, context):
    sources=[]
    for f in inputs.metadata.files:
        if f.media_type!=SOURCE_MEDIA_TYPE:continue
        ref=context.files.resolve_file(f)
        if ref.role.value!='creative_director' or ref.purpose!=ArtifactPurpose.evidence:
            raise ValueError('A1 source must be registered A1 evidence')
        sources.append((ref,context.files.read(ref)))
    if len(sources)!=1:raise ValueError('Exactly one registered original A1 source is required')
    ref,data=sources[0];raw=json.loads(data)
    if not any(n.get('source_sha256')==ref.sha256 and n.get('original_a1')==raw for n in inputs.content.get('reference_notes',[])):
        raise ValueError('A1 bytes do not match lossless Brief provenance')
    return ref,data,raw

class QualityLayoutPlanner:
    def __init__(self, *, registry, font_family_key, count_tokens, token_limit=77,
                 priorities=None, clarifications=(), planning_policy=None):
        self.registry=registry;self.family=font_family_key
        self.count_tokens=count_tokens;self.token_limit=token_limit
        self.priorities=priorities or {};self.clarifications=clarifications
        self.planning_policy=planning_policy

    async def run(self,inputs,context):
        source_ref,data,raw=read_a1_source(inputs,context)
        contract=extract_visual_requirements(raw,source_bytes=data,priorities=self.priorities)
        from .intent_conflicts import resolve_intent
        contract=resolve_intent(contract,self.clarifications)
        from .visual_visibility import add_visibility_requirements
        contract=add_visibility_requirements(contract)
        compilation=compile_visual_prompts(contract,count_tokens=self.count_tokens,limit=self.token_limit)
        if self.planning_policy:
            spec,layout_evidence=self.planning_policy.plan(raw,context.original_request,self.registry,self.family)
        else:
            spec=plan_poster_spec(raw,request=context.original_request,registry=self.registry,font_family_key=self.family)
            layout_evidence={'basis':'A1 descriptions only','actual_subject_positions':'unknown'}
        for plan in spec['asset_plan']:
            plan['prompt']=compilation['positive'];plan['negative_prompt']=compilation['negative']
        for receipt in compilation['receipts']:
            if receipt['status']=='pending_layout':
                receipt['status']='partial_layout' if receipt['source']['pointer'].endswith('/typography') and layout_evidence.get('unsupported_preferences') else 'planned_layout'
                receipt['spec_targets']=[{'id':e['id'],'box':e['box'],**({k:e[k] for k in ('align','font_family_key','font_size_px')} if e['type']=='text' else {'fit':e['fit']})} for e in spec['elements']]
                receipt['unsupported_preferences']=layout_evidence.get('unsupported_preferences',[])
        envelope={'contract':contract.model_dump(mode='json'),'compilation':compilation,
                  'spec_sha256':digest(spec),'brief_sha256':digest(inputs.content),
                  'source_ref':source_ref.model_dump(mode='json'),'layout_evidence':layout_evidence}
        ref=context.files.write_bytes(context.files.invocation_path('visual-contract.json'),
            json.dumps(envelope,ensure_ascii=False,indent=2).encode(),
            purpose=ArtifactPurpose.evidence,media_type=CONTRACT_MEDIA_TYPE)
        return AgentOutput(content=spec,content_format=POSTER_SPEC_FORMAT,artifacts=(source_ref,ref))
