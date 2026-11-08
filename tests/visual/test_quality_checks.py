import asyncio,copy,hashlib
import pytest
from poster_visual_producer.quality_checks import evaluate_observation
from poster_visual_producer.image_budget import FirstRoundLedger,BudgetedGenerator
from poster_visual_producer.generators import FixtureImageGenerator,compile_request
from poster_visual_producer.contracts import ImageElement
from poster_visual_producer.imaging import prepare_asset
from tests.visual.helpers import png

def test_actual_bbox_crop_loss_and_unknown_never_pass(spec_data):
    e=ImageElement.model_validate(spec_data['elements'][0])
    data=png();processing=prepare_asset(data,e).record
    obs={'source_sha256':hashlib.sha256(data).hexdigest(),'source_status':'pass','poster_status':'unknown','object_boxes_norm':[[.4,.4,.6,.6]]}
    r=evaluate_observation(data=data,processing=processing,observation=obs)
    assert r['status']=='unknown'
    obs['source_sha256']='wrong'
    with pytest.raises(ValueError):evaluate_observation(data=data,processing=processing,observation=obs)
    obs['source_sha256']=hashlib.sha256(data).hexdigest();obs['poster_status']='pass';obs['object_boxes_norm']=[[0,0,.02,.02]]
    assert evaluate_observation(data=data,processing=processing,observation=obs)['poster_status']=='fail'

def test_budget_counts_failures_rejects_retries_and_survives_reload(tmp_path,spec_data):
    ledger=FirstRoundLedger(tmp_path/'budget.json');gen=FixtureImageGenerator(lambda r:png())
    req=compile_request(spec_data['asset_plan'][0],gen.capabilities)
    first=ledger.reserve('one',req,());ledger.finish(first,'failed',{})
    with pytest.raises(ValueError,match='already attempted'):FirstRoundLedger(ledger.path).reserve('one',req,())
    for i in range(5):ledger.reserve(str(i),req,())
    with pytest.raises(ValueError,match='exhausted'):ledger.reserve('seventh',req,())

def test_request_validation_occurs_before_costly_call(tmp_path,spec_data,brief_data):
    from tests.visual.test_agent import setup,run
    from poster_visual_producer.contracts import StageError
    a,s,sp,b,p=setup(tmp_path,spec_data,brief_data)
    def validator(request):
        assert 'Avoid the following' in request.prompt
        raise StageError('PROMPT_OVERFLOW','adapted request too long')
    p.request_validator=validator
    with pytest.raises(StageError,match='too long'):run(a,p,s,sp,'overflow')
    assert s.usage.image_operations==0


def test_provider_failure_consumes_persistent_budget(tmp_path,spec_data):
    import json
    def fail(request):raise RuntimeError('provider failed')
    gen=FixtureImageGenerator(fail);ledger=FirstRoundLedger(tmp_path/'failed.json')
    wrapped=BudgetedGenerator(gen,ledger,'case')
    request=compile_request(spec_data['asset_plan'][0],gen.capabilities)
    with pytest.raises(RuntimeError):asyncio.run(wrapped.generate(request))
    saved=json.loads(ledger.path.read_text())
    assert len(saved['attempts'])==1 and saved['attempts'][0]['status']=='failed_or_interrupted'
