"""Run an independent audit. Fake is default; live requires explicit configuration."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack, nullcontext
from pathlib import Path
import sys

from .adapters.local_input import load_input
from .engine import AuditEngine
from .dispatch import DispatchResult, audit_and_dispatch
from .delivery import DeliveryError, DeliveryGuard, delivery_key
from .errors import BudgetExceededError, InputError, ProviderError
from .providers.fake import FakeProvider
from .reporting import reserve_outputs, usage_summary, write_bytes, write_json
from .runtime import StandaloneExecutor
from .structured_diagnostics import structured_diagnostics
from .evidence_review import read_secondary_review
from .telemetry import TelemetryRecorder, TelemetryWriteError, provider_measurement_kind


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
    parser.add_argument('--structured-diagnostics', action='store_true', help='Write versioned machine-readable diagnostics beside the report')
    parser.add_argument('--delivery-ledger', type=Path, help='Shared SQLite ledger for persistent delivery deduplication')
    parser.add_argument('--handler-timeout', type=float, help='Handler deadline in seconds; requires --delivery-ledger, default 30')
    parser.add_argument('--secondary-review', type=Path, help='Explicit bounded OCR backend/regions policy; writes evidence sidecar, no fallback')
    parser.add_argument('--telemetry', action='store_true', help='Record opt-in timing/accounting sidecar, no content')
    parser.add_argument('--capability-profile', type=Path, help='Explicit sealed model capability for pre-admission checks')
    parser.add_argument('--provider-region', help='Explicit region matching selected capability; never inferred')
    parser.add_argument('--provider-session', action='store_true', help='Use one owned sequential HTTP client in this invocation')
    parser.add_argument('--language-profile', type=Path, help='Verified local diagnostics display policy; original machine data retained')
    args = parser.parse_args(argv)
    # Use identical tilde semantics in preflight and actual readers/writers.
    for name in ('input', 'out', 'config', 'response', 'delivery_ledger', 'secondary_review', 'capability_profile', 'language_profile'):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.expanduser())
    executor = None
    telemetry = None
    outputs_reserved = False
    handler_started = False
    handler_completed = False
    language = None
    stack = ExitStack()
    accounting = args.out.with_name(args.out.name + '.usage.json')
    failure = args.out.with_name(args.out.name + '.error.json')
    receipt = args.out.with_name(args.out.name + '.dispatch.json')
    feedback_path = args.out.with_name(args.out.name + '.a1.json')
    diagnostics_path = args.out.with_name(args.out.name + '.diagnostics.json')
    delivery_path = args.out.with_name(args.out.name + '.delivery.json')
    evidence_path = args.out.with_name(args.out.name + '.evidence.json')
    telemetry_path = args.out.with_name(args.out.name + '.telemetry.json')
    display_path = args.out.with_name(args.out.name + '.diagnostics-display.json')
    guard = None
    handled_result = None
    image_paths = {
        (kind, media): args.out.with_name(args.out.name + f'.{kind}.{extension}')
        for kind in ('poster', 'candidate')
        for media, extension in (('image/png', 'png'), ('image/jpeg', 'jpg'))
    }
    try:
        if args.delivery_ledger is not None:
            ledger = args.delivery_ledger.expanduser().resolve()
            ledger_files = {ledger, *(Path(str(ledger) + suffix) for suffix in ('-journal', '-wal', '-shm'))}
            reserved = {path.expanduser().resolve() for path in (
                args.out, accounting, failure, receipt, feedback_path, diagnostics_path,
                delivery_path, evidence_path, telemetry_path, display_path, *image_paths.values(), args.out.with_name('.' + args.out.name + '.lock'),
            )}
            source_paths = [args.input, args.config, args.response, args.secondary_review, args.capability_profile, args.language_profile]
            if args.provider == 'fake' and args.response is None:
                source_paths.append(args.input.with_suffix('.observation.json'))
            sources = {path.resolve() for path in source_paths if path is not None}
            if ledger_files.intersection(reserved | sources):
                raise InputError('delivery ledger and its journal files must be separate from audit inputs and the output family')
        optional_outputs = []
        if args.structured_diagnostics or args.language_profile:
            optional_outputs.append(diagnostics_path)
        if args.language_profile:
            optional_outputs.append(display_path)
        if args.telemetry:
            optional_outputs.append(telemetry_path)
        if args.delivery_ledger:
            optional_outputs.append(delivery_path)
        if args.secondary_review:
            optional_outputs.append(evidence_path)
        stack.enter_context(reserve_outputs(args.out, accounting, failure, receipt,
                                            feedback_path, *image_paths.values(), *optional_outputs))
        outputs_reserved = True
        if args.telemetry:
            telemetry = TelemetryRecorder(execution_mode='fake' if args.provider == 'fake' else 'live',
                measurement_kind='fake_replay' if args.provider == 'fake' else 'live_service', scope='dispatch')
        if args.handler_timeout is not None and args.delivery_ledger is None:
            raise InputError('--handler-timeout requires --delivery-ledger')
        if args.delivery_ledger is not None:
            guard = DeliveryGuard(args.delivery_ledger, handler_timeout=args.handler_timeout if args.handler_timeout is not None else 30.0)
        usage_summary((), input_price=args.input_price, output_price=args.output_price, currency=args.currency)
        with telemetry.stage('input_load') if telemetry else nullcontext():
            audit = load_input(args.input)
        if args.language_profile:
            from .language_profiles import read_language_profile
            language = read_language_profile(args.language_profile)
        reviewer = read_secondary_review(args.secondary_review) if args.secondary_review else None
        if reviewer is not None:
            reviewer.preflight(audit)
        if args.provider == 'fake':
            if args.config or args.model or args.capability_profile or args.provider_region or args.provider_session:
                raise InputError('--config is only used with --provider chat')
            provider = FakeProvider.from_file(args.response or args.input.with_suffix('.observation.json'))
        else:
            if not args.config or args.response:
                raise InputError('chat requires --config and does not accept --response')
            from .providers.chat import ChatProvider, load_config
            options = {}
            if args.capability_profile:
                from .provider_capabilities import load_capability
                options['capability'] = load_capability(args.capability_profile)
            if args.provider_region is not None:
                options['region'] = args.provider_region
            provider = ChatProvider(load_config(args.config, args.model), **options)
        if telemetry is not None:
            telemetry.measurement_kind = provider_measurement_kind(provider)
        executor = StandaloneExecutor(args.attempts, allow_live=provider.live,
                                      request_timeout=args.timeout, total_timeout=args.total_timeout)
        engine = AuditEngine(provider, executor, max_retries=args.attempts - 1, evidence_reviewer=reviewer, telemetry=telemetry, language_profile=language)
        publication: dict[str, object] = {}

        def save_audit(result: DispatchResult) -> None:
            nonlocal handled_result
            diagnostics = structured_diagnostics(result.audit, result.report) if args.structured_diagnostics or language else None
            handled_result = result
            write_json(accounting, usage_summary(executor.records, input_price=args.input_price,
                                                 output_price=args.output_price, currency=args.currency))
            write_json(args.out, result.report.model_dump(mode='json'))
            if diagnostics is not None:
                write_json(diagnostics_path, diagnostics.model_dump(mode='json'))
                if language is not None:
                    from .language_profiles import render_diagnostics
                    write_json(display_path, render_diagnostics(diagnostics, language).model_dump(mode='json'))
            if engine.last_evidence is not None:
                write_json(evidence_path, engine.last_evidence.model_dump(mode='json'))

        async def release(result: DispatchResult) -> None:
            save_audit(result)
            image_path = image_paths[('poster', result.audit.image.media_type)]
            write_bytes(image_path, result.audit.image.data)
            publication.update(status='released', image_path=str(image_path.resolve()), feedback_path=None)

        async def feedback(result: DispatchResult) -> None:
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

        async def deliver(result: DispatchResult) -> None:
            nonlocal handler_started, handler_completed
            with telemetry.stage('source_revalidate') if telemetry else nullcontext():
                from .dispatch import _bound_report, _validate_image_bytes
                _validate_image_bytes(result.audit)
                _bound_report(result.report, result.audit, model=provider.name, live=provider.live)
            handler_started = True
            with telemetry.stage('delivery') if telemetry else nullcontext():
                await (release(result) if result.route == 'release' else feedback(result))
            handler_completed = True

        async def run_dispatch():
            if args.provider_session:
                from .providers.chat import ProviderSession
                async with ProviderSession(provider):
                    return await audit_and_dispatch(audit, engine, on_release=deliver, on_feedback=deliver, delivery=guard)
            return await audit_and_dispatch(audit, engine, on_release=deliver, on_feedback=deliver, delivery=guard)

        result = asyncio.run(run_dispatch())
        if result.route == 'test_only':
            save_audit(result)
            publication.update(status='test_only', image_path=None, feedback_path=None)
        if guard is not None and result.route != 'test_only':
            guarded_receipt = guard.lookup(delivery_key(result.model_copy(update={'handler_status': 'pending'})))
            if guarded_receipt is None or guarded_receipt.state != 'completed':
                raise DeliveryError('delivery ledger has no completed receipt')
            write_json(delivery_path, {'schema_version': 1, 'request_status': 'handler_completed',
                                       'receipt': guarded_receipt.model_dump(mode='json')})
        write_json(receipt, {
            'schema_version': 1, 'route': result.route, 'action': result.action,
            'recipient': result.recipient, 'handler_status': result.handler_status,
            'sample_id': result.report.sample_id, 'image_sha256': result.report.image_sha256,
            'input_fingerprint': result.input_fingerprint, 'report_path': str(args.out.resolve()),
            'execution_mode': result.report.execution_mode, **publication,
        })
        if telemetry is not None:
            if result.route == 'test_only':
                telemetry.not_run('delivery')
            telemetry.finish(terminal_outcome='completed', delivery_state='not_called' if result.route == 'test_only' else 'completed')
            telemetry.write(telemetry_path)
        report = result.report
        print(f'{report.decision}: {args.out.resolve()} ({report.execution_mode}; '
              f'{report.vision_calls} live model calls; {publication["status"]}; {receipt.resolve()})')
        return 0
    except (InputError, ProviderError, BudgetExceededError, DeliveryError, OSError, ValueError, KeyboardInterrupt, asyncio.CancelledError) as exc:
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
        if guard is not None:
            try:
                previous = getattr(exc, 'receipt', None)
                if previous is None and handled_result is not None:
                    previous = guard.lookup(delivery_key(handled_result))
                if previous is not None and not delivery_path.exists():
                    write_json(delivery_path, {'schema_version': 1, 'request_status': 'failed_or_blocked',
                                               'error_type': type(exc).__name__, 'receipt': previous.model_dump(mode='json')})
            except (DeliveryError, InputError, OSError):
                print('could not persist delivery state; inspect the shared ledger', file=sys.stderr)
        if telemetry is not None and outputs_reserved and not isinstance(exc, TelemetryWriteError):
            try:
                if telemetry._ended is None:
                    previous = getattr(exc, 'receipt', None)
                    state = getattr(previous, 'state', None)
                    delivery_state = state if state in ('completed', 'uncertain') else 'completed' if handler_completed else 'uncertain' if handler_started else 'rejected' if isinstance(exc, DeliveryError) else 'not_called'
                    terminal = 'cancelled' if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else 'delivery_error' if isinstance(exc, DeliveryError) or handler_started else 'input_error' if isinstance(exc, (InputError, ValueError)) else 'execution_error'
                    telemetry.finish(terminal_outcome=terminal, delivery_state=delivery_state, error=exc)
                telemetry.write(telemetry_path)
            except (OSError, InputError, ValueError):
                print('telemetry_write_failed; prior audit/calls/delivery remain unchanged', file=sys.stderr)
        print(f'critic error: {exc}', file=sys.stderr)
        return 130 if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else 2
    finally:
        stack.close()


if __name__ == '__main__':
    raise SystemExit(main())
