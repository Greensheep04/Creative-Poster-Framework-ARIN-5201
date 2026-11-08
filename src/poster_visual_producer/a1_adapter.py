"""Lossless replay adapter; original source bytes are always a separate artifact."""
import hashlib,json
def adapt_a1(source_bytes,request,source_name):
    raw=json.loads(source_bytes)
    copy={key:{'text':raw['copy'][field],'origin':'a1.copy.'+field,'must_preserve':True}
          for key,field in [('title','headline'),('subtitle','subtitle'),('cta','call_to_action')]
          if raw['copy'].get(field)}
    return {'schema_version':'0.1.0','run_id':request.run_id,'brief_version':1,'request_ref':request.request_id,
      'intent':{'concept':raw['concept']['central_idea'],'core_message':raw['core_message'],'visual_style':raw['visual_direction']['style']},
      'keyword_bindings':[{'keyword_id':k.keyword_id,'interpretation':raw['concept']['connection_to_keywords'],
          'visual_evidence':[raw['visual_direction']['main_subject']],'text_alone_sufficient':False} for k in request.keywords],
      'copy':copy,'visual_requirements':raw['visual_direction'],'layout_intent':raw['layout'],'assumptions':[],
      'reference_notes':[{'source_file':source_name,'source_sha256':hashlib.sha256(source_bytes).hexdigest(),
        'original_a1':raw,'mapping':'Exact approved copy; lossless original source retained'}],'open_issues':[]}
