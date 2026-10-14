"""Hard guard for tests that write, pack or unpack a bucket.

conftest's autouse fixture points ``paths.OPENTRACES_DIR`` at a temp home, but
anything that undoes the shared ``monkeypatch`` (e.g. ``monkeypatch.undo()``)
silently points the rest of a test at the real ``~/.opentraces``. Importing
``bucket_isolation_guard`` into a test module makes that fail loudly before
and after every test instead of touching real data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opentraces.core import paths
from opentraces.core.bucket_layout import trace_records_root


def assert_bucket_isolated(basetemp: Path) -> None:
    base = basetemp.resolve()
    for label, path in (
        ("paths.OPENTRACES_DIR", paths.OPENTRACES_DIR),
        ("bucket_dir()", paths.bucket_dir()),
        ("trace_records_root()", trace_records_root()),
    ):
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(base):
            pytest.fail(f"bucket isolation escaped: {label} = {resolved} is outside {base}", pytrace=False)


@pytest.fixture(autouse=True)
def bucket_isolation_guard(tmp_path_factory):
    basetemp = tmp_path_factory.getbasetemp()
    assert_bucket_isolated(basetemp)
    yield
    assert_bucket_isolated(basetemp)
