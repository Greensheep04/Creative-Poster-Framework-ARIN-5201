"""Inspect or create model configuration, never make network requests."""

import argparse
from importlib.resources import files
import json
import os
from pathlib import Path

from .errors import InputError
from .providers.chat import ModelCatalog, read_config, valid_key_for_config
from .reporting import write_text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('init')
    create.add_argument('--out', type=Path, required=True)
    inspect = commands.add_parser('list')
    inspect.add_argument('--config', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            write_text(args.out, files('poster_critic').joinpath('resources/models.example.json').read_text())
            print(f'Created {args.out.resolve()}; edit endpoints/model IDs and set keys locally.')
        else:
            config = read_config(args.config)
            entries = config.models if isinstance(config, ModelCatalog) else {'single': config}
            active = config.active_model if isinstance(config, ModelCatalog) else 'single'
            print(json.dumps({'network_requests': 0, 'active_model': active, 'models': [
                {'alias': name, 'profile': item.profile, 'model': item.model, 'base_url': item.base_url,
                 'response_format': item.response_format, 'api_key_env': item.api_key_env,
                 'key_present_and_well_formed': valid_key_for_config(item, os.environ.get(item.api_key_env, ''))}
                for name, item in entries.items()]}, ensure_ascii=False, indent=2))
        return 0
    except (InputError, OSError) as exc:
        print(f'configuration error: {exc}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
