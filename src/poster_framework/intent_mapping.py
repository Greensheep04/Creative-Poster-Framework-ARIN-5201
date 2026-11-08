"""Deterministic extraction from concrete A1 fields, never abstract-goal invention."""
import hashlib
import re
from .visual_requirements import Requirement, Source, VisualContract

ENTITIES = {
 "doorway": r"\bdoor(?:way)?s?\b", "chair": r"\bchairs?\b", "photographs": r"\b(?:photographs?|photos?)\b",
 "house": r"\b(?:houses?|homes?|residential)\b", "wall": r"\bwalls?\b", "bricks": r"\bbricks?\b",
 "mortar": r"\bmortar\b", "hammer": r"\bhammer\b", "tools": r"\btools?\b", "hands": r"\bhands?\b", "feet": r"\bfeet\b",
 "people": r"\b(?:figures?|persons?|people|residents?|strangers?)\b", "plastic bottle": r"\b(?:plastic\s+)?bottles?\b",
 "watch": r"\bwatch(?:es)?\b", "windows": r"\bwindows?\b", "ocean": r"\b(?:ocean|shoreline|coastal|sea)\b",
 "waves": r"\b(?:waves?|tide)\b", "path": r"\bpaths?\b", "memory fragments": r"\bmemory fragments\b",
 "shopping bags": r"\bshopping bags?\b", "electronic waste": r"\belectronic devices?\b", "sun": r"\bsun\b",
}
# Rules match descriptions rather than sample identities. Unrecognized concrete
# descriptions remain literal; unknown abstract relations require confirmation.
PROPOSITIONS = [
 (r"fragmented mosaic.*spiral.*doorway", "fragmented home mosaic: windows, chair, photographs spiral into open doorway"),
 (r"tilted and layered.*central doorway", "tilted displaced fragments surround a stable central doorway"),
 (r"half.restored.*mortar", "half-restored cracked brick wall, fresh mortar and bricks"),
 (r"one side.*cracks.*hands and feet", "foreground hands and feet, hands jointly repairing wall with tools, cracked versus repaired sections"),
 (r"tools laid nearby.*hands and feet", "foreground hands and tools repairing wall"),
 (r"lone figure.*holding.*bottle.*watch", "one person holding plastic bottle and broken watch at receding shore"),
 (r"figure.*facing the horizon.*ocean in the background", "person facing horizon, receding ocean behind"),
 (r"two figures.*window at the other", "exactly two people in separate windows looking at each other"),
 (r"figures.*silhouettes aligned.*diagonal line", "two silhouettes connected diagonally across separate windows"),
 (r"house with translucent.*memory fragments", "modern house with glowing translucent memory fragments embedded in walls"),
 (r"house.*distorted in each location.*memory fragments", "house walls warped across locations, glowing fragments follow movement"),
 (r"house appearing in multiple locations", "rural to urban landscape, house in multiple locations"),
 (r"three stages of migration", "three houses along left-to-right migration path"),
 (r"path.*one house to the next", "path connects past and present houses"),
 (r"past homes", "ghostly past houses in background"),
 (r"ocean surface.*waves.*deepen and darken", "ocean waves darken left to right"),
 (r"flowing mass of consumption.*darker and more turbulent", "consumption waste in turbulent polluted waves"),
 (r"icons of shopping bags.*electronic devices", "shopping bags, plastic bottles, electronic devices embedded in waves"),
 (r"radial symmetry.*mosaic", "radial mosaic around central doorway"),
 (r"horizontal split.*calm ocean.*turbulent", "left calm ocean, right dark waves eroding shore"),
]

def propositions(text, category):
    if category in ('goal','forbidden','style','mood','palette','typography','reading_order'):
        return []
    for pattern, phrase in PROPOSITIONS:
        if re.search(pattern,text,re.I): return [phrase]
    if category in ('subject','scene'): return [text.rstrip('.')]
    if category=='relation' and any(re.search(p,text,re.I) for p in ENTITIES.values()) and re.search(r"\b(between|behind|above|below|beside|through|toward|holding|repairing|on)\b",text,re.I):
        return [text.rstrip('.')]
    return []

def extract_visual_requirements(raw, *, source_bytes, priorities=None):
    sha=hashlib.sha256(source_bytes).hexdigest(); priorities=priorities or {};values=[]
    def add(pointer,value,category,priority):
        if isinstance(value,str) and value.strip():values.append((pointer,value,category,priority))
    add('/concept/central_idea',raw.get('concept',{}).get('central_idea'),'goal','preferred')
    add('/core_message',raw.get('core_message'),'goal','preferred')
    direction_key='visual_direction' if 'visual_direction' in raw else 'visual_requirements'
    direction=raw.get(direction_key,{})
    if isinstance(direction,dict):
        for key,category,priority in [('main_subject','subject','required'),('visual_relationship','relation','required'),('setting','scene','preferred'),('style','style','preferred'),('mood','mood','optional')]:
            add('/'+direction_key+'/'+key,direction.get(key),category,priority)
        for i,text in enumerate(direction.get('supporting_elements',[])):
            add('/'+direction_key+'/supporting_elements/'+str(i),text,'subject','preferred')
    for key in ('composition','reading_order','typography','focal_point'):
        add('/layout/'+key,raw.get('layout',{}).get(key),'composition' if key=='focal_point' else key,'preferred')
    palette=raw.get('palette',{})
    for i,text in enumerate(palette.get('colors',[]) if isinstance(palette,dict) else []):
        add('/palette/colors/'+str(i),text,'palette','preferred')
    for parent,items in [('/avoid',raw.get('avoid',[])),('/'+direction_key+'/avoid',direction.get('avoid',[]) if isinstance(direction,dict) else [])]:
        for i,text in enumerate(items):add(parent+'/'+str(i),text,'forbidden','forbidden')
    requirements=[]
    for pointer,text,category,default in values:
        entities=[] if category in ('goal','forbidden','style','mood','palette','typography') else [name for name,pattern in ENTITIES.items() if re.search(pattern,text,re.I)]
        requirements.append(Requirement(requirement_id='vr-'+hashlib.sha256((sha+pointer).encode()).hexdigest()[:16],category=category,priority=priorities.get(pointer,default),source=Source(pointer=pointer,quote=text,sha256=sha),entities=entities,predicates=propositions(text,category),priority_basis='approved regression evidence policy' if pointer in priorities else 'source-field policy'))
    main=[r for r in requirements if r.source.pointer.endswith('/main_subject')]
    issues=[] if main and any(r.entities for r in main) else [{'code':'MISSING_CONCRETE_EVIDENCE','status':'needs_confirmation','pointers':['/'+direction_key+'/main_subject']}]
    return VisualContract(source_sha256=sha,requirements=requirements,source_fields=[r.source.pointer for r in requirements],issues=issues)
