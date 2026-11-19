import copy,json
from pathlib import Path
import pytest
from poster_framework import OriginalRequest
from poster_framework.quality_layout import QualityPlanningPolicy,TypographyPolicy,score_templates
from poster_framework.layout_fonts import FontFace,FontRegistry
from tests.visual.helpers import fonts
ROOT=Path(__file__).resolve().parents[2]
def inputs():
    raw=json.loads((ROOT/'examples/creative-director-samples/Qwen3-4B-Instruct-2507/urban-loneliness-connection-windows/human-story.json').read_text(encoding='utf-8'))
    f=fonts();registry=FontRegistry({(k,w):FontFace(k,w,v.path,v.index,'test') for k,weights in f.families.items() for w,v in weights.items()})
    return raw,OriginalRequest.from_keywords('layoutquality',raw['keywords']),registry

def test_template_decision_uses_intended_composition():
    raw,req,registry=inputs()
    assert score_templates(raw)[0]['template_id']=='framed_hero_v1'
    raw['layout']['typography']='Serif headline'
    raw['layout']['composition']='Horizontal split, left to right'
    assert score_templates(raw)[0]['template_id']=='split_stack_v1'

def test_independent_default_empty_subtitle_and_exact_punctuation():
    raw,req,registry=inputs();before=copy.deepcopy(raw)
    spec,e=QualityPlanningPolicy().plan(raw,req,registry,'noto_sans_sc')
    assert e['design_goal']=='independent_concept_exploration'
    assert e['actual_subject_positions']=='unknown'
    assert not any(t.get('copy_ref')=='subtitle' for t in spec['elements'])
    assert spec['elements'][1]['text']==raw['copy']['headline']
    assert '’' in spec['elements'][1]['text'] and before==raw

def test_series_keeps_typography_and_template_consistent():
    raw,req,registry=inputs();p=QualityPlanningPolicy(design_goal='cohesive_poster_series',series_title_size=44)
    a,_=p.plan(raw,req,registry,'noto_sans_sc')
    raw['copy']['headline']='A shorter title';b,_=p.plan(raw,req,registry,'noto_sans_sc')
    assert a['template_id']==b['template_id']=='split_stack_v1'
    assert a['elements'][1]['font_size_px']==b['elements'][1]['font_size_px']==44

def test_gap_and_leading_are_adjustable_strategies():
    raw,req,registry=inputs();raw['copy']['subtitle']='A quiet connection.'
    spec,e=QualityPlanningPolicy(typography=TypographyPolicy(paragraph_gap=36)).plan(raw,req,registry,'noto_sans_sc')
    title,body=spec['elements'][1:]
    assert body['box']['y']-title['box']['y']-title['box']['height']==36
    assert title['font_size_px'] in TypographyPolicy().title_sizes
    assert body['line_height_px']>body['font_size_px']


@pytest.mark.parametrize('relative',[
 'home-migration-memory/message-led.json','repair-community-tomorrow/human-story.json',
 'time-consumption-ocean/human-story.json','urban-loneliness-connection-windows/human-story.json',
 'home-migration-memory/visual-metaphor.json','time-consumption-ocean/visual-metaphor.json'])
def test_six_sources_fit_both_a2_and_a3_without_rewriting(relative):
    from poster_visual_producer.renderer import preflight
    from poster_visual_producer.contracts import PosterSpec
    _,_,registry=inputs()
    raw=json.loads((ROOT/'examples/creative-director-samples/Qwen3-4B-Instruct-2507'/relative).read_text(encoding='utf-8'))
    req=OriginalRequest.from_keywords('six-layout',raw['keywords'])
    spec,e=QualityPlanningPolicy().plan(raw,req,registry,'noto_sans_sc')
    measured=preflight(PosterSpec.model_validate(spec),fonts())
    assert measured['title'].element.text==raw['copy']['headline']
    assert ('subtitle' in measured)==bool(raw['copy']['subtitle'])
    assert e['actual_subject_positions']=='unknown'
