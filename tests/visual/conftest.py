import json, sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

@pytest.fixture
def spec_data():
    return json.loads((ROOT / "examples/visual/spec.json").read_text(encoding="utf-8"))

@pytest.fixture
def brief_data():
    return json.loads((ROOT / "examples/visual/brief.json").read_text(encoding="utf-8"))
