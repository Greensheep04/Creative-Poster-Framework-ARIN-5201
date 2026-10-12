import asyncio,copy,hashlib,io,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from poster_visual_producer.generators import compile_request
from tests.visual.helpers import make_session,call_context,fonts
from poster_visual_producer.agent import VisualProducerAgent


def config(tmp_path,**values):
    from poster_visual_producer.qwen_backend import QwenImageConfig
    options={'model_dir':tmp_path,'revision':'a'*40,'steps':12};options.update(values)
    return QwenImageConfig(**options)


def weight_files(root):
    root.mkdir(parents=True,exist_ok=True)
    contents={'model_index.json':json.dumps({'_class_name':'QwenImagePipeline','scheduler':['diffusers','Scheduler'],'tokenizer':['transformers','Tokenizer'],'transformer':['diffusers','Model'],'vae':['diffusers','VAE'],'text_encoder':['transformers','Encoder']}).encode(),
        'LICENSE':b'test license','scheduler/scheduler_config.json':b'{}','tokenizer/tokenizer_config.json':b'{}','tokenizer/vocab.json':b'{}','tokenizer/merges.txt':b'test',
        'transformer/config.json':b'{}','transformer/diffusion_pytorch_model.safetensors':b'weights',
        'text_encoder/config.json':b'{}','text_encoder/model.safetensors':b'weights','vae/config.json':b'{}','vae/diffusion_pytorch_model.safetensors':b'weights'}
    for name,data in contents.items():
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    return contents


def manifest(root,contents):
    (root/'poster-model-manifest.json').write_text(json.dumps({'model_id':'Qwen/Qwen-Image','revision':'a'*40,'files':{n:hashlib.sha256(d).hexdigest() for n,d in contents.items()}}),encoding='utf-8')


def prepared(tmp_path,**values):
    from poster_visual_producer.qwen_backend import LocalQwenImageGenerator
    gen=LocalQwenImageGenerator(config(tmp_path,**values));seen={}
    class Seed:
        def manual_seed(self,seed):self.seed=seed;return self
    class Scope:
        def __enter__(self):return self
        def __exit__(self,*args):pass
    class Pipe:
        def __call__(self,**kwargs):
            seen.update(kwargs)
            return SimpleNamespace(images=[Image.new('RGB',(kwargs['width'],kwargs['height']),'green')])
    gen._pipeline=Pipe();gen._torch=SimpleNamespace(Generator=lambda device:Seed(),inference_mode=Scope,__version__='test')
    return gen,seen


def test_qwen_actual_arguments_seed_cfg_and_png(tmp_path,spec_data):
    gen,seen=prepared(tmp_path)
    request=compile_request(spec_data['asset_plan'][0],gen.capabilities,seed=9)
    result=asyncio.run(gen.generate(request))
    assert seen['generator'].seed==9 and seen['true_cfg_scale']==4.0
    assert seen['negative_prompt']==request.negative_prompt and 'guidance_scale' not in seen
    assert seen['num_inference_steps']==12
    assert Image.open(io.BytesIO(result.data)).size==request.size
    assert result.provenance['model_id']=='Qwen/Qwen-Image'


def test_qwen_sampling_change_invalidates_cache(tmp_path,spec_data,brief_data):
    session,spec,_=make_session(tmp_path,spec_data,brief_data)
    for name,cfg in [('first',4.0),('second',5.0)]:
        gen,_=prepared(tmp_path,true_cfg_scale=cfg)
        inputs,ctx=call_context(session,spec,name)
        asyncio.run(VisualProducerAgent(gen,fonts(),seed=9).run(inputs,ctx))
    assert session.usage.image_operations==2


@pytest.mark.parametrize('bad',['missing','revision','hash','shard','class'])
def test_qwen_weights_must_be_complete_and_versioned(tmp_path,bad):
    from poster_visual_producer.qwen_backend import verify_qwen_weights
    contents=weight_files(tmp_path)
    if bad=='missing':contents.pop('tokenizer/merges.txt')
    if bad=='shard':
        contents.pop('transformer/diffusion_pytorch_model.safetensors')
        contents['transformer/diffusion_pytorch_model.safetensors.index.json']=json.dumps({'weight_map':{'x':'missing-00001.safetensors'}}).encode()
        (tmp_path/'transformer/diffusion_pytorch_model.safetensors.index.json').write_bytes(contents['transformer/diffusion_pytorch_model.safetensors.index.json'])
    if bad=='class':
        contents['model_index.json']=b'{"_class_name":"AnotherPipeline"}'
        (tmp_path/'model_index.json').write_bytes(contents['model_index.json'])
    manifest(tmp_path,contents)
    if bad=='hash':(tmp_path/'vae/config.json').write_bytes(b'bad')
    c=config(tmp_path)
    if bad=='revision':c=c.model_copy(update={'revision':'b'*40})
    with pytest.raises(ValueError):verify_qwen_weights(c)


def test_qwen_verified_weights_and_missing_weights(tmp_path,spec_data):
    from poster_visual_producer.qwen_backend import verify_qwen_weights,LocalQwenImageGenerator
    contents=weight_files(tmp_path);manifest(tmp_path,contents)
    verify_qwen_weights(config(tmp_path))
    g=LocalQwenImageGenerator(config(tmp_path/'absent'))
    with pytest.raises(ValueError,match='[Mm]odel|[Ww]eight'):asyncio.run(g.generate(compile_request(spec_data['asset_plan'][0],g.capabilities)))


def test_qwen_loader_local_only_dtype_and_offload(tmp_path,monkeypatch):
    import sys
    from poster_visual_producer.qwen_backend import LocalQwenImageGenerator
    contents=weight_files(tmp_path);manifest(tmp_path,contents)
    seen={}
    class Pipe:
        @classmethod
        def from_pretrained(cls,path,**kwargs):seen.update(kwargs);return cls()
        def enable_model_cpu_offload(self):seen['offload']=True
        def enable_vae_slicing(self):pass
    torch=SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:True,is_bf16_supported=lambda:True),bfloat16='bf16',float16='fp16')
    monkeypatch.setitem(sys.modules,'torch',torch);monkeypatch.setitem(sys.modules,'diffusers',SimpleNamespace(QwenImagePipeline=Pipe))
    gen=LocalQwenImageGenerator(config(tmp_path,cpu_offload=True));gen._load()
    assert seen['local_files_only'] is True and seen['torch_dtype']=='bf16' and seen['offload']


def test_qwen_invalid_sampling_is_rejected(tmp_path):
    for values in ({'true_cfg_scale':1},{'dtype':'float32'},{'steps':0},{'true_cfg_scale':float('nan')}):
        with pytest.raises(ValueError):config(tmp_path,**values)
