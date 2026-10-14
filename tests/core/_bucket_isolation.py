"""Hard guard for tests that write, pack or unpack a bucket.

conftest's autouse fixture points ``paths.OPENTRACES_DIR`` at a temp home, but
anything that undoes the shared ``monkeypatch`` (e.g. ``monkeypatch.undo()``)
silently points the rest of a test at the real ``~/.opentraces``. Importing
``bucket_isolation_guard`` into a test module fails such a test BEFORE any
write: the guard wraps every bucket write boundary (record writes, pack,
unpack) through its own ``MonkeyPatch`` instance, which a test's
``monkeypatch.undo()`` cannot remove, and re-checks at setup and teardown.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest

from opentraces.core import bucket_trace_pack as btp
from opentraces.core import bucket_trace_records as btr
from opentraces.core import paths
from opentraces.core.bucket_layout import trace_records_root

_BASETEMP: Path | None = None
# Every function through which a pack test can write under the bucket.
WRITE_BOUNDARIES = (
    (btr, "_atomic_write_json"),
    (btp, "_write_pack"),
    (btp, "pack_trace_dir"),
    (btp, "unpack_trace_dir"),
)


def assert_bucket_isolated(basetemp: Path | None = None) -> None:
    base = basetemp or _BASETEMP
    if base is None:
        pytest.fail("bucket isolation guard is not active", pytrace=False)
    base = base.resolve()
    for label, path in (
        ("paths.OPENTRACES_DIR", paths.OPENTRACES_DIR),
        ("bucket_dir()", paths.bucket_dir()),
        ("trace_records_root()", trace_records_root()),
    ):
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(base):
            pytest.fail(f"bucket isolation escaped: {label} = {resolved} is outside {base}", pytrace=False)


def _guarded(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        assert_bucket_isolated()
        return fn(*args, **kwargs)

    return wrapper


@pytest.fixture(autouse=True)
def bucket_isolation_guard(tmp_path_factory):
    global _BASETEMP
    _BASETEMP = tmp_path_factory.getbasetemp()
    assert_bucket_isolated()
    guard = pytest.MonkeyPatch()  # NOT the test's monkeypatch: undo() can't drop it
    for module, name in WRITE_BOUNDARIES:
        guard.setattr(module, name, _guarded(getattr(module, name)))
    try:
        yield
        assert_bucket_isolated()
    finally:
        guard.undo()
        _BASETEMP = None
