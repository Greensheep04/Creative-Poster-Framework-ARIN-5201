import hashlib,importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
from tests.visual.test_qwen_backend import weight_files


def module():
    path=Path(__file__).resolve().parents[2]/'scripts/fetch-qwen-image.py'
    assert path.exists(),'explicit Qwen downloader required'
    spec=importlib.util.spec_from_file_location('fetch_qwen_test',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m


def test_qwen_download_uses_pinned_official_identity_and_verifies_files(tmp_path):
    m=module();root=tmp_path/'model with spaces';contents=weight_files(root)
    class API:
        def model_info(self,repo_id,revision,files_metadata):
            assert repo_id=='Qwen/Qwen-Image' and revision=='a'*40 and files_metadata
            return SimpleNamespace(sha='a'*40,siblings=[SimpleNamespace(rfilename=n,size=len(d),lfs={'sha256':hashlib.sha256(d).hexdigest()},blob_id=None) for n,d in contents.items()])
    seen={}
    def snapshot(**kwargs):seen.update(kwargs);return str(root)
    result=m.fetch_qwen_weights(root,'a'*40,api=API(),snapshot=snapshot)
    assert result['revision']=='a'*40 and len(result['files'])==len(contents)
    assert seen['revision']=='a'*40 and Path(seen['local_dir'])==root
    assert (root/'poster-model-manifest.json').is_file()


def test_qwen_download_cannot_publish_corrupt_or_incomplete_manifest(tmp_path):
    m=module();contents=weight_files(tmp_path)
    class API:
        def model_info(self,**kwargs):return SimpleNamespace(sha='a'*40,siblings=[SimpleNamespace(rfilename=n,size=len(d),lfs={'sha256':'0'*64},blob_id=None) for n,d in contents.items()])
    with pytest.raises(ValueError):m.fetch_qwen_weights(tmp_path,'a'*40,api=API(),snapshot=lambda **k:str(tmp_path))
    assert not (tmp_path/'poster-model-manifest.json').exists()
