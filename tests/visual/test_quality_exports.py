import hashlib,io
from PIL import Image,ImageFont
from poster_visual_producer.exports import export_formal,export_comparison

def test_formal_export_unchanged_comparison_labels_are_external(tmp_path):
    stream=io.BytesIO();Image.new('RGB',(720,1280),'#123C55').save(stream,'PNG');png=stream.getvalue()
    formal=tmp_path/'formal.png';export_formal(png,formal)
    assert formal.read_bytes()==png
    output=tmp_path/'comparison.png'
    result=export_comparison([{'poster':formal,'label':'seed 42 | experiment 1','details':'prompt label outside poster'}],output,ImageFont.load_default(),columns=1,thumb_width=720)
    image=Image.open(output)
    assert image.crop((0,80,720,1360)).tobytes()==Image.open(formal).tobytes()
    assert result['poster_pixels_edited'] is False

def test_portable_bundle_contains_quality_runner_and_immutable_samples():
    import json
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    manifest=set((root/'scripts/package-manifest.txt').read_text(encoding='utf-8').splitlines())
    cases=json.loads((root/'examples/visual/quality-cases.json').read_text(encoding='utf-8'))
    assert {c['source'] for c in cases}<=manifest
    assert {'examples/visual/quality_regression.py','docs/quality-pipeline.md','src/poster_visual_producer/image_budget.py'}<=manifest
