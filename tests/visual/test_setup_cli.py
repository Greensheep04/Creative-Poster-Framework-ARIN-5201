import json,subprocess,sys
from pathlib import Path
import pytest
from tests.visual.test_cloud_backend import server

ROOT=Path(__file__).resolve().parents[2]

def command(*args):
    return subprocess.run([sys.executable,'-m','poster_visual_producer',*map(str,args)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8')

@pytest.mark.parametrize('kind',['sdxl','qwen_image','cloud_api'])
def test_setup_creates_selected_template_and_never_overwrites(tmp_path,kind):
    path=tmp_path/'config with spaces.json'
    result=command('setup','--backend',kind,'--output',path)
    assert result.returncode==0,result.stderr
    assert json.loads(path.read_text(encoding='utf-8'))['kind']==kind
    path.write_text('{"sentinel":true}',encoding='utf-8')
    assert command('setup','--backend',kind,'--output',path).returncode!=0
    assert json.loads(path.read_text(encoding='utf-8'))=={'sentinel':True}
    assert command('setup','--backend',kind,'--output',path,'--overwrite').returncode==0


def test_doctor_cloud_ready_without_network_call(tmp_path,monkeypatch):
    from poster_visual_producer.configuration import diagnose_backend
    monkeypatch.setenv('POSTER_TEST_KEY','secret')
    with server() as (endpoint,records):
        config={'kind':'cloud_api','provider':'test','model':'test-model','endpoint':endpoint,'api_key_env':'POSTER_TEST_KEY','sizes':[[1024,1536]]}
        result=diagnose_backend(config,fonts_config=ROOT/'examples/visual/fonts.json',font_base=ROOT,output_root=tmp_path/'output')
        assert result['ready'] and not records
        assert 'secret' not in json.dumps(result)


def test_doctor_reports_missing_key_and_rejects_inline_secret(tmp_path,monkeypatch):
    from poster_visual_producer.configuration import diagnose_backend
    monkeypatch.delenv('POSTER_TEST_KEY',raising=False)
    c={'kind':'cloud_api','provider':'test','model':'test-model','endpoint':'https://example.org/v1/images/generations','api_key_env':'POSTER_TEST_KEY'}
    result=diagnose_backend(c,fonts_config=None,font_base=ROOT,output_root=None)
    assert not result['ready'] and any(r['code']=='MISSING_CREDENTIAL' for r in result['checks'])
    c['api_key']='never-print-this-secret'
    result=diagnose_backend(c,fonts_config=None,font_base=ROOT,output_root=None)
    assert not result['ready'] and 'never-print-this-secret' not in json.dumps(result)


def test_cloud_diagnostics_do_not_import_gpu_packages(tmp_path,monkeypatch):
    monkeypatch.setenv('POSTER_TEST_KEY','secret')
    config=tmp_path/'cloud.json'
    config.write_text(json.dumps({'kind':'cloud_api','provider':'test','model':'test-model','endpoint':'https://example.org/v1/images/generations','api_key_env':'POSTER_TEST_KEY'}),encoding='utf-8')
    script='''import sys,importlib.abc,json
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,*args):
        if fullname.split('.')[0] in ('torch','diffusers','transformers'):raise ImportError('GPU packages unavailable')
sys.meta_path.insert(0,Block())
from poster_visual_producer.configuration import diagnose_backend
from pathlib import Path
print(json.dumps(diagnose_backend(json.loads(Path(sys.argv[1]).read_text()),fonts_config=None,font_base=Path('.'),output_root=None)))'''
    result=subprocess.run([sys.executable,'-c',script,str(config)],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['ready']


def test_doctor_cli_returns_nonzero_for_unconfigured_profile(tmp_path):
    path=tmp_path/'profile.json'
    assert command('setup','--backend','qwen_image','--output',path).returncode==0
    result=command('doctor','--backend-config',path)
    assert result.returncode==1,result.stderr
    payload=json.loads(result.stdout)
    assert not payload['ready'] and payload['checks']
