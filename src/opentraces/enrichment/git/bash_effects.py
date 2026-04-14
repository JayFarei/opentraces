"""Shell-pattern reconstruction layer for the attribution engine.

Claude sessions often mutate files through Bash rather than Write/Edit
tool_uses (47% of mutation events in the real corpus, per the plan-043
mining data). For those mutations the per-prompt file-history blobs are
absent, so we reconstruct the effect of each Bash command by parsing the
command string itself: redirects, heredocs, mv/cp/rm, sed -i, and the
handful of deterministic producers (echo, printf, cat).

The functions here are deliberately conservative — when parsing
confidence is low we return an empty effect list so the attribution
engine falls back to "pre-audit" rather than fabricating credit.

This module is a focused surface over the implementation in
`opentraces.enrichment.git.attribution`. The functions below are the
supported entry points; deeper helpers (heredoc parser, printf
interpreter, sed-script applier) are reachable via the underscore-prefixed
names when needed for tests, but the three public names here cover every
consumer contract the attribution path cares about.
"""
from __future__ import annotations

from . import attribution as _attribution

# Public surface — stable names. These are the functions the attribution
# pipeline (and any future callers) should reach for.
effects_of_bash_command = _attribution._effects_of_bash_command
content_from_producer = _attribution._content_from_producer
apply_sed_inplace = _attribution._apply_sed_inplace

# Legacy / low-level helpers — re-exported so tests can import them
# without reaching across the private boundary of the attribution module.
split_shlex = _attribution._split_shlex
interpret_printf = _attribution._interpret_printf
interpret_echo = _attribution._interpret_echo
parse_trailing_heredoc = _attribution._parse_trailing_heredoc
find_trailing_redirect = _attribution._find_trailing_redirect
apply_sed_script = _attribution._apply_sed_script

__all__ = [
    "effects_of_bash_command",
    "content_from_producer",
    "apply_sed_inplace",
    "split_shlex",
    "interpret_printf",
    "interpret_echo",
    "parse_trailing_heredoc",
    "find_trailing_redirect",
    "apply_sed_script",
]
