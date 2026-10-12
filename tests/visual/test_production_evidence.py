import asyncio, copy
from poster_framework import AgentRole, RevisionAction, VersionDependency
from poster_visual_producer.agent import VisualProducerAgent
from poster_visual_producer.generators import FixtureImageGenerator
from tests.visual.helpers import make_session, call_context, fonts, png, publish


def run(session, spec, producer, name, action=None, target=None):
    inputs, context = call_context(session, spec, name, action, target)
    return asyncio.run(producer.run(inputs, context)).content


def test_multiple_elements_keep_independent_processing_refs(tmp_path, spec_data, brief_data):
    second = copy.deepcopy(spec_data['elements'][0])
    second.update(id='second_hero', z=5)
    second['box']['y'] = 300
    spec_data['elements'].append(second)
    spec_data['allowed_overlaps'].append(['hero', 'second_hero'])
    session, spec, _ = make_session(tmp_path, spec_data, brief_data)
    producer = VisualProducerAgent(FixtureImageGenerator(lambda r: png()), fonts())
    candidate = run(session, spec, producer, 'first')
    asset = candidate['assets'][0]
    assert asset['element_ids'] == ['hero', 'second_hero']
    assert asset['source_size'] == {'width': 900, 'height': 1180}
    bindings = asset['processed_bindings']
    assert [b['element_id'] for b in bindings] == ['hero', 'second_hero']
    assert [b['asset_to_canvas'] for b in bindings] == [[1,0,0,1,36,260],[1,0,0,1,36,300]]
    from poster_framework import ArtifactReference
    assert all(session.file_store.read(ArtifactReference.model_validate(b['processed_ref'])) for b in bindings)
    assert asset['processed_ref'] == bindings[0]['processed_ref']
    assert candidate['text_manifest'][0]['copy_ref'] == 'title'
    assert candidate['execution_decisions'][0]['action'] == 'generate'


def test_layout_move_records_reuse_and_zero_image_ops(tmp_path, spec_data, brief_data):
    session, spec, _ = make_session(tmp_path, spec_data, brief_data)
    producer = VisualProducerAgent(FixtureImageGenerator(lambda r: png()), fonts())
    first = run(session, spec, producer, 'first')
    spec = copy.deepcopy(spec)
    spec['spec_version'] = 2
    spec['elements'][1]['box']['y'] = 84
    publish(session, 'layout', spec, AgentRole.layout_planner, 'layout2')
    second = run(session, spec, producer, 'second')
    assert second['usage_delta']['image_ops'] == 0
    assert first['assets'][0]['source_ref'] == second['assets'][0]['source_ref']
    assert second['execution_decisions'][0]['action'] == 'reuse_current'


def test_authorized_asset_repair_records_target_and_preserves_copy(tmp_path, spec_data, brief_data):
    session, spec, _ = make_session(tmp_path, spec_data, brief_data)
    count = [0]
    def source(r):
        count[0] += 1
        return png('blue' if count[0] == 1 else 'green')
    producer = VisualProducerAgent(FixtureImageGenerator(source), fonts())
    first = run(session, spec, producer, 'first')
    ref = first['assets'][0]['source_ref']
    target = VersionDependency(artifact_id=ref['artifact_id'], revision=ref['revision'])
    second = run(session, spec, producer, 'second', RevisionAction.regenerate_asset, target)
    assert second['text_manifest'] == first['text_manifest']
    assert second['assets'][0]['asset_version'] == 2
    assert second['execution_decisions'][0]['action'] == 'regenerate'
    assert second['execution_decisions'][0]['target'] == target.model_dump(mode='json')
    assert second['usage_delta']['image_ops'] == 1
