"""Compatibility shim. The branch/PR consumer moved to
:mod:`opentraces.consumers.branch_pr` (see consumers/README.md).

Kept so existing imports, ``opentraces.core.branch_context``, and any
already-installed ``pr-intent-summary-v1`` workflow copies keep working. New
code should import from ``opentraces.consumers.branch_pr`` directly.

Re-exports the public ``__all__`` surface plus the ``NO_LINEAGE_*`` constants
the bundled workflow imports explicitly. This is intentionally the
externally-used surface, not full module-name parity; private helpers are not
re-exported. Import them from ``opentraces.consumers.branch_pr`` if needed.
"""

from __future__ import annotations

from opentraces.consumers.branch_pr import *  # noqa: F401,F403
# Re-export module-level constants that are not in ``__all__`` but are imported
# explicitly by the bundled workflow's build_rows.py.
from opentraces.consumers.branch_pr import (  # noqa: F401
    NO_LINEAGE_MERGE_COMMIT,
    NO_LINEAGE_NO_ATTRIBUTION,
    NO_LINEAGE_NO_BURST,
    NO_LINEAGE_NO_TRACES,
)
