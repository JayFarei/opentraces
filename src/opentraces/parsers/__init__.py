"""Compatibility shim. Moved to :mod:`opentraces.capture`."""

import warnings

warnings.warn(
    "opentraces.parsers is deprecated; use opentraces.capture",
    DeprecationWarning,
    stacklevel=2,
)

from ..capture import (  # noqa: F401,E402
    IMPORTERS,
    PARSERS,
    _IMPORT_ALIASES,
    _register_defaults,
    get_importers,
    get_parsers,
    resolve_import_format,
)
