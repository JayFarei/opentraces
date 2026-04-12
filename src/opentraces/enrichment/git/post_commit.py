"""Compatibility shim. Moved to :mod:`opentraces.capture.git.post_commit`."""

import warnings

warnings.warn(
    "opentraces.enrichment.git.post_commit is deprecated; "
    "use opentraces.capture.git.post_commit",
    DeprecationWarning,
    stacklevel=2,
)

from ...capture.git.post_commit import *  # noqa: F401,F403,E402
