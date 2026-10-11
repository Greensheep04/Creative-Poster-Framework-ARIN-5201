"""Run an independent audit. Fake is default; live requires explicit configuration."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
from pathlib import Path
import sys

from .adapters.local_input import load_input
from .engine import AuditEngine
from .errors import BudgetExceededError, InputError, ProviderError
from .providers.fake import FakeProvider
from .reporting import reserve_outputs, usage_summary, write_json
from .runtime import StandaloneExecutor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Independent poster critic; fake does not inspect images.')
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--provider', choices=['fake', 'chat'], default='fake')
    parser.add_argument('--response', type=Path, help='Fake observation, defaults to <input-stem>.observation.json')
    parser.add_argument('--config', type=Path, help='Provider JSON without secrets; required for chat')
    parser.add_argument('--model', help='Named entry in a model catalog; otherwise use its active_model')
    parser.add_argument('--out', type=Path, required=True, help='New report path; never overwritten')
    parser.add_argument('--attempts', type=int, choices=[1, 2], default=2)
    parser.add_argument('--timeout', type=float, default=30.0, help='Per-attempt timeout, seconds')
    parser.add_argument('--total-timeout', type=float, default=90.0, help='Elapsed request/retry budget, seconds')
    parser.add_argument('--input-price', type=float, help='Optional input cost per million tokens')
    parser.add_argument('--output-price', type=float, help='Optional output cost per million tokens')
    parser.add_argument('--currency', help='Currency for the optional flat-rate estimate')
    args = parser.parse_args(argv)
    executor = None
    stack = ExitStack()
    accounting = args.out.with_name(args.out.name + '.usage.json')
    failure = args.out.with_name(args.out.name + '.error.json')
    try:
        stack.enter_context(reserve_outputs(args.out, accounting, failure))
        usage_summary((), input_price=args.input_price, output_price=args.output_price, currency=args.currency)
        audit = load_input(args.input)
        if args.provider == 'fake':
            if args.config or args.model:
                raise InputError('--config is only used with --provider chat')
            provider = FakeProvider.from_file(args.response or args.input.with_suffix('.observation.json'))
        else:
            if not args.config or args.response:
                raise InputError('chat requires --config and does not accept --response')
            from .providers.chat import ChatProvider, load_config
            provider = ChatProvider(load_config(args.config, args.model))
        executor = StandaloneExecutor(args.attempts, allow_live=provider.live,
                                      request_timeout=args.timeout, total_timeout=args.total_timeout)
        report = asyncio.run(AuditEngine(provider, executor, max_retries=args.attempts - 1).audit(audit))
        write_json(args.out, report.model_dump(mode='json'))
        write_json(accounting, usage_summary(executor.records, input_price=args.input_price,
                                             output_price=args.output_price, currency=args.currency))
        print(f'{report.decision}: {args.out.resolve()} ({report.execution_mode}; {report.vision_calls} live model calls)')
        return 0
    except (InputError, ProviderError, BudgetExceededError, OSError, ValueError, KeyboardInterrupt) as exc:
        # Preserve consumed attempts on operational errors, without making a quality judgment.
        if executor is not None and executor.records:
            try:
                write_json(failure, {'kind': 'execution_error', 'error_type': type(exc).__name__,
                                    'calls': [c.model_dump(mode='json') for c in executor.records]})
                if not accounting.exists():
                    write_json(accounting, usage_summary(executor.records, input_price=args.input_price,
                                                         output_price=args.output_price, currency=args.currency))
            except OSError:
                print('could not persist execution records', file=sys.stderr)
        print(f'critic error: {exc}', file=sys.stderr)
        return 130 if isinstance(exc, KeyboardInterrupt) else 2
    finally:
        stack.close()


if __name__ == '__main__':
    raise SystemExit(main())
