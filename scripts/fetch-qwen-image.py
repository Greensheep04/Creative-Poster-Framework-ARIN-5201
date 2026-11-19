"""Explicit fixed-revision Qwen download. Never imported or run by generation."""
import argparse,hashlib,json,re
from pathlib import Path
from poster_visual_producer.qwen_backend import validate_qwen_components,QWEN_REVISION

MODEL='Qwen/Qwen-Image'

def fetch_qwen_weights(model_dir,revision,*,api=None,snapshot=None):
    if not re.fullmatch(r'[a-f0-9]{40}',revision):raise ValueError('Use an exact 40-character Qwen revision')
    if api is None or snapshot is None:
        from huggingface_hub import HfApi,snapshot_download
        api=api or HfApi();snapshot=snapshot or snapshot_download
    root=Path(model_dir).resolve();root.mkdir(parents=True,exist_ok=True)
    info=api.model_info(repo_id=MODEL,revision=revision,files_metadata=True)
    if info.sha!=revision:raise ValueError('Official Qwen revision mismatch')
    rows=[r for r in info.siblings if r.rfilename in ('model_index.json','LICENSE','README.md') or r.rfilename.startswith(('scheduler/','tokenizer/','transformer/','vae/','text_encoder/')) and not r.rfilename.endswith(('.bin','.msgpack'))]
    names=[r.rfilename for r in rows]
    for name in names:
        if not (root/name).resolve().is_relative_to(root):raise ValueError('Unsafe model component path')
    # Hub cache handles resumed downloads. Only an explicit script invocation downloads weights.
    snapshot(repo_id=MODEL,revision=revision,local_dir=str(root),allow_patterns=names)
    files={}
    for row in rows:
        p=root/row.rfilename
        if not p.is_file():raise ValueError('Incomplete Qwen download')
        if row.size is not None and p.stat().st_size!=row.size:raise ValueError('Qwen component size mismatch')
        with p.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        lfs=row.lfs
        expected=lfs.get('sha256') if isinstance(lfs,dict) else getattr(lfs,'sha256',None)
        if expected:
            if digest!=expected:raise ValueError('Qwen component checksum mismatch')
        elif row.blob_id:
            h=hashlib.sha1();h.update(f'blob {p.stat().st_size}\0'.encode())
            with p.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
            if h.hexdigest()!=row.blob_id:raise ValueError('Qwen config checksum mismatch')
        else:raise ValueError('Official Qwen component lacks checksum identity')
        files[row.rfilename]=digest
    validate_qwen_components(root,files)
    result={'model_id':MODEL,'revision':revision,'files':files,'license':'Apache-2.0'}
    temp=root/'poster-model-manifest.json.tmp';temp.write_text(json.dumps(result,indent=2),encoding='utf-8');temp.replace(root/'poster-model-manifest.json')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir',type=Path,required=True);parser.add_argument('--revision',default=QWEN_REVISION)
    args=parser.parse_args()
    result=fetch_qwen_weights(args.model_dir,args.revision)
    print(json.dumps({'model_id':result['model_id'],'revision':result['revision'],'verified_files':len(result['files']),'manifest':str(args.model_dir/'poster-model-manifest.json')}))

if __name__=='__main__':main()
