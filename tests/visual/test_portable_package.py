from pathlib import Path
import tomllib

def test_portable_package_manifest_has_runtime_and_configuration_resources():
    root=Path(__file__).resolve().parents[2]
    names=set((root/'scripts/package-manifest.txt').read_text(encoding='utf-8').splitlines())
    required={'src/poster_framework/coordination.py','src/poster_visual_producer/qc_bridge.py',
        'src/poster_visual_producer/qc_contracts.py','src/poster_visual_producer/cloud_backend.py',
        'src/poster_visual_producer/qwen_backend.py','src/poster_visual_producer/setup_cli.py',
        'src/poster_visual_producer/configuration.py','docs/backend-setup.md','docs/qc-handoff.md',
        'docs/schemas/LocalManifestV1.json','scripts/fetch-qwen-image.py',
        'examples/visual/qc_collaboration.py','examples/visual/backend-qwen.example.json',
        'examples/visual/backend-cloud.example.json'}
    assert required<=names,required-names
    config=tomllib.loads((root/'pyproject.toml').read_text(encoding='utf-8'))
    include=config['tool']['hatch']['build']['targets']['wheel']['force-include']
    assert include['resources/fonts/manifest.json']=='poster_visual_producer/resources/fonts/manifest.json'
    assert include['resources/fonts/LICENSE']=='poster_visual_producer/resources/fonts/LICENSE'
