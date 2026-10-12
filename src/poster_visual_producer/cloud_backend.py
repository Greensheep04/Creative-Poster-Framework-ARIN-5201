"""Explicit synchronous Images JSON protocol, with no GPU dependency or implicit fallback."""
import asyncio, base64, binascii, json, os
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from pydantic import BaseModel,ConfigDict,Field,field_validator
from .generators import GeneratorCapabilities,GeneratedImage,ProviderError
from .contracts import StageError
from .imaging import decode_image
from .metadata import safe_metadata


def validate_url(value,*,endpoint=False):
    parts=urlsplit(value)
    if not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ValueError('URL must have a host and no credentials/fragment')
    if parts.scheme!='https' and not (parts.scheme=='http' and parts.hostname in ('127.0.0.1','localhost','::1')):
        raise ValueError('Use HTTPS; HTTP is allowed only for local test services')
    if endpoint and (parts.query or not parts.path.rstrip('/').endswith('/images/generations')):
        raise ValueError('Configure a complete Images generations endpoint without a query')
    return value

class CloudAPIConfig(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    provider: str=Field(min_length=1)
    model: str=Field(min_length=1)
    endpoint: str
    api_key_env: str=Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]*$')
    sizes: tuple[tuple[int,int],...]=((1024,1536),(1024,1024),(1536,1024))
    timeout_seconds: float=Field(default=120,gt=0,le=600,allow_inf_nan=False)
    max_response_bytes: int=Field(default=48*1024*1024,strict=True,gt=0,le=64*1024*1024)
    parameters: dict[str,str]=Field(default_factory=dict)
    @field_validator('endpoint')
    @classmethod
    def endpoint_url(cls,v):return validate_url(v,endpoint=True)
    @field_validator('parameters')
    @classmethod
    def supported_parameters(cls,v):
        if not set(v)<= {'quality','output_format','response_format','style'}:
            raise ValueError('Unsupported Images JSON parameter; use a custom factory for another protocol')
        return v
    @field_validator('sizes')
    @classmethod
    def sizes_valid(cls,v):
        if not v or any(type(n)!=int or n<=0 or n>8192 for pair in v for n in pair):
            raise ValueError('Declare valid supported image sizes')
        return v

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None

class CloudImageGenerator:
    def __init__(self,config):
        self.config=config
        self.capabilities=GeneratorCapabilities(provider=config.provider,model=config.model,sizes=config.sizes,
            generation_parameters=(('protocol','images-json-v1'),('endpoint',config.endpoint),*sorted(config.parameters.items())))
        self._opener=build_opener(NoRedirect())

    async def generate(self,request):
        return await asyncio.to_thread(self._generate,request)

    def _read(self,response,limit):
        data=response.read(limit+1)
        if not data or len(data)>limit:
            raise StageError('INVALID_ASSET','Cloud response absent or exceeds size limit')
        return data

    def _generate(self,request):
        key=os.environ.get(self.config.api_key_env)
        if not key:
            raise StageError('MISSING_CREDENTIAL','API Key environment variable is missing')
        if request.size not in self.capabilities.sizes or request.seed is not None or request.negative_prompt is not None:
            raise StageError('UNSUPPORTED_CAPABILITY','Request was not compiled for the selected cloud protocol')
        body={'model':self.config.model,'prompt':request.prompt,'n':1,'size':f'{request.size[0]}x{request.size[1]}',**self.config.parameters}
        req=Request(self.config.endpoint,data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8'),headers={'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
        try:
            with self._opener.open(req,timeout=self.config.timeout_seconds) as response:
                request_id=response.headers.get('x-request-id')
                result=json.loads(self._read(response,self.config.max_response_bytes))
        except HTTPError as error:
            raise ProviderError('Images API rejected/failed with HTTP '+str(error.code),submission_status='rejected' if error.code in (400,401,403,404,422,429) else 'unknown',retry_safe=error.code==429) from error
        except (URLError,TimeoutError,OSError) as error:
            raise ProviderError('Images API transport failed ('+type(error).__name__+')',submission_status='unknown') from error
        if not isinstance(result,dict) or not isinstance(result.get('data'),list) or len(result['data'])!=1:
            raise StageError('INVALID_ASSET','Images API must return exactly one data entry')
        row=result['data'][0]
        if not isinstance(row,dict):raise StageError('INVALID_ASSET','Invalid Images API data entry')
        if row.get('b64_json'):
            try:data=base64.b64decode(row['b64_json'],validate=True)
            except (ValueError,TypeError,binascii.Error) as error:raise StageError('INVALID_ASSET','Invalid base64 image') from error
            delivery='base64'
        elif row.get('url'):
            url=validate_url(row['url'])
            try:
                # Never propagate the API's Authorization to a delivery URL.
                with self._opener.open(Request(url),timeout=self.config.timeout_seconds) as response:
                    data=self._read(response,32*1024*1024)
            except (URLError,TimeoutError,OSError) as error:
                raise ProviderError('Submitted image could not be retrieved ('+type(error).__name__+')',submission_status='unknown') from error
            delivery='url'
        else:raise StageError('INVALID_ASSET','Images API returned no usable image')
        decode_image(data)
        return GeneratedImage(data,request_id=request_id,usage=safe_metadata(result.get('usage')),
            provenance=safe_metadata({'source_mode':'cloud_api','protocol':'images-json-v1','actual_parameters':body,'delivery':delivery,'revised_prompt':row.get('revised_prompt'),'visual_quality_evaluated':False}))
