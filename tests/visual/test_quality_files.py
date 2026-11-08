import pytest
from poster_framework import RunSession,OriginalRequest,ExecutionIdentity,ExecutionMode,AgentRole,ArtifactPurpose,FileReference
from poster_framework.errors import FileSecurityError

def test_scoped_resolution_validates_issued_identity(tmp_path):
 s=RunSession(OriginalRequest.from_keywords('resolve-test',['a','b']),root=tmp_path)
 c=s.create_context(ExecutionIdentity(run_id=s.run_id,role=AgentRole.layout_planner,invocation_id='a2',execution_mode=ExecutionMode.real))
 a=c.files.write_bytes(c.files.invocation_path('contract.json'),b'{}',purpose=ArtifactPurpose.evidence,media_type='application/json')
 f=FileReference(file_id=a.artifact_id,**{k:getattr(a,k) for k in ('run_id','uri','revision','sha256','media_type')})
 assert c.files.resolve_file(f)==a
 for update in ({'sha256':'wrong'},{'run_id':'other'},{'revision':'stale'},{'uri':'../escape'}):
  with pytest.raises(FileSecurityError):c.files.resolve_file(f.model_copy(update=update))
