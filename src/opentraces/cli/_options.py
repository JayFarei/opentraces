"""Shared Click option decorators and helpers for the opentraces CLI.

Centralises option definitions and output helpers that are byte-identical across
many commands so that help text and defaults stay consistent without repetition.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, TypeVar

import click

F = TypeVar("F", bound=Callable[..., Any])


def project_dir_option(fn: F) -> F:
    """Attach ``--project`` / ``project_dir`` to a Click command.

    Equivalent to::

        @click.option(
            "--project", "project_dir",
            type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
            default=None,
            help="Project directory (default: CWD).",
        )

    Only applied to commands whose option is byte-identical to the above.
    Variants with different help text or flags remain inline.
    """
    return click.option(
        "--project",
        "project_dir",
        type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
        default=None,
        help="Project directory (default: CWD).",
    )(fn)


def dump_json(obj: Any) -> str:
    """Return ``json.dumps(obj, indent=2, sort_keys=True)``.

    Single source of truth for the canonical JSON serialisation used across
    CLI commands.  Output is byte-identical to the inline calls it replaces.
    Do NOT use for calls that need ``default=``, ``ensure_ascii=False``, or a
    different ``indent`` / ``sort_keys`` value; those remain inline.
    """
    return json.dumps(obj, indent=2, sort_keys=True)
