"""A2-only intended-composition scoring and adjustable typography policies."""
import copy,math,re
from dataclasses import dataclass
from types import SimpleNamespace
from .layout_planner import _visual_prompt,_binding_evidence,_brief_bindings,_check_keywords
from .layout_templates import resolve_palette,build_hero_element,hero_asset_plan
from .layout_templates import get_template,background_color,slot_text_color
from .layout_fonts import wrap_text
from .layout_checks import check_layout_geometry
from .layout_spec import validate_poster_spec
from .layout_errors import TextOverflowError,LayoutContractError

@dataclass(frozen=True)
class TypographyPolicy:
    title_sizes: tuple[int,...]=(64,56,48,44)
    body_sizes: tuple[int,...]=(28,26,24)
    title_leading: float=1.18
    body_leading: float=1.28
    paragraph_gap: int=28

def fit_copy(text,*,family,weight,registry,sizes,width,max_lines,leading,max_height):
    for size in sizes:
        font=registry.load(family,weight,size)
        def bounds(t):return font.getbbox(t,anchor='lt')
        def width_of(t):
            b=bounds(t);return max(font.getlength(t),b[2]-b[0])
        lines=wrap_text(text,SimpleNamespace(getlength=width_of),width)
        line_height=math.ceil(size*leading)
        ink_height=max((bounds(t)[3]-bounds(t)[1] for t in lines),default=0)
        baseline_boxes=[font.getbbox(t) for t in lines]
        legacy_span=max(b[3] for b in baseline_boxes)-min(b[1] for b in baseline_boxes)
        height=(len(lines)-1)*line_height+max(ink_height,legacy_span)
        if len(lines)<=max_lines and height<=max_height and all(width_of(t)<=width for t in lines):
            face=registry.resolve(family,weight)
            if registry.missing_glyphs(face,text):raise LayoutContractError('Approved copy contains missing font glyphs')
            return size,line_height,max(height,line_height),list(lines)
    raise TextOverflowError('Approved copy cannot fit permitted typography tiers; A2 confirmation required')

def score_templates(raw):
    layout=raw.get('layout',{})
    reading=layout.get('reading_order','').lower()
    typography=layout.get('typography','').lower()
    composition=layout.get('composition','').lower()
    scores={'framed_hero_v1':2,'split_stack_v1':2,'full_bleed_v1':-10};reasons={k:[] for k in scores}
    def add(k,score,why):scores[k]+=score;reasons[k].append(why)
    if re.search(r'headline at top|headline above|above the central subject',reading+' '+typography):
        add('framed_hero_v1',9,'A1 places headline above subject')
    if re.search(r'horizontal|three stages|left to right|left-to-right',composition):
        add('split_stack_v1',8,'A1 needs width for spatial progression')
    if re.search(r'starts at the wall|leftmost house|diagonal line of the windows',reading):
        add('split_stack_v1',3,'A1 directs image-first reading')
    if len(raw['copy']['headline'])>45:
        add('split_stack_v1',2,'Long headline benefits from stacked copy area')
    if 'negative space' in composition and re.search(r'overlay|over the image',typography):
        add('full_bleed_v1',20,'A1 explicitly reserves overlay space (actual contrast still unknown)')
    return [{'template_id':k,'score':v,'reasons':reasons[k]} for k,v in sorted(scores.items(),key=lambda p:-p[1])]

def common_spec(raw,request,family):
    """Build shared contract fields without an unrelated template text fitter."""
    _check_keywords(raw,request)
    template=get_template('split_stack_v1');palette=resolve_palette(raw)
    title=raw['copy']['headline'];subtitle=raw['copy'].get('subtitle','')
    brief_bindings=_brief_bindings(raw);bindings=[]
    for keyword in request.keywords:
        ids=['hero']
        if keyword.text in title:ids.append('title')
        if subtitle and keyword.text in subtitle:ids.append('subtitle')
        bindings.append({'keyword_id':keyword.keyword_id,'element_ids':ids,
          'layout_evidence':_binding_evidence(keyword.text,ids,title=title,subtitle=subtitle,
              brief_binding=brief_bindings.get(keyword.keyword_id),template_id=template.template_id)})
    prompt,negative=_visual_prompt(raw,request)
    return {'schema_version':'0.1.0','run_id':request.run_id,'spec_version':1,
      'based_on':{'brief_version':raw.get('brief_version',1)},
      'canvas':{'width':720,'height':1280,'color_mode':'RGB'},'template_id':template.template_id,
      'background':{'color':background_color(template,palette)},
      'design_tokens':{'palette':palette,'font_family_key':family,'safe_margin_px':36},
      'asset_plan':[hero_asset_plan(template,prompt=prompt,negative_prompt=negative,keyword_ids=[k.keyword_id for k in request.keywords])],
      'elements':[build_hero_element(template)],'keyword_element_bindings':bindings,
      'allowed_overlaps':[],'render_policy':{'text_overflow':'error','missing_font':'error','output_format':'PNG'}}

class QualityPlanningPolicy:
    def __init__(self,*,design_goal='independent_concept_exploration',typography=None,
                 role_families=None,series_template='split_stack_v1',series_title_size=56,series_body_size=26):
        if design_goal not in ('independent_concept_exploration','cohesive_poster_series'):raise ValueError('Unknown design goal')
        self.goal=design_goal;self.type=typography or TypographyPolicy()
        self.families=role_families or {};self.series_template=series_template
        self.series_title_size=series_title_size;self.series_body_size=series_body_size

    def plan(self,raw,request,registry,family):
        scores=score_templates(raw);attempts=[]
        candidates=[self.series_template] if self.goal=='cohesive_poster_series' else [x['template_id'] for x in scores]
        typo=raw.get('layout',{}).get('typography','').lower()
        headline_direction=typo.split(';')[0]
        title_family=self.families.get('serif',family) if re.search(r'(?<!sans-)serif',headline_direction) else self.families.get('sans',family)
        body_family=self.families.get('handwritten',family) if 'handwritten' in typo else (self.families.get('sans',family) if 'sans-serif for subtitle' in typo else title_family)
        if self.goal=='cohesive_poster_series':title_family=body_family=family
        base=common_spec(raw,request,family)
        for template_id in candidates:
            try:
                spec,evidence=self._candidate(base,raw,template_id,registry,title_family,body_family)
            except (TextOverflowError,LayoutContractError) as e:
                attempts.append({'template_id':template_id,'status':'rejected','reason':str(e)});continue
            evidence.update(design_goal=self.goal,template_scores=scores,rejected_candidates=attempts,
                basis='A1 intended composition, reading order, typography and copy length only',
                actual_subject_positions='unknown',actual_contrast='unknown')
            return spec,evidence
        raise TextOverflowError('No planned template satisfies approved text: '+str(attempts))

    def _candidate(self,base,raw,template_id,registry,title_family,body_family):
        spec=copy.deepcopy(base);template=get_template(template_id)
        spec['template_id']=template_id
        background=background_color(template,spec['design_tokens']['palette']);spec['background']['color']=background
        titles=(self.series_title_size,) if self.goal=='cohesive_poster_series' else self.type.title_sizes
        bodies=(self.series_body_size,) if self.goal=='cohesive_poster_series' else self.type.body_sizes
        width=624;title=raw['copy']['headline'];subtitle=raw['copy'].get('subtitle','')
        size,leading,height,lines=fit_copy(title,family=title_family,weight=700,registry=registry,sizes=titles,width=width,max_lines=3,leading=self.type.title_leading,max_height=230)
        framed=template_id=='framed_hero_v1';bleed=template_id=='full_bleed_v1'
        y=64 if framed else (860 if bleed else 808)
        align='center' if framed else 'left'
        direction=raw.get('layout',{}).get('typography','').lower()
        if re.search(r'aligned to (?:the )?left|left.aligned',direction):align='left'
        elif re.search(r'aligned to (?:the )?center|center.aligned',direction):align='center'
        texts=[]
        color=slot_text_color(template.text_slots[0],background,spec['design_tokens']['palette'])
        def text(id,text,family,weight,size,leading,height,y,max_lines):
            return {'id':id,'type':'text','z':2 if id=='title' else 3,'box':{'x':48,'y':y,'width':width,'height':height},'copy_ref':id,'text':text,'font_family_key':family,'font_weight':weight,'font_size_px':size,'line_height_px':leading,'max_lines':max_lines,'align':align,'color':color}
        texts.append(text('title',title,title_family,700,size,leading,height,y,3))
        body_y=y+height+self.type.paragraph_gap
        body_lines=[]
        if subtitle:
            bs,bl,bh,body_lines=fit_copy(subtitle,family=body_family,weight=400,registry=registry,sizes=bodies,width=width,max_lines=4,leading=self.type.body_leading,max_height=min(170,1244-body_y))
            texts.append(text('subtitle',subtitle,body_family,400,bs,bl,bh,body_y,4))
        bottom=max(t['box']['y']+t['box']['height'] for t in texts)
        if bottom>1244:raise TextOverflowError('Copy leaves poster safe margin')
        hero=copy.deepcopy(base['elements'][0]);hero['focus_point_norm']=[.5,.5]
        if framed:hero['box']={'x':36,'y':max(300,bottom+40),'width':648,'height':1244-max(300,bottom+40)}
        elif bleed:hero['box']={'x':0,'y':0,'width':720,'height':1280}
        else:hero['box']={'x':0,'y':0,'width':720,'height':760}
        # A2 approves contain for multi-object evidence. A3 may not change fit.
        hero['fit']='contain' if not bleed else 'cover'
        spec['elements']=[hero]+texts
        spec['asset_plan'][0]['target_size']={k:hero['box'][k] for k in ('width','height')}
        spec['asset_plan'][0]['subject_safe_region_norm']=[.05,.05,.9,.9]
        spec['allowed_overlaps']=[['hero',t['id']] for t in texts] if bleed else []
        spec['design_tokens']['font_family_key']=title_family
        validate_poster_spec(spec)
        checks=check_layout_geometry(spec,registry=registry)
        if not checks['ok']:raise LayoutContractError(str(checks['issues']))
        return spec,{'typography_policy':self.type.__dict__,'title_lines':lines,'subtitle_lines':body_lines,
           'approved_fit':hero['fit'],'planned_subject_region':[.05,.05,.9,.9],
           'font_families':{'title':title_family,'subtitle':body_family},
           'unsupported_preferences':[x for x in ['shadow','fade'] if x in raw.get('layout',{}).get('typography','').lower()]}
