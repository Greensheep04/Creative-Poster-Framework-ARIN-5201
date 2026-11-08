"""Source-backed size/legibility policy; no new core objects or pixel claims."""
from .visual_requirements import Requirement
PROPS={'chair','photographs','hammer','plastic bottle','watch','shopping bags','electronic waste'}

def add_visibility_requirements(contract):
    requirements=list(contract.requirements)
    for r in contract.requirements:
        props=[e for e in r.entities if e in PROPS]
        if r.priority!='required' or r.disposition!='active' or not props:continue
        # Only subjects explicitly naming the props can authorize a size clause.
        if r.category!='subject':continue
        requirements.append(Requirement(requirement_id=r.requirement_id+'-visibility',category='composition',priority='required',
            source=r.source,entities=props,predicates=['large clearly recognizable props'],
            priority_basis='approved P3 prop-legibility policy; no additional objects'))
    return contract.model_copy(update={'requirements':requirements})
