"""opentraces: Crowdsource agent traces to HuggingFace Hub."""

from __future__ import annotations

import sys
from pathlib import Path


def _prefer_workspace_schema_path() -> None:
    """Prefer the in-repo schema package when running from a checkout.

    ``opentraces`` and ``opentraces-schema`` are developed side by side in this
    repo. A stale site-packages install of ``opentraces-schema`` can otherwise
    shadow the local schema sources and break the CLI at import time.
    """

    repo_root = Path(__file__).resolve().parents[2]
    schema_src = repo_root / "packages" / "opentraces-schema" / "src"
    if not (schema_src / "opentraces_schema").exists():
        return

    schema_src_str = str(schema_src)
    try:
        current_index = sys.path.index(schema_src_str)
    except ValueError:
        sys.path.insert(0, schema_src_str)
        return

    if current_index != 0:
        sys.path.pop(current_index)
        sys.path.insert(0, schema_src_str)


_prefer_workspace_schema_path()

__version__ = "0.4.12"
