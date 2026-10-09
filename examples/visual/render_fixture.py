"""Offline renderer demonstration. The source artwork is procedural, not AI-generated."""
from pathlib import Path
import json, math
from PIL import Image, ImageDraw
from poster_visual_producer.contracts import PosterSpec
from poster_visual_producer.fonts import FontRegistry
from poster_visual_producer.renderer import render_poster
from poster_visual_producer.imaging import encode_png

ROOT=Path(__file__).resolve().parents[2]

def ocean_fixture(variant=0):
    w,h=900,1180
    im=Image.new("RGB",(w,h)); pixels=im.load()
    horizon=445
    for y in range(h):
        t=y/horizon if y<horizon else (y-horizon)/(h-horizon)
        start,end=((198,225,228),(234,236,222)) if y<horizon else ((95,156,170),(14,66,96))
        for x in range(w): pixels[x,y]=tuple(round(a+(b-a)*t) for a,b in zip(start,end))
    d=ImageDraw.Draw(im)
    d.ellipse((610,180,750,320),fill=(246,236,190))
    d.line((0,horizon,w,horizon),fill=(199,218,214),width=3)
    for j in range(16):
        base=510+j*40
        pts=[(x,base+int((5+j*0.7)*math.sin(x/135+j+variant*0.4))) for x in range(0,w,3)]
        d.line(pts,fill=tuple(min(255,v) for v in (76+j*3,143+j*2,158+j*2)),width=2)
    return encode_png(im)

def main():
    spec=PosterSpec.model_validate_json((ROOT/"examples/visual/spec.json").read_text(encoding="utf-8"))
    fonts=FontRegistry.from_config(json.loads((ROOT/"examples/visual/fonts.json").read_text(encoding="utf-8")),ROOT)
    out=ROOT/"runs/offline-render"; out.mkdir(parents=True,exist_ok=True)
    source=ocean_fixture(); (out/"source.png").write_bytes(source)
    result=render_poster(spec,{"hero_image":source},fonts)
    (out/"poster.png").write_bytes(result.png)
    result.manifest["source_mode"]="offline_procedural_fixture"
    (out/"render_manifest.json").write_text(json.dumps(result.manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"mode":"offline_procedural_fixture","poster":str(out/"poster.png"),"canvas":[720,1280],"sha256":result.manifest["output_sha256"]},ensure_ascii=False,indent=2))

if __name__=="__main__": main()
