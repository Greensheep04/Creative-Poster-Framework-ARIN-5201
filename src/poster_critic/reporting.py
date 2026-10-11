"""Durable local output and transparent usage estimates; never infer missing bills."""

import json
import math
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .contracts import CallRecordV2
from .errors import InputError


def write_text(path: Path, text: str, *, replace: bool = False) -> None:
    """Publish a complete file atomically; no replacement unless explicitly requested."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.a4-write-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)  # Atomic create-if-absent; never clobber another run.
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: object, *, replace: bool = False) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    write_text(path, text, replace=replace)


@contextmanager
def file_lock(path: Path):
    """Local POSIX advisory lock. Persistent inode; kernel releases it after a crash."""
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InputError('another process is using this output; choose another destination') from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def reserve_outputs(*paths: Path):
    """Check the complete CLI output family before making a model request."""
    with file_lock(paths[0].with_name('.' + paths[0].name + '.lock')):
        for path in paths:
            if path.exists() or path.is_symlink():
                raise InputError(f'output already exists: {path}')
        # Test the same atomic publication operation used for the actual results.
        fd, name = tempfile.mkstemp(prefix='.a4-probe-', dir=paths[0].parent)
        os.close(fd)
        probe = Path(name + '.json')
        try:
            write_json(probe, {'probe': True})
        finally:
            Path(name).unlink(missing_ok=True)
            probe.unlink(missing_ok=True)
        yield


def usage_summary(calls: tuple[CallRecordV2, ...], *, input_price: float | None = None,
                  output_price: float | None = None, currency: str | None = None) -> dict:
    if (input_price is None) != (output_price is None):
        raise InputError('provide both input and output prices per million tokens')
    if input_price is not None and (not currency or any(
            isinstance(p, bool) or not math.isfinite(p) or p < 0 for p in (input_price, output_price))):
        raise InputError('prices must be finite/nonnegative and require a currency')
    live = [c for c in calls if c.live]
    known = [c for c in live if c.usage is not None]
    input_tokens = sum(c.usage.input_tokens for c in known)
    output_tokens = sum(c.usage.output_tokens for c in known)
    subtotal = None if input_price is None else (input_tokens * input_price + output_tokens * output_price) / 1_000_000
    return {
        'live_attempts': len(live), 'attempts_with_unknown_usage': len(live) - len(known),
        'reported_input_tokens': input_tokens, 'reported_output_tokens': output_tokens,
        'currency': currency, 'known_usage_cost_estimate': subtotal,
        'total_cost_estimate': subtotal if len(known) == len(live) else None,
        'note': 'Optional flat-rate estimate, not an invoice or monetary budget; token details are not added twice.',
    }
