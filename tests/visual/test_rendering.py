import importlib.util, io
from pathlib import Path
import pytest
from PIL import Image
ROOT = Path(__file__).resolve().parents[2]

def render_api():
    assert importlib.util.find_spec("poster_visual_producer.renderer"), "deterministic renderer is required"
    from poster_visual_producer.renderer import render_poster
    from poster_visual_producer.fonts import FontRegistry, FontFace
    from poster_visual_producer.contracts import PosterSpec, StageError
    from poster_visual_producer.imaging import prepare_asset, decode_image
    fonts = FontRegistry({"noto_sans_sc": {
        400: FontFace(ROOT/"resources/fonts/NotoSansCJKsc-Regular.otf"),
        700: FontFace(ROOT/"resources/fonts/NotoSansCJKsc-Bold.otf")}})
    return render_poster, fonts, PosterSpec, StageError, prepare_asset, decode_image

def source(size=(648,850), color="#236890"):
    b=io.BytesIO(); Image.new("RGB",size,color).save(b,"PNG"); return b.getvalue()

def test_png_pixels_and_manifest_match_spec(spec_data):
    render, fonts, Spec, _, _, _ = render_api()
    result=render(Spec.model_validate(spec_data), {"hero_image":source()}, fonts)
    image=Image.open(io.BytesIO(result.png))
    assert image.size == (720,1280) and image.mode == "RGB"
    assert image.getpixel((0,0)) == (246,250,251)
    assert image.getpixel((40,270)) == (35,104,144)
    assert result.manifest["elements"][1]["lines"] == ["静海之蓝"]
    assert len(result.manifest["elements"][1]["font_sha256"]) == 64
    assert result.png == render(Spec.model_validate(spec_data), {"hero_image":source()}, fonts).png

@pytest.mark.parametrize("change,code",[("small","TEXT_OVERFLOW"),("glyph","MISSING_GLYPH"),("font","MISSING_FONT")])
def test_text_errors_are_not_silently_changed(spec_data, change, code):
    render,fonts,Spec,Error,_,_=render_api()
    text=spec_data["elements"][1]
    if change=="small": text["box"]["width"]=10
    if change=="glyph": text["text"]="\U0010ffff"
    if change=="font": text["font_family_key"]="unregistered"
    with pytest.raises(Error) as caught: render(Spec.model_validate(spec_data),{"hero_image":source()},fonts)
    assert caught.value.code==code

def test_english_wrap_preserves_characters(spec_data):
    render,fonts,Spec,_,_,_=render_api()
    title=spec_data["elements"][1]
    title.update(text="Ocean serenity and blue",font_size_px=28,line_height_px=38,max_lines=3)
    title["box"].update(width=280,height=152)
    result=render(Spec.model_validate(spec_data),{"hero_image":source()},fonts)
    lines=result.manifest["elements"][1]["lines"]
    assert len(lines)>1 and "".join(lines)=="Ocean serenity and blue"

@pytest.mark.parametrize("fit,size,crop",[("cover",(100,100),[50.0,0.0,150.0,100.0]),("contain",(100,100),None)])
def test_aspect_ratio_processing(fit,size,crop,spec_data):
    _,_,Spec,_,prepare,_=render_api()
    e=spec_data["elements"][0]; e.update(fit=fit); e["box"].update(width=100,height=100)
    spec_data["asset_plan"][0]["target_size"]={"width":100,"height":100}
    p=prepare(source((200,100)),Spec.model_validate(spec_data).elements[0])
    assert p.image.size==size and p.record["crop_box"]==crop
    if fit=="contain": assert p.image.getpixel((50,0))[3]==0

def test_corrupt_oversized_and_wrong_format_are_rejected():
    *_, Error, prepare, decode = render_api()
    with pytest.raises(Error): decode(b"not an image")
    with pytest.raises(Error): decode(source((200,100)),max_pixels=1000)
    b=io.BytesIO(); Image.new("RGB",(20,20)).save(b,"GIF")
    with pytest.raises(Error): decode(b.getvalue())

def test_real_weight_file_is_required(spec_data):
    render,fonts,Spec,Error,_,_=render_api()
    spec_data["elements"][1]["font_weight"]=500
    with pytest.raises(Error) as caught: render(Spec.model_validate(spec_data),{"hero_image":source()},fonts)
    assert caught.value.code=="MISSING_FONT"
