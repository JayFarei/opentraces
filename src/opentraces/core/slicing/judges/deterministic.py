"""Deterministic judge (issue #141): identity — use the slicer's defaults.

S1/S2 raise no requests; S3/S4 fall back to their conservative deterministic
default answers (S3 collapses same-artifact clusters to the last success; S4
answers every pivot "stay"). No model call.
"""
from __future__ import annotations

from typing import Any


def resolve(*, slicer_name: str, steps: list[Any], requests: list[Any]) -> dict:
    return {}
