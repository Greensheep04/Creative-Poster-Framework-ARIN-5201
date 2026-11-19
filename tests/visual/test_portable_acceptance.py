import asyncio,json
import pytest
import tempfile
from pathlib import Path

@pytest.fixture
def tmp_path():
    # Windows test runner paths plus framework invocation IDs exceed legacy MAX_PATH.
    scratch=Path(__file__).resolve().parents[2]/'.scratch'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='qa-',dir=scratch) as directory:
        yield Path(directory)

@pytest.mark.parametrize('scenario,ops,rounds',[('no_authorization',1,0),('regenerate_asset',3,1),('rerender',1,1),('revise_layout',1,1)])
def test_portable_collaboration_evidence(tmp_path,scenario,ops,rounds):
    from examples.visual.qc_collaboration import run_case
    summary=asyncio.run(run_case(tmp_path/scenario,scenario))
    assert summary['status']=='needs_review'
    assert summary['visual_quality_evaluated'] is False
    assert summary['image_operations']==ops
    assert summary['revision_rounds']==rounds
    assert summary['invariants']['passed']
    assert summary['invariants']['stale_report_rejected']
    assert len(summary['candidates'])==rounds+1
    assert all(c['text_manifest'][0]['copy_ref']=='title' for c in summary['candidates'])
    assert (tmp_path/scenario/'summary.json').is_file()
