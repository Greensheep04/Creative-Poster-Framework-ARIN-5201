"""Formal pixels and labeled experiments have separate export paths."""
import hashlib,io
from pathlib import Path
from PIL import Image,ImageDraw

def export_formal(png,path):
    with Image.open(io.BytesIO(png)) as image:
        if image.format!='PNG' or image.size!=(720,1280):raise ValueError('Expected formal 720x1280 PNG')
    Path(path).write_bytes(png)
    return {'path':str(Path(path).resolve()),'sha256':hashlib.sha256(png).hexdigest(),'kind':'formal_poster','labels_added':False}

def export_comparison(rows,path,font,*,columns=3,thumb_width=360):
    thumb_height=thumb_width*1280//720;band=80
    canvas=Image.new('RGB',(columns*thumb_width,((len(rows)+columns-1)//columns)*(thumb_height+band)),'#e8edf1')
    draw=ImageDraw.Draw(canvas)
    for i,row in enumerate(rows):
        x=(i%columns)*thumb_width;y=(i//columns)*(thumb_height+band)
        draw.text((x+10,y+8),row['label'],font=font,fill='#172d3c')
        draw.text((x+10,y+37),row.get('details',''),font=font,fill='#172d3c')
        with Image.open(row['poster']) as image:canvas.paste(image.convert('RGB').resize((thumb_width,thumb_height),Image.Resampling.LANCZOS),(x,y+band))
    canvas.save(path,'PNG')
    return {'path':str(Path(path).resolve()),'kind':'experimental_comparison','label_band_height':band,'poster_pixels_edited':False}
