"""Stable hashes for input binding and safe local evaluation continuation."""

import hashlib
from importlib.resources import files
import json

from .contracts import AuditInputV1


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                      separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def input_fingerprint(audit: AuditInputV1) -> str:
    manifest = audit.model_dump(mode='json', exclude={'image'})
    for item in manifest.get('approved_copy') or ():
        if item.get('match_policy') == 'exact':
            item.pop('match_policy')  # Preserve pre-existing exact-copy label bindings.
    return fingerprint({'manifest': manifest,
                        'image_sha256': audit.image.sha256})


def software_fingerprint() -> str:
    names = ('contracts.py', 'checks.py', 'policy.py', 'engine.py', 'runtime.py', 'ports.py',
             'providers/chat.py', 'providers/fake.py', 'evaluate.py', 'evaluation_io.py',
             'identity.py', 'reporting.py', 'adapters/local_input.py',
             'prompts/critic_v2.txt', 'prompts/critic_v3.txt', 'prompts/critic_v4.txt')
    return fingerprint({name: hashlib.sha256(files('poster_critic').joinpath(name).read_bytes()).hexdigest()
                        for name in names})
