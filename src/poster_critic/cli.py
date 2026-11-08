"""Run an independent audit. Fake is default; live requires explicit configuration."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
from pathlib import Path
import sys

from .adapters.local_input import load_input
from .engine import AuditEngine
from .dispatch import DispatchResult, audit_and_dispatch
from .errors import BudgetExceededError, InputError, ProviderError
from .providers.fake import FakeProvider
from .reporting import reserve_outputs, usage_summary, write_bytes, write_json
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
    receipt = args.out.with_name(args.out.name + '.dispatch.json')
    feedback_path = args.out.with_name(args.out.name + '.a1.json')
    image_paths = {
        (kind, media): args.out.with_name(args.out.name + f'.{kind}.{extension}')
        for kind in ('poster', 'candidate')
        for media, extension in (('image/png', 'png'), ('image/jpeg', 'jpg'))
    }
    try:
        stack.enter_context(reserve_outputs(args.out, accounting, failure, receipt,
                                            feedback_path, *image_paths.values()))
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
        publication: dict[str, object] = {}

        def save_audit(result: DispatchResult) -> None:
            write_json(accounting, usage_summary(executor.records, input_price=args.input_price,
                                                 output_price=args.output_price, currency=args.currency))
            write_json(args.out, result.report.model_dump(mode='json'))

        async def on_release(result: DispatchResult) -> None:
            save_audit(result)
            image_path = image_paths[('poster', result.audit.image.media_type)]
            write_bytes(image_path, result.audit.image.data)
            publication.update(status='released', image_path=str(image_path.resolve()), feedback_path=None)

        async def on_feedback(result: DispatchResult) -> None:
            save_audit(result)
            image_path = image_paths[('candidate', result.audit.image.media_type)]
            write_bytes(image_path, result.audit.image.data)
            write_json(feedback_path, {
                'schema_version': 1, 'recipient': 'creative_director', 'status': 'awaiting_a1',
                'candidate_path': str(image_path.resolve()),
                'input_fingerprint': result.input_fingerprint,
                'audit': result.audit.model_dump(mode='json'),
                'report': result.report.model_dump(mode='json'),
            })
            publication.update(status='awaiting_a1', image_path=str(image_path.resolve()),
                               feedback_path=str(feedback_path.resolve()))

        result = asyncio.run(audit_and_dispatch(
            audit, AuditEngine(provider, executor, max_retries=args.attempts - 1),
            on_release=on_release, on_feedback=on_feedback,
        ))
        if result.route == 'test_only':
            save_audit(result)
            publication.update(status='test_only', image_path=None, feedback_path=None)
        write_json(receipt, {
            'schema_version': 1, 'route': result.route, 'action': result.action,
            'recipient': result.recipient, 'handler_status': result.handler_status,
            'sample_id': result.report.sample_id, 'image_sha256': result.report.image_sha256,
            'input_fingerprint': result.input_fingerprint, 'report_path': str(args.out.resolve()),
            'execution_mode': result.report.execution_mode, **publication,
        })
        report = result.report
        print(f'{report.decision}: {args.out.resolve()} ({report.execution_mode}; '
              f'{report.vision_calls} live model calls; {publication["status"]}; {receipt.resolve()})')
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
