"""Claude Code hook installer.

Ships the ``on_stop`` and ``on_compact`` Python scripts that the CLI
copies into ``~/.claude/hooks/`` on ``opentraces hooks install``. Also
exposes :mod:`intent_adapter`, imported directly from the summarization
subsystem without being copied anywhere.
"""
