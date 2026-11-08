"""Image facts and source-bound visual observations, never an automatic semantic judge."""
import hashlib,io
from PIL import Image,ImageStat

def pending_evidence(contract,source_data,manifest):
    technical=[]
    for asset_id,data in source_data.items():
        with Image.open(io.BytesIO(data)) as image:
            rgb=image.convert('RGB');std=ImageStat.Stat(rgb).stddev
            technical.append({'asset_id':asset_id,'source_sha256':hashlib.sha256(data).hexdigest(),
              'size':list(image.size),'channel_stddev':std,
              'blank_image_status':'fail' if max(std)<1 else 'pass',
              'semantic_status':'unknown'})
    return {'schema_version':1,'source_sha256':contract['contract']['source_sha256'],
       'spec_sha256':contract['spec_sha256'],'technical_checks':technical,
       'visual_checks':[{'requirement_id':r['requirement_id'],'priority':r['priority'],
         'source_status':'unknown','poster_status':'unknown','reason':'Actual pixels require inspection'}
         for r in contract['contract']['requirements'] if r['priority']=='required' and r['disposition']=='active'],
       'processing':[{'element_id':r['element_id'],'record':r['processing']} for r in manifest['elements'] if r['type']=='image'],
       'issues':[{'code':'VISUAL_EVIDENCE_PENDING','status':'unknown','route':'A2_replan_or_authorized_asset_rework'}],
       'layout_changed_by_a3':False}

def evaluate_observation(*,data,processing,observation,min_source_side=48,min_poster_side=24,thumbnail_scale=.375):
    sha=hashlib.sha256(data).hexdigest()
    if observation['source_sha256']!=sha:raise ValueError('Observation belongs to different source bytes')
    states=('pass','fail','unknown')
    if observation['source_status'] not in states or observation['poster_status'] not in states:raise ValueError('Invalid visual status')
    result=dict(observation);boxes=[]
    sw,sh=processing['original_size'];a,_,_,d,tx,ty=processing['raw_to_processed']
    pw,ph=processing['processed_size']
    for box in observation.get('object_boxes_norm',[]):
        x0,y0,x1,y1=box
        if not 0<=x0<x1<=1 or not 0<=y0<y1<=1:raise ValueError('Invalid normalized source bounding box')
        projected=[x0*sw*a+tx,y0*sh*d+ty,x1*sw*a+tx,y1*sh*d+ty]
        contained=projected[0]>=0 and projected[1]>=0 and projected[2]<=pw and projected[3]<=ph
        source_side=min((x1-x0)*sw,(y1-y0)*sh)
        poster_side=min(projected[2]-projected[0],projected[3]-projected[1])
        boxes.append({'source_box_norm':box,'projected_box':projected,'crop_safe':contained,
          'source_min_side_px':source_side,'poster_min_side_px':poster_side,'thumbnail_min_side_px':poster_side*thumbnail_scale,
          'size_status':'pass' if source_side>=min_source_side and poster_side>=min_poster_side else 'fail'})
    result['geometry_checks']=boxes
    if boxes and any(not b['crop_safe'] or b['size_status']=='fail' for b in boxes):result['poster_status']='fail'
    result['status']='fail' if 'fail' in (result['source_status'],result['poster_status']) else ('pass' if result['source_status']==result['poster_status']=='pass' else 'unknown')
    return result
