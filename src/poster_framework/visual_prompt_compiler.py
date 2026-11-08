"""Compile concrete propositions with receipts; never truncate required constraints."""
from .visual_requirements import IntentError
import re

_COLOR_NAMES={'sky blue':(135,206,235),'light blue':(102,178,255),'navy blue':(0,51,102),
 'charcoal gray':(46,46,46),'dark gray':(58,58,58),'white':(255,255,255),'black':(0,0,0),
 'red':(255,0,0),'green':(0,128,0),'blue':(0,0,255),'yellow':(255,255,0),
 'terracotta':(183,110,82),'sage green':(156,168,137)}

def color_words(value):
    if not re.fullmatch(r'#[0-9a-fA-F]{6}',value):return value
    rgb=tuple(int(value[i:i+2],16) for i in (1,3,5))
    return min(_COLOR_NAMES,key=lambda name:sum((x-y)**2 for x,y in zip(rgb,_COLOR_NAMES[name])))


def compile_visual_prompts(contract, *, count_tokens, limit=77):
    if contract.issues: raise IntentError(contract.issues)
    positive=[];negative=['text','logos','watermarks'];receipts=[]
    requirements=sorted(contract.requirements,key=lambda r: ({'required':0,'forbidden':1,'preferred':2,'optional':3}[r.priority], {'composition':0,'style':1,'palette':2,'scene':3,'subject':4}.get(r.category,5)))
    for req in requirements:
        if req.disposition=='superseded':
            receipts.append({'requirement_id':req.requirement_id,'source':req.source.model_dump(),'priority':req.priority,'status':'authorized_superseded','resolution_id':req.resolution_id});continue
        if req.category in ('goal','reading_order','typography','composition') and not req.predicates:
            receipts.append({'requirement_id':req.requirement_id,'source':req.source.model_dump(),'priority':req.priority,'status':'applied_layout' if req.category!='goal' else 'context_only','evidence_ids':[r.requirement_id for r in requirements if r.priority=='required' and r.entities]})
            continue
        fragments = req.predicates or req.entities
        if req.category=='relation' and not req.predicates:fragments=[]
        if req.category in ('style','mood','palette','forbidden'): fragments=[color_words(req.source.quote) if req.category=='palette' else req.source.quote]
        if not fragments:
            if req.priority=='required':raise IntentError([{'code':'UNMAPPED_REQUIRED_RELATION','pointers':[req.source.pointer]}])
            receipts.append({'requirement_id':req.requirement_id,'source':req.source.model_dump(),'priority':req.priority,'status':'unmapped_preferred','reason':'not supported by deterministic vocabulary'});continue
        channel=negative if req.priority=='forbidden' else positive
        additions=[s for s in fragments if s not in channel]
        proposed='; '.join(channel+additions)
        if max(count_tokens(proposed))>limit:
            if req.priority in ('required','forbidden'):raise IntentError([{'code':'REQUIRED_PROMPT_OVERFLOW','pointers':[req.source.pointer]}])
            receipts.append({'requirement_id':req.requirement_id,'source':req.source.model_dump(),'priority':req.priority,'status':'deferred_token_budget'});continue
        channel.extend(additions)
        receipts.append({'requirement_id':req.requirement_id,'source':req.source.model_dump(),'priority':req.priority,'status':'emitted_negative' if channel is negative else 'emitted_positive','fragments':additions,'merged':not additions})
    return {'positive':'; '.join(positive),'negative':'; '.join(negative),'positive_tokens':count_tokens('; '.join(positive)),'negative_tokens':count_tokens('; '.join(negative)),'receipts':receipts}
