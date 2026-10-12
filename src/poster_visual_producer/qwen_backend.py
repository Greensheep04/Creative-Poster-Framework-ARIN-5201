"""Optional original Qwen-Image generation; weights are never downloaded during inference."""
import asyncio,hashlib,json,secrets,threading,time
from pathlib import Path
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel,ConfigDict,Field,field_validator
from .contracts import StageError
from .generators import GeneratorCapabilities,GeneratedImage,ProviderError
from .imaging import encode_png

QWEN_REVISION='75e0b4be04f60ec59a75f475837eced720f823b6'

class QwenImageConfig(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    model_dir:Path
    model_id:Literal['Qwen/Qwen-Image']='Qwen/Qwen-Image'
    revision:str=Field(pattern=r'^[a-f0-9]{40}$')
    steps:int=Field(default=30,strict=True,ge=1,le=100)
    true_cfg_scale:float=Field(default=4.0,gt=1,le=20,allow_inf_nan=False)
    dtype:Literal['float16','bfloat16']='bfloat16'
    cpu_offload:bool=Field(default=False,strict=True)
    sizes:tuple[tuple[int,int],...]=((768,1024),(1024,1024),(1024,768))
    @field_validator('sizes')
    @classmethod
    def valid_sizes(cls,v):
        if not v or any(type(n)!=int or n<=0 or n>8192 or n%32 for pair in v for n in pair):
            raise ValueError('Qwen profile sizes must be positive multiples of 32')
        return v


def validate_qwen_components(root,names):
    root=Path(root).resolve();names=set(names)
    required={'model_index.json','LICENSE','scheduler/scheduler_config.json','tokenizer/tokenizer_config.json','tokenizer/vocab.json','tokenizer/merges.txt'}
    if 'model_index.json' not in names:raise StageError('MISSING_MODEL','Qwen model_index.json missing')
    index=json.loads((root/'model_index.json').read_text(encoding='utf-8'))
    if index.get('_class_name')!='QwenImagePipeline' or not {'scheduler','tokenizer','transformer','vae','text_encoder'}<=set(index):
        raise StageError('MISSING_MODEL','Incorrect/incomplete Qwen pipeline identity')
    for component,weight_name in [('transformer','diffusion_pytorch_model'),('vae','diffusion_pytorch_model'),('text_encoder','model')]:
        required.add(component+'/config.json')
        shard_index=component+'/'+weight_name+'.safetensors.index.json'
        if shard_index in names:
            shards=json.loads((root/shard_index).read_text(encoding='utf-8')).get('weight_map')
            if not isinstance(shards,dict) or not shards:raise StageError('MISSING_MODEL','Incomplete Qwen shard index')
            required.add(shard_index)
            for name in shards.values():
                if not isinstance(name,str) or Path(name).name!=name or not name.endswith('.safetensors'):
                    raise StageError('MISSING_MODEL','Invalid Qwen shard path')
                required.add(component+'/'+name)
        else:required.add(component+'/'+weight_name+'.safetensors')
    if not required<=names or any(not (root/name).is_file() for name in required):
        raise StageError('MISSING_MODEL','Incomplete Qwen components/shards')


def verify_qwen_weights(config):
    root=config.model_dir.resolve()
    path=root/'poster-model-manifest.json'
    if not path.is_file():raise StageError('MISSING_MODEL','Qwen weights manifest missing; run explicit fetch-qwen-image.py')
    manifest=json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('model_id')!=config.model_id or manifest.get('revision')!=config.revision:
        raise StageError('STALE_VERSION','Qwen model identity mismatch')
    files=manifest.get('files')
    if not isinstance(files,dict) or not files:raise StageError('MISSING_MODEL','Incomplete Qwen model manifest')
    for name,digest in files.items():
        path=(root/name).resolve()
        if not path.is_relative_to(root) or not path.is_file():raise StageError('MISSING_MODEL','Invalid Qwen component path')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=digest:raise StageError('INVALID_ASSET','Qwen component hash mismatch')
    validate_qwen_components(root,files)

class LocalQwenImageGenerator:
    def __init__(self,config):
        self.config=config
        self.capabilities=GeneratorCapabilities(provider='local_diffusers',model=config.model_id,sizes=config.sizes,
            supports_seed=True,supports_negative_prompt=True,weights_version=config.revision,
            generation_parameters=(('steps',config.steps),('true_cfg_scale',config.true_cfg_scale),('dtype',config.dtype),('cpu_offload',config.cpu_offload),('pipeline','qwen-image-v1')))
        self._pipeline=None;self._torch=None;self._lock=threading.RLock()

    def _load(self):
        verify_qwen_weights(self.config)
        try:
            import torch
            from diffusers import QwenImagePipeline
        except ImportError as error:raise StageError('MISSING_DEPENDENCY','Install the locked local-gpu dependencies for Qwen') from error
        if not torch.cuda.is_available():raise StageError('GPU_UNAVAILABLE','Qwen profile requires CUDA; no silent CPU fallback')
        if self.config.dtype=='bfloat16' and not torch.cuda.is_bf16_supported():raise StageError('UNSUPPORTED_CAPABILITY','Device does not support bfloat16; configure float16 explicitly')
        pipe=QwenImagePipeline.from_pretrained(str(self.config.model_dir),torch_dtype=getattr(torch,self.config.dtype),use_safetensors=True,local_files_only=True)
        if hasattr(pipe,'enable_vae_slicing'):pipe.enable_vae_slicing()
        if self.config.cpu_offload:pipe.enable_model_cpu_offload()
        else:pipe.to('cuda')
        self._pipeline=pipe;self._torch=torch

    async def generate(self,request):return await asyncio.to_thread(self._generate,request)

    def _generate(self,request):
        if request.size not in self.capabilities.sizes:raise StageError('UNSUPPORTED_CAPABILITY','Unsupported Qwen generation size')
        with self._lock:
            started=time.perf_counter()
            if self._pipeline is None:self._load()
            torch=self._torch;cuda=getattr(torch,'cuda',None)
            seed=request.seed if request.seed is not None else secrets.randbits(32)
            rng=torch.Generator(device='cpu').manual_seed(seed)
            if cuda:cuda.reset_peak_memory_stats()
            try:
                with torch.inference_mode():
                    result=self._pipeline(prompt=request.prompt,negative_prompt=request.negative_prompt or ' ',
                        true_cfg_scale=self.config.true_cfg_scale,num_inference_steps=self.config.steps,
                        width=request.size[0],height=request.size[1],generator=rng)
                data=encode_png(result.images[0].convert('RGB'))
            except Exception as error:raise ProviderError('Qwen generation failed ('+type(error).__name__+')',submission_status='unknown') from error
            return GeneratedImage(data,request_id='qwen-local-'+uuid4().hex,
                usage={'elapsed_seconds':round(time.perf_counter()-started,3),'api_cost_usd':0,'peak_allocated_vram_bytes':cuda.max_memory_allocated() if cuda else None},
                provenance={'source_mode':'local_gpu','model_id':self.config.model_id,'model_revision':self.config.revision,
                    'actual_seed':seed,'steps':self.config.steps,'true_cfg_scale':self.config.true_cfg_scale,
                    'dtype':self.config.dtype,'cpu_offload':self.config.cpu_offload,'torch_version':str(torch.__version__),'visual_quality_evaluated':False})
