"""Compatibility shim. Moved to :mod:`opentraces.capture.git.install`."""

import warnings

warnings.warn(
    "opentraces.installers.git_hook is deprecated; use opentraces.capture.git.install",
    DeprecationWarning,
    stacklevel=2,
)

from ..capture.git.install import *  # noqa: F401,F403,E402
from ..capture.git.install import (  # noqa: F401,E402
    CHAIN_BEGIN,
    CHAIN_END,
    HOOK_FILENAME,
    NOTES_REFSPEC,
    OWNED_HOOK_CONTENT,
    install,
    remove,
    status,
)
