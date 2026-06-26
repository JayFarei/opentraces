"""Agent judge (issue #141, DEFAULT): "the CLI prompts the agent that ran it."

When the slicer raises judgment requests and no ``--answers`` were supplied,
``resolve`` returns :data:`NEEDS_JUDGMENT`. The CLI then prints the requests +
an explicit instruction and exits ``rc=10 needs-judgment``. The invoking agent
answers them in one structured response and re-runs with ``--answers`` — two
calls + one agent reasoning step, no credentials, no setup.
"""
from __future__ import annotations

from typing import Any

NEEDS_JUDGMENT = object()


def resolve(*, slicer_name: str, steps: list[Any], requests: list[Any]) -> dict | object:
    return NEEDS_JUDGMENT if requests else {}
