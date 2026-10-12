"""Validate local inputs/configuration without connecting or revealing credentials."""

import argparse
import importlib.util
import json
import os
from pathlib import Path

from .adapters.local_input import load_input
from .errors import InputError
from .providers.chat import load_config, valid_api_key, valid_key_for_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Local-only readiness check; no API requests.')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--model', help='Named catalog entry')
    args = parser.parse_args(argv)
    result = {'network_requests': 0, 'httpx_available': importlib.util.find_spec('httpx') is not None,
              'live_ready': False, 'missing': []}
    try:
        if args.input:
            audit = load_input(args.input)
            result['input'] = {'sample_id': audit.sample_id, 'image_sha256': audit.image.sha256}
        if args.config:
            config = load_config(args.config, args.model)
            result['profile'], result['model'] = config.profile, config.model
            result['api_key_env'] = config.api_key_env
            if not valid_api_key(os.environ.get(config.api_key_env, '')):
                result['missing'].append(config.api_key_env)
            elif not valid_key_for_config(config, os.environ.get(config.api_key_env, '')):
                result['missing'].append(f'{config.api_key_env} must be a Token Plan subscription key (sk-cp-)')
            if 'YOUR-' in config.base_url.upper():
                result['missing'].append('replace example base_url with the account endpoint')
            if 'YOUR-' in config.model.upper():
                result['missing'].append('replace example model with an available vision model ID')
        else:
            result['missing'].append('provider config (--config)')
        if not result['httpx_available']:
            result['missing'].append('httpx installation')
        result['live_ready'] = not result['missing']
        result['note'] = 'Local configuration only; account permissions and model compatibility remain unverified.'
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['live_ready'] else 2
    except InputError as exc:
        print(json.dumps({'error': str(exc), 'network_requests': 0}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
