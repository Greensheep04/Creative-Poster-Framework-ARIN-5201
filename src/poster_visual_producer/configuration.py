"""Trusted backend profiles and non-generating readiness checks."""
import json,os,tempfile
from pathlib import Path
from .qwen_backend import QWEN_REVISION
from .fonts import FontRegistry
from .contracts import StageError


def backend_template(kind):
    if kind=='sdxl':return {'kind':'sdxl','model_dir':'$POSTER_SDXL_MODEL_DIR','revision':'462165984030d82259a11f4367a4eed129e94a7b','steps':30,'guidance_scale':6.0,'cpu_offload':False}
    if kind=='qwen_image':return {'kind':'qwen_image','model_dir':'$POSTER_QWEN_MODEL_DIR','model_id':'Qwen/Qwen-Image','revision':QWEN_REVISION,'steps':30,'true_cfg_scale':4.0,'dtype':'bfloat16','cpu_offload':True}
    if kind=='cloud_api':return {'kind':'cloud_api','provider':'','model':'','endpoint':'https://api.openai.com/v1/images/generations','api_key_env':'POSTER_IMAGE_API_KEY','sizes':[[1024,1536],[1024,1024],[1536,1024]],'parameters':{}}
    raise ValueError('Choose sdxl, qwen_image or cloud_api')


def write_backend_config(path,config,*,overwrite=False):
    path=Path(path)
    if path.is_symlink():raise ValueError('Configuration output cannot be a symbolic link')
    path.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive creation prevents a second setup process from overwriting a user file.
    with path.open('w' if overwrite else 'x',encoding='utf-8') as stream:
        json.dump(config,stream,ensure_ascii=False,allow_nan=False,indent=2)
    return path


def diagnose_backend(config,*,fonts_config=None,font_base=Path('.'),output_root=None):
    checks=[]
    def add(code,message,remediation='',ok=False):checks.append({'code':code,'ok':ok,'message':message,'remediation':remediation})
    kind=config.get('kind') if isinstance(config,dict) else None
    try:
        if kind=='factory':
            add('APPLICATION_PROBE_REQUIRED','Custom factory is not executed by doctor','Use your trusted application to validate the factory')
        else:
            from .backends import create_generator,verify_weights
            generator=create_generator(config)
            add('SCHEMA_VALID','Selected backend configuration is valid',ok=True)
            if kind=='cloud_api':
                if os.environ.get(generator.config.api_key_env):add('CREDENTIAL_PRESENT','API Key environment variable is set',ok=True)
                else:add('MISSING_CREDENTIAL','API Key environment variable is not set','Set '+generator.config.api_key_env+' in your local environment')
                add('REMOTE_NOT_PROBED','Remote service is not contacted; readiness is local configuration only',ok=True)
            else:
                try:
                    import torch
                    from diffusers import StableDiffusionXLPipeline,QwenImagePipeline
                    if not torch.cuda.is_available():add('GPU_UNAVAILABLE','CUDA is unavailable','Install the compatible CUDA PyTorch environment or select cloud API')
                    elif kind=='qwen_image' and generator.config.dtype=='bfloat16' and not torch.cuda.is_bf16_supported():add('UNSUPPORTED_DTYPE','Device lacks bfloat16 support','Configure float16 explicitly')
                    else:add('GPU_READY','CUDA and selected pipeline dependencies are available',ok=True)
                except ImportError:add('MISSING_DEPENDENCY','Local inference dependencies are missing','Install the locked local-gpu optional dependencies')
                try:
                    if kind=='sdxl':verify_weights(generator.config)
                    else:
                        from .qwen_backend import verify_qwen_weights
                        verify_qwen_weights(generator.config)
                    add('MODEL_VERIFIED','Fixed model identity, complete components and hashes verified',ok=True)
                except (ValueError,OSError,json.JSONDecodeError) as error:
                    add(getattr(error,'code','MISSING_MODEL'),'Selected local model files are absent, incomplete or invalid','Run the explicit downloader and verify your model directory/revision')
    except Exception as error:
        # Do not serialize validation errors: they can contain user-provided secrets.
        add(getattr(error,'code','INVALID_CONFIGURATION'),'Selected backend profile is incomplete or invalid','Fill the selected template; set model directory environment variables; remove unknown fields')
    if fonts_config is not None:
        try:
            registry=FontRegistry.from_config(json.loads(Path(fonts_config).read_text(encoding='utf-8-sig')),Path(font_base))
            from .contracts import TextElement
            for family,weights in registry.families.items():
                for weight in weights:
                    registry.resolve(TextElement(id='font_probe',type='text',z=0,box={'x':0,'y':0,'width':500,'height':100},copy_ref='probe',text='海A',font_family_key=family,font_weight=weight,font_size_px=28,line_height_px=38,max_lines=1,align='left',color='#000000'))
            if not registry.families:raise ValueError('Empty font registry')
            add('FONTS_READY','Configured font files/weights and Chinese/Latin glyphs verified',ok=True)
        except Exception:add('MISSING_FONT','Configured font files or glyphs are unavailable','Fetch the pinned project fonts and set --font-base')
    if output_root is not None:
        try:
            parent=Path(output_root).resolve()
            while not parent.exists():parent=parent.parent
            with tempfile.NamedTemporaryFile(prefix='poster-doctor-',dir=parent):pass
            add('OUTPUT_WRITABLE','Output parent directory is writable',ok=True)
        except OSError:add('OUTPUT_UNWRITABLE','Output directory cannot be written','Choose a writable output root')
    return {'backend':kind,'ready':bool(checks) and all(c['ok'] for c in checks),'checks':checks}
