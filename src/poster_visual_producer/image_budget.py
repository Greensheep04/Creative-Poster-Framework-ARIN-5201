"""Durable first-round budget, bound to authorization and source identities."""
import copy,hashlib,json,os
from pathlib import Path
from datetime import datetime,timezone
from contextlib import contextmanager

class FirstRoundLedger:
    def __init__(self,path,*,authorization_id=None,sources=None):
        self.path=Path(path);self.lock=self.path.with_suffix('.lock')
        self.authorization_id=authorization_id;self.sources=sources
        self.path.parent.mkdir(parents=True,exist_ok=True)
    @contextmanager
    def transaction(self):
        # Kernel locks release on process death, including during a crash.
        with self.lock.open('a+b') as handle:
            handle.seek(0,2)
            if handle.tell()==0:handle.write(b'\0');handle.flush()
            handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            try:
                state=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {
                    'round':1,'limit':6,'total_authorized_limit':15,'attempts':[],
                    'authorization_id':self.authorization_id,'sources':self.sources}
                if state['round']!=1 or state['limit']!=6 or state.get('total_authorized_limit')!=15:
                    raise ValueError('Round authorization mismatch')
                if state.get('authorization_id')!=self.authorization_id or state.get('sources')!=self.sources:
                    raise ValueError('Authorization/source identity changed')
                yield state
                tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(state,indent=2),encoding='utf-8');os.replace(tmp,self.path)
            finally:
                handle.seek(0)
                if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
    def snapshot(self):
        with self.transaction() as state:return copy.deepcopy(state)
    def reserve(self,case_id,request,backend,*,source_sha256=None,run_dir=None):
        with self.transaction() as state:
            if self.sources is not None and self.sources.get(case_id)!=source_sha256:
                raise ValueError('Case source is outside this authorization')
            if len(state['attempts'])>=6:raise ValueError('First round budget exhausted; user decision required')
            if any(a['case_id']==case_id for a in state['attempts']):raise ValueError('Case already attempted; no automatic retry')
            ordinal=len(state['attempts'])+1
            state['attempts'].append({'ordinal':ordinal,'case_id':case_id,'source_sha256':source_sha256,'run_dir':run_dir,
              'status':'pending_unknown','request':request.as_json(),'backend':dict(backend),
              'started_at':datetime.now(timezone.utc).isoformat()})
            return ordinal
    def finish(self,ordinal,status,details):
        with self.transaction() as state:
            row=state['attempts'][ordinal-1]
            if row['status']!='pending_unknown':raise ValueError('Attempt is already finalized')
            row.update(status=status,details=details,finished_at=datetime.now(timezone.utc).isoformat())
    def attach_result(self,case_id,result):
        with self.transaction() as state:
            row=next(r for r in state['attempts'] if r['case_id']==case_id)
            if self.sources and result['source_sha256']!=self.sources[case_id]:raise ValueError('Result source mismatch')
            row['result']=copy.deepcopy(result)

def authorization_ledger(runtime_root,authorization):
    if not authorization.get('authorization_id') or authorization.get('round')!=1 or authorization.get('limit')!=6:
        raise ValueError('Explicit first-round authorization required')
    sources=authorization.get('sources',{})
    if len(sources)!=6 or not all(isinstance(k,str) and isinstance(v,str) and v for k,v in sources.items()):
        raise ValueError('Authorization must identify all six original sources')
    name=hashlib.sha256(authorization['authorization_id'].encode()).hexdigest()
    return FirstRoundLedger(Path(runtime_root)/'image-authorizations'/(name+'.json'),
        authorization_id=authorization['authorization_id'],sources=sources)

class BudgetedGenerator:
    def __init__(self,generator,ledger,case_id,*,source_sha256=None,run_dir=None):
        self.generator=generator;self.ledger=ledger;self.case_id=case_id;self.capabilities=generator.capabilities
        self.source_sha256=source_sha256;self.run_dir=run_dir
    async def generate(self,request):
        ordinal=self.ledger.reserve(self.case_id,request,self.capabilities.generation_parameters,
            source_sha256=self.source_sha256,run_dir=self.run_dir)
        try:result=await self.generator.generate(request)
        except BaseException as error:
            self.ledger.finish(ordinal,'failed_or_interrupted',{'error_type':type(error).__name__})
            raise
        self.ledger.finish(ordinal,'generated',{'usage':result.usage,'provenance':result.provenance})
        return result
