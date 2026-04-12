"""Claude Code hook scripts and intent adapter.

These modules are copied into ``~/.claude/hooks/`` by ``opentraces hooks
install`` and invoked by Claude Code directly. They live here so the
installer can locate them at :data:`HOOK_SCRIPTS_DIR`.
"""

from pathlib import Path

HOOK_SCRIPTS_DIR = Path(__file__).parent
