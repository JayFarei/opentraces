"""Entity-diff enrichment — wraps the `ot-entities` binary.

The binary parses a commit's diff and emits a list of entity-level changes
(added / modified / renamed / deleted functions, classes, etc). It's
distributed as a separate executable so we don't ship a parser
toolchain in the Python wheel.

The upstream name and implementation details are intentionally omitted
from user-visible surfaces — users see "entity parser" / "ot-entities"
everywhere. The naming audit test (tests/integration/test_naming_audit.py)
enforces this.
"""

from .runner import EntityRunner
from .version import ENTITY_BINARY_VERSION, MANIFEST_PATH

__all__ = ["EntityRunner", "ENTITY_BINARY_VERSION", "MANIFEST_PATH"]
