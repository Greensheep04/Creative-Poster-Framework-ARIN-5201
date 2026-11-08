"""Compile concrete propositions with receipts; never truncate required constraints."""
from .visual_requirements import IntentError

def compile_visual_prompts(contract, *, count_tokens, limit=77):
    if contract.issues: raise IntentError(contract.issues)
    positive=[];negative=['text','logos','watermarks'];receipts=[]
    requirements=sorted(contract.requirements,key=lambda r: {'required':0,'forbidden':1,'preferred':2,'optional':3}[r.priority])
    for req in requirements:
        if req.category in ('goal','reading_order','typography','composition') and not req.predicates:
            receipts.append({'requirement_id':req.requirement_id,'source':req.source.model_dump(),'priority':req.priority,'status':'applied_layout' if req.category!='goal' else 'context_only','evidence_ids':[r.requirement_id for r in requirements if r.priority=='required' and r.entities]})
            continue
        fragments = req.predicates or req.entities
        if req.category=='relation' and not req.predicates:fragments=[]
        if req.category in ('style','mood','palette','forbidden'): fragments=[req.source.quote]
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
