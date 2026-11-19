"""Deterministic A2 conflict checks. Clarifications never mutate original A1."""
import re
from .intent_mapping import ENTITIES
from .visual_requirements import Requirement

def resolve_intent(contract, clarifications=()):
    reqs=list(contract.requirements);issues=list(contract.issues);resolutions=[]
    approved={}
    for record in clarifications:
        if record.get('source_sha256')!=contract.source_sha256 or not record.get('authorization_id'):
            issues.append({'code':'INVALID_CLARIFICATION_IDENTITY','status':'needs_confirmation'});continue
        code=record.get('code')
        if code not in ('HELD_PROPS_FOREGROUND','REAL_UNBRANDED_WASTE') or code in approved:
            issues.append({'code':'INVALID_CLARIFICATION_SCOPE','status':'needs_confirmation'});continue
        approved[code]=record
    def replace(index,**fields):
        reqs[index]=reqs[index].model_copy(update=fields)
    def resolved(code,reason,source_ids,authorization=None):
        resolutions.append({'code':code,'status':'resolved','reason':reason,'requirement_ids':source_ids,'authorization':authorization})
    main=[(i,r) for i,r in enumerate(reqs) if r.source.pointer.endswith('/main_subject')]
    held=[(i,r) for i,r in main if re.search(r'hold',r.source.quote,re.I)]
    grounded=[(i,r) for i,r in enumerate(reqs) if 'supporting_elements' in r.source.pointer and re.search(r'buried|lying on|on the sand|on the ground',r.source.quote,re.I)]
    for i,r in grounded:
        common=set(r.entities)&set(e for _,h in held for e in h.entities)
        if not common:continue
        if 'HELD_PROPS_FOREGROUND' not in approved:
            issues.append({'code':'OBJECT_LOCATION_CONFLICT','status':'needs_confirmation','pointers':[r.source.pointer]+[h.source.pointer for _,h in held],'entities':sorted(common)})
        else:
            replace(i,disposition='superseded',resolution_id='HELD_PROPS_FOREGROUND')
    if held and 'HELD_PROPS_FOREGROUND' in approved:
        for i,r in held:
            if not {'plastic bottle','watch'}<=set(r.entities):
                issues.append({'code':'CLARIFICATION_NOT_APPLICABLE','status':'needs_confirmation'});continue
            replace(i,predicates=['foreground close view: one person holding exactly one plastic bottle and one broken watch'])
        for _,r in held:
            reqs.append(Requirement(requirement_id=r.requirement_id+'-quantity',category='forbidden',priority='forbidden',
                source=r.source,entities=['plastic bottle','watch'],
                predicates=['extra bottles, extra watches, bottles on ground, watches on ground'],
                priority_basis='explicit approved HELD_PROPS_FOREGROUND quantity/location restriction',
                resolution_id='HELD_PROPS_FOREGROUND'))
        resolved('HELD_PROPS_FOREGROUND','User approved held props; duplicate ground placements superseded, not deleted',[r.requirement_id for _,r in held+grounded],approved['HELD_PROPS_FOREGROUND'])
    forbidden=[r for r in reqs if r.priority=='forbidden']
    for i,r in enumerate(reqs):
        if r.disposition!='active' or r.priority=='forbidden':continue
        quote=r.source.quote.lower()
        if re.search(r'icons?.*(shopping bags|plastic bottles|electronic)',quote) and any(re.search(r'icons?.*(products|brands)',f.source.quote,re.I) for f in forbidden):
            if 'REAL_UNBRANDED_WASTE' not in approved:
                issues.append({'code':'REQUIRED_REPRESENTATION_FORBIDDEN','status':'needs_confirmation','pointers':[r.source.pointer]+[f.source.pointer for f in forbidden if 'icon' in f.source.quote.lower()]})
            else:
                replace(i,predicates=['real unbranded shopping bags, plastic bottles and electronic waste carried in waves'])
                resolved('REAL_UNBRANDED_WASTE','User approved real waste, forbids logos and product icons',[r.requirement_id],approved['REAL_UNBRANDED_WASTE'])
        if r.priority!='required':continue
        for f in forbidden:
            # Qualified prohibitions have a narrower scope than an object ban.
            if re.match(r'^(?:no\s+)?(?:icons?\s+(?:of|for)|individual\s+icons?\s+for|a\s+map\s+of|maps?\b|overly\s+literal\s+depictions\s+of|facial\s+expressions|crowds?\b|generic\s+slogans?\s+like)',f.source.quote,re.I):continue
            if f.resolution_id=='HELD_PROPS_FOREGROUND':continue
            banned={name for name,pattern in ENTITIES.items() if re.search(pattern,f.source.quote,re.I)}
            common=set(r.entities)&banned
            if common:
                issues.append({'code':'REQUIRED_OBJECT_FORBIDDEN','status':'needs_confirmation','entities':sorted(common),'pointers':[r.source.pointer,f.source.pointer]})
    mosaic=[(i,r) for i,r in enumerate(reqs) if re.search(r'mosaic|collage',r.source.quote,re.I) and r.priority!='forbidden']
    if mosaic and any(re.search(r'crowded|chaotic',f.source.quote,re.I) for f in forbidden):
        i,r=mosaic[0];replace(i,predicates=r.predicates+['well-separated fragments'])
        resolved('DENSITY_CONTROL','Keep all core objects, space fragments; decoration may defer under token budget',[r.requirement_id])
    return contract.model_copy(update={'requirements':reqs,'issues':issues,'resolutions':resolutions})
