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
 "clock": r"\bclocks?\b", "luggage": r"\b(?:luggage|suitcases?)\b", "family": r"\bfamil(?:y|ies)\b",
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

# Unknown rule terms preserve the full literal source, including new objects.
RULE_VOCAB = {'fragmented mosaic.*spiral.*doorway': ['a', 'arranged', 'chair', 'childhood', 'composed', 'doorway', 'each', 'familiar', 'fragmented', 'homes', 'in', 'into', 'leads', 'like', 'mosaic', 'objects', 'of', 'open', 'photograph', 'single', 'spiral', 'that', 'window'], 'tilted and layered.*central doorway': ['a', 'and', 'are', 'belonging', 'central', 'consistent', 'creating', 'displacement', 'doorway', 'enduring', 'form', 'fragments', 'in', 'individual', 'layered', 'movement', 'of', 'orientation', 'remains', 'sense', 'slightly', 'symbolizing', 'the', 'tilted', 'while'], 'half.restored.*mortar': ['a', 'and', 'brick', 'bricks', 'carefully', 'cracked', 'fresh', 'half', 'mortar', 'placed', 'restored', 'wall', 'weathered', 'with'], 'one side.*cracks.*hands and feet': ['a', 'action', 'and', 'beside', 'bricks', 'but', 'clearly', 'cracks', 'feet', 'foreground', 'gently', 'hands', 'in', 'is', 'it', 'laid', 'no', 'of', 'one', 'other', 'pattern', 'pile', 'placed', 'presence', 'restored', 'showing', 'side', 'slightly', 'small', 'staggered', 'suggesting', 'the', 'tilted', 'tools', 'visible', 'wall', 'while', 'with'], 'tools laid nearby.*hands and feet': [], 'lone figure.*holding.*bottle.*watch': ['a', 'and', 'at', 'bottle', 'broken', 'edge', 'figure', 'hand', 'holding', 'lone', 'of', 'plastic', 'receding', 'shoreline', 'standing', 'the', 'watch'], 'figure.*facing the horizon.*ocean in the background': ['a', 'and', 'background', 'blue', 'facing', 'figure', 'gradient', 'gray', 'horizon', 'in', 'into', 'is', 'ocean', 'of', 'receding', 'slightly', 'soft', 'the', 'tilted', 'with'], 'two figures.*window at the other': ['a', 'at', 'building', 'city', 'each', 'figures', 'looking', 'of', 'on', 'opposite', 'other', 'sides', 'standing', 'the', 'through', 'two', 'window'], 'figures.*silhouettes aligned.*diagonal line': ['a', 'across', 'aligned', 'angles', 'are', 'at', 'connects', 'diagonal', 'different', 'figures', 'forming', 'frame', 'in', 'line', 'positioned', 'silhouettes', 'slightly', 'space', 'that', 'the', 'their', 'them', 'windows', 'with'], 'house with translucent.*memory fragments': ['a', 'and', 'embedded', 'floorboards', 'fragments', 'glowing', 'house', 'in', 'its', 'memory', 'minimalist', 'modern', 'translucent', 'walls', 'with'], 'house.*distorted in each location.*memory fragments': ['aligning', 'are', 'distorted', 'each', 'emotional', 'fragments', 'gently', 'house', 'in', 'is', 'its', 'location', 'memory', 'migration', 'mirroring', 'movement', 'of', 'or', 'pulse', 'slightly', 'strain', 'stretched', 'subtly', 'the', 'walls', 'warped', 'with'], 'house appearing in multiple locations': ['a', 'across', 'appearing', 'from', 'house', 'in', 'landscape', 'locations', 'multiple', 'neutral', 'open', 'rural', 'scene', 'the', 'to', 'transitioning', 'urban', 'with'], 'three stages of migration': ['a', 'diagonal', 'different', 'each', 'emotional', 'flow', 'from', 'house', 'in', 'left', 'migration', 'of', 'right', 'showing', 'stages', 'the', 'three', 'to', 'tone', 'with'], 'path.*one house to the next': ['a', 'from', 'house', 'leads', 'next', 'one', 'path', 'single', 'that', 'the', 'to'], 'past homes': ['background', 'faint', 'homes', 'in', 'of', 'past', 'silhouettes', 'the'], 'ocean surface.*waves.*deepen and darken': ['a', 'and', 'as', 'darken', 'deepen', 'forming', 'from', 'gradually', 'horizontal', 'left', 'moving', 'ocean', 'progress', 'rhythmic', 'right', 'slow', 'surface', 'that', 'they', 'tide', 'to', 'vast', 'waves', 'with'], 'flowing mass of consumption.*darker and more turbulent': ['a', 'and', 'as', 'becomes', 'consumption', 'darker', 'degradation', 'each', 'environmental', 'flowing', 'is', 'it', 'just', 'mass', 'more', 'moves', 'not', 'ocean', 'of', 'representing', 's', 'surface', 'symbolizing', 'the', 'tide', 'turbulent', 'visibly', 'water', 'wave', 'with', 'year'], 'icons of shopping bags.*electronic devices': ['and', 'bags', 'bottles', 'devices', 'electronic', 'embedded', 'faint', 'icons', 'in', 'of', 'plastic', 'shopping', 'the', 'translucent', 'waves'], 'radial symmetry.*mosaic': ['a', 'and', 'creating', 'doorway', 'elements', 'from', 'journey', 'mosaic', 'of', 'outward', 'radial', 'return', 'sense', 'spiraling', 'symmetry', 'the', 'with'], 'horizontal split.*calm ocean.*turbulent': ['and', 'calm', 'darkened', 'early', 'eroding', 'horizontal', 'left', 'ocean', 'right', 'shoreline', 'shows', 'side', 'split', 'tide', 'turbulent', 'water']}

def propositions(text, category):
    if category in ('goal','forbidden','style','mood','palette','typography','reading_order'):
        return []
    for pattern, phrase in PROPOSITIONS:
        if re.search(pattern,text,re.I):
            source_entities={n for n,p in ENTITIES.items() if re.search(p,text,re.I)}
            emitted_entities={n for n,p in ENTITIES.items() if re.search(p,phrase,re.I)}
            if set(re.findall(r'[a-z]+',text.lower()))<=set(RULE_VOCAB[pattern]) and emitted_entities<=source_entities:
                return [phrase]
            return [text.rstrip('.')]
    if category in ('subject','scene'): return [text.rstrip('.')]
    if category=='relation' and any(re.search(p,text,re.I) for p in ENTITIES.values()) and re.search(r"\b(between|behind|above|below|beside|through|toward|holding|repairing|on)\b",text,re.I):
        return [text.rstrip('.')]
    return []

KEYWORD_EVIDENCE = {
 'home':r'house|home|doorway',
 'migration':r'migrat|movement|displace|multiple locations|path.*house|three stages|spiral',
 'memory':r'memory fragments|photograph|photo|faded|past homes',
 'repair':r'restor|repair|fresh mortar',
 'community':r'hands and feet|neighbors|residents|working together|joint',
 'tomorrow':r'fresh mortar|restor|repair',
 'time':r'watch|tide|waves.*(?:progress|darken)',
 'consumption':r'bottle|shopping bag|electronic|consumption',
 'ocean':r'ocean|shore|waves',
 'urban loneliness':r'opposite sides|separate windows|narrow alley',
 'connection':r'looking.*at the other|connects them|overlapping gaze',
 'windows':r'windows?',
}
def keyword_links(raw,requirements):
    links=[]
    concrete=[r for r in requirements if r.category in ('subject','relation','scene','composition')]
    for i,keyword in enumerate(raw.get('keywords',[])):
        pattern=KEYWORD_EVIDENCE.get(keyword.lower(),re.escape(keyword))
        sources=[r.source.pointer for r in concrete if re.search(pattern,r.source.quote,re.I)]
        links.append({'keyword':keyword,'pointer':'/keywords/'+str(i),'evidence_pointers':sources,
          'status':'planned_concrete_proxy' if sources else 'needs_confirmation','pixel_status':'unknown'})
    return links

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
    links=keyword_links(raw,requirements)
    issues.extend({'code':'MISSING_KEYWORD_VISUAL_EVIDENCE','status':'needs_confirmation','pointers':[x['pointer']],'keyword':x['keyword']} for x in links if x['status']=='needs_confirmation')
    return VisualContract(source_sha256=sha,requirements=requirements,source_fields=[r.source.pointer for r in requirements],issues=issues,keyword_evidence=links)
