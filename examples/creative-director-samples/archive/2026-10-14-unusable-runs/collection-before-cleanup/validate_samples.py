#!/usr/bin/env python3
"""Validate captured samples without modifying any request or model output.

The checker implements the subset of JSON Schema used by schema.json:
object, array and string types; required keys; properties; additionalProperties.
Run from anywhere. --write-report saves validation-report.json and .md.
"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def validate(value, schema, at='$'):
    errors=[]
    typ=schema.get('type')
    if typ=='object':
        if not isinstance(value,dict): return [f'{at}: expected object']
        props=schema.get('properties',{})
        for key in schema.get('required',[]):
            if key not in value: errors.append(f'{at}.{key}: missing required property')
        if schema.get('additionalProperties') is False:
            for key in value.keys()-props.keys(): errors.append(f'{at}.{key}: unexpected property')
        for key in value.keys() & props.keys(): errors.extend(validate(value[key],props[key],f'{at}.{key}'))
    elif typ=='array':
        if not isinstance(value,list): return [f'{at}: expected array']
        for i,item in enumerate(value): errors.extend(validate(item,schema['items'],f'{at}[{i}]'))
    elif typ=='string':
        if not isinstance(value,str): errors.append(f'{at}: expected string')
    elif typ is not None: raise ValueError(f'Unsupported schema type: {typ}')
    return sorted(errors)


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def inspect(root):
    plan=load(root/'run-plan.json')
    schema=load(root/'schema.json')
    runs=[]
    for planned in plan['runs']:
        model_slug=planned['model'].split('/')[-1].lower()
        base=root/'outputs'/planned['variant']/planned['keyword_id']/model_slug
        paths={kind:Path(str(base)+suffix) for kind,suffix in {
            'raw':'.raw.txt','parsed':'.json','metadata':'.metadata.json','request':'.request.txt'}.items()}
        record={**planned,'status':'pending','json_parse_valid':None,'schema_valid':None,'keywords_exact':None,'files':{kind:str(p.relative_to(root)) for kind,p in paths.items() if p.exists()},'errors':[]}
        metadata={}
        if paths['metadata'].exists():
            try: metadata=load(paths['metadata'])
            except (ValueError,UnicodeError) as exc: record['errors'].append(f'Metadata parse error: {exc}')
            if isinstance(metadata,dict):
                record['reported_status']=metadata.get('status',metadata.get('outcome'))
                record['metadata']=metadata
        raw_data=None
        raw_parsed=False
        if paths['raw'].exists():
            raw=paths['raw'].read_text(encoding='utf-8')
            record['raw_sha256']=hashlib.sha256(paths['raw'].read_bytes()).hexdigest()
            record['raw_characters']=len(raw)
            if not raw.strip():
                record['status']='empty_response'
                record['errors'].append('Raw response is empty.')
            else:
                try:
                    raw_data=json.loads(raw)
                    raw_parsed=True
                    record['json_parse_valid']=True
                    expected_keywords=next(k['keywords'] for k in load(root/'keywords.json') if k['id']==planned['keyword_id'])
                    record['keywords_exact']=isinstance(raw_data,dict) and raw_data.get('keywords')==expected_keywords
                    schema_errors=validate(raw_data,schema)
                    record['errors'].extend(schema_errors)
                    record['schema_valid']=not schema_errors
                    if not record['keywords_exact']: record['errors'].append('Output keywords do not exactly match the input keywords and their order.')
                    record['status']='valid' if not schema_errors else 'schema_invalid'
                except ValueError as exc:
                    record['status']='invalid_json'
                    record['json_parse_valid']=False
                    record['errors'].append(f'Raw response is not standalone JSON: {exc}')
        elif paths['parsed'].exists():
            record['status']='missing_raw_response'
            record['errors'].append('Parsed output exists without raw response.')
        elif paths['metadata'].exists():
            record['status']='recorded_without_output'
        if paths['parsed'].exists():
            try:
                parsed=load(paths['parsed'])
                record['parsed_schema_errors']=validate(parsed,schema)
                if raw_parsed and parsed!=raw_data:
                    record['errors'].append('Parsed JSON differs from raw response JSON.')
            except (ValueError,UnicodeError) as exc: record['errors'].append(f'Parsed output cannot be read: {exc}')
        if paths['raw'].exists() and not paths['metadata'].exists(): record['errors'].append('Missing metadata.')
        if paths['raw'].exists() and not paths['request'].exists(): record['errors'].append('Missing actual request text.')
        if paths['request'].exists():
            request=paths['request'].read_text(encoding='utf-8')
            input_messages=load(root/planned['input'])['messages']
            expected='\n\n'.join(m['content'] for m in input_messages)
            if request==expected or request==expected+'\n':
                record['request_mode']='combined_user_message_multiline'
            elif request==expected.replace('\n',' ') or request==expected.replace('\n',' ')+'\n':
                record['request_mode']='combined_user_message_each_newline_replaced_with_one_space'
            else:
                record['request_mode']='unrecognized_adaptation'
            record['request_matches_combined_input']=record['request_mode']!='unrecognized_adaptation'
            if not record['request_matches_combined_input']: record['errors'].append('Actual request differs from both the multiline combined input and the approved newline-to-space adaptation; inspect metadata.')
        if record['status']=='pending' and plan.get('model_statuses',{}).get(planned['model'])=='paused_by_user':
            record['status']='paused_by_user'
        runs.append(record)
    archives=[]
    for manifest_path in sorted((root/'archive').glob('*/archive-manifest.json')):
        manifest=load(manifest_path)
        for item in manifest['files']:
            path=manifest_path.parent/item['archived_path']
            ok=path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
            archives.append({'path':str(path.relative_to(root)),'hash_matches':ok})
    return {'planned_runs':len(runs),'active_target_runs':plan.get('active_target_runs',len(runs)),'counts':dict(Counter(r['status'] for r in runs)),
            'json_parse_valid_count':sum(r['json_parse_valid'] is True for r in runs),'schema_valid_count':sum(r['schema_valid'] is True for r in runs),'exact_keywords_count':sum(r['keywords_exact'] is True for r in runs),'archive_files_checked':len(archives),'archive_hashes_valid':all(a['hash_matches'] for a in archives),
            'notes':['Only saved evidence is inspected; pending or metadata-only runs are not model outputs.',
                     'Schema validation does not evaluate meaning, quality, or factual accuracy.',
                     'Raw text is parsed as-is; code fences and reasoning are not stripped.'],
            'runs':runs,'archives':archives}


def markdown(report):
    lines=['# Creative Director collection validation','',
           f"Original planned runs: {report['planned_runs']}; active target: {report.get('active_target_runs',report['planned_runs'])}. Archived files verified: {report['archive_files_checked']}. Archive hashes match: {report['archive_hashes_valid']}.",'',
           '| Status | Count |','| --- | ---: |']
    lines += [f'| {status} | {count} |' for status,count in sorted(report['counts'].items())]
    lines += ['', 'This report checks saved files only. A metadata-only failure or pending run is not a collected model response. Valid JSON/schema does not establish creative quality.', '',
              '| Prompt | Keywords | Model | Status | JSON parse | Schema | Exact keywords | Issues |','| --- | --- | --- | --- | --- | --- | --- | --- |']
    for run in report['runs']:
        issues='; '.join(run['errors']).replace('|','\\|').replace('\n',' ')
        lines.append(f"| {run['variant']} | {run['keyword_id']} | {run['model']} | {run['status']} | {run['json_parse_valid']} | {run['schema_valid']} | {run['keywords_exact']} | {issues} |")
    return '\n'.join(lines)+'\n'


def comparisons(root,report):
    """Quote completed model output fields; never treat failed attempts as outputs."""
    def cell(value):
        return str(value).replace('|','\\|').replace('\n',' ')
    for variant in ['message-led','visual-metaphor','human-story']:
        lines=[f'# {variant}: collected poster concepts','',
               'The table quotes fields from saved model responses. It does not rank quality. Failed attempts and pending runs are excluded. Full requests, raw responses, and metadata remain under `outputs/`.','']
        completed=[r for r in report['runs'] if r['variant']==variant and r['json_parse_valid'] is True and 'raw' in r['files']]
        lines.append(f'{len(completed)} parseable responses collected across the original 15-run variant inventory. See collection-status.json for active targets and paused models.\n')
        for keyword in load(root/'keywords.json'):
            selected=[r for r in completed if r['keyword_id']==keyword['id']]
            if not selected: continue
            lines += ['## '+', '.join(keyword['keywords']),'',
                      '| Model | Headline | Core message | Visual relationship | Schema / exact keywords |',
                      '| --- | --- | --- | --- | --- |']
            for run in selected:
                data=load(root/run['files']['raw'])
                if not isinstance(data,dict): continue
                copy=data.get('copy',{}) if isinstance(data.get('copy'),dict) else {}
                visual=data.get('visual_direction',{}) if isinstance(data.get('visual_direction'),dict) else {}
                lines.append('| '+' | '.join(map(cell,[run['model'],copy.get('headline',''),data.get('core_message',''),visual.get('visual_relationship',''),str(run['schema_valid'])+' / '+str(run['keywords_exact'])]))+' |')
            lines.append('')
        destination=root/'comparisons'/f'{variant}.md'
        destination.parent.mkdir(exist_ok=True)
        destination.write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',nargs='?',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--write-report',action='store_true')
    parser.add_argument('--write-comparisons',action='store_true')
    args=parser.parse_args()
    report=inspect(args.root)
    if args.write_report:
        (args.root/'validation-report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        (args.root/'validation-report.md').write_text(markdown(report),encoding='utf-8')
    if args.write_comparisons: comparisons(args.root,report)
    print(json.dumps({k:v for k,v in report.items() if k not in ['runs','archives']},indent=2))


if __name__=='__main__': main()
