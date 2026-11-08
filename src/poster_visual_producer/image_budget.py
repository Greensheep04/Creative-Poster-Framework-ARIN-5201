"""Persistent, fail-closed application budget for the approved first round."""
import json,os
from pathlib import Path
from datetime import datetime,timezone
from contextlib import contextmanager

class FirstRoundLedger:
    def __init__(self,path):
        self.path=Path(path);self.lock=self.path.with_suffix('.lock')
        self.path.parent.mkdir(parents=True,exist_ok=True)
    @contextmanager
    def transaction(self):
        fd=os.open(self.lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        try:
            state=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'round':1,'limit':6,'total_authorized_limit':15,'attempts':[]}
            if state['round']!=1 or state['limit']!=6:raise ValueError('Round authorization mismatch')
            yield state
            tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(state,indent=2),encoding='utf-8');os.replace(tmp,self.path)
        finally:os.close(fd);self.lock.unlink()
    def reserve(self,case_id,request,backend):
        with self.transaction() as state:
            if len(state['attempts'])>=6:raise ValueError('First round budget exhausted; user decision required')
            if any(a['case_id']==case_id for a in state['attempts']):raise ValueError('Case already attempted; no automatic retry')
            ordinal=len(state['attempts'])+1
            state['attempts'].append({'ordinal':ordinal,'case_id':case_id,'status':'pending_unknown','request':request.as_json(),
              'backend':dict(backend),'started_at':datetime.now(timezone.utc).isoformat()})
            return ordinal
    def finish(self,ordinal,status,details):
        with self.transaction() as state:
            row=state['attempts'][ordinal-1]
            row.update(status=status,details=details,finished_at=datetime.now(timezone.utc).isoformat())

class BudgetedGenerator:
    def __init__(self,generator,ledger,case_id):
        self.generator=generator;self.ledger=ledger;self.case_id=case_id;self.capabilities=generator.capabilities
    async def generate(self,request):
        ordinal=self.ledger.reserve(self.case_id,request,self.capabilities.generation_parameters)
        try:result=await self.generator.generate(request)
        except BaseException as error:
            self.ledger.finish(ordinal,'failed_or_interrupted',{'error_type':type(error).__name__})
            raise
        self.ledger.finish(ordinal,'generated',{'usage':result.usage,'provenance':result.provenance})
        return result
