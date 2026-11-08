from pathlib import Path
from types import SimpleNamespace
import pytest
from poster_visual_producer.prompt_validation import SDXLPromptValidator
from poster_visual_producer.contracts import StageError

def test_both_encoders_and_final_repair_additions(monkeypatch):
    import transformers
    class Tokens:
        model_max_length=77
        def __init__(self,extra):self.extra=extra
        def __call__(self,text,truncation):
            assert truncation is False
            return {'input_ids':list(range(len(text.split())+self.extra))}
    monkeypatch.setattr(transformers.CLIPTokenizer,'from_pretrained',lambda path,local_files_only:Tokens(2 if path.endswith('tokenizer') else 3))
    v=SDXLPromptValidator(Path('local-only'))
    assert v.count('one two')==[4,5]
    request=SimpleNamespace(prompt='word '*74,negative_prompt='logo')
    v(request)
    request.prompt+=' Approved asset repair constraints: foreground object'
    with pytest.raises(StageError,match='tokenizer'):v(request)
    request.prompt='short';request.negative_prompt='negative '*75
    with pytest.raises(StageError):v(request)

def test_registered_latin_font_preserves_curly_apostrophe():
    import os
    from poster_visual_producer.fonts import FontRegistry
    from poster_visual_producer.contracts import TextElement
    root=Path(os.environ.get('WINDIR',''))/'Fonts'
    if not (root/'timesbd.ttf').is_file():pytest.skip('Native Latin font not installed')
    f=FontRegistry.from_config({'latin':{'700':{'path':str(root/'timesbd.ttf')}}},Path.cwd())
    e=TextElement(id='title',type='text',z=2,box={'x':48,'y':48,'width':624,'height':200},copy_ref='title',text='It’s remembered.',font_family_key='latin',font_weight=700,font_size_px=64,line_height_px=76,max_lines=2,align='left',color='#123C55')
    font,_=f.resolve(e);metrics=f.glyph_metrics(e)
    assert metrics['curly_apostrophe_advance_px']<32
    assert e.text=='It’s remembered.'
    assert font.getlength('It’s')-font.getlength("It's")<16
