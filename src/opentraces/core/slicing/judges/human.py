"""Human judge (issue #141): the same requests answered by a person.

Behaviourally identical to the agent judge at the CLI boundary — the requests
are surfaced (``rc=10``) and a person supplies the ``--answers`` file. Kept as a
distinct backend so a caller can express intent (and so a future UI can route
human review differently from agent review).
"""
from __future__ import annotations

from typing import Any

from .agent import NEEDS_JUDGMENT


def resolve(*, slicer_name: str, steps: list[Any], requests: list[Any]) -> dict | object:
    return NEEDS_JUDGMENT if requests else {}
