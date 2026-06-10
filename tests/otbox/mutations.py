"""World-fault mutations (otbox 2.0 phase 5).

Principle 4: every guard must demonstrably fail. A journey that claims to
protect a guarantee must go RED when that guarantee is deliberately broken
in the world — a guard that stays green under its fault is vacuous, and
vacuity is detected by EXECUTION, not review.

v1 scope: world-faults only (filesystem/config mutations of a built box).
Product faultpoint-seam mutations (maturation-returns-unknown,
schema-comparison-inverted) arrive with the phase-6 faultpoints.py seam.

Ownership inversion: kills are not author-chosen — guarantees.toml maps
guarantee -> fault -> guard journeys, and the kill test derives from that
file, so adding a guarantee without a working kill fails the suite.
"""

from __future__ import annotations

import gzip
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

GUARANTEES_PATH = Path(__file__).parent / "catalogue" / "guarantees.toml"


def _home_opentraces(box) -> Path:
    return box.root / "home" / ".opentraces"


def _fault_disable_security_tools(driver, box) -> str:
    """Break: 'a planted secret never reaches query output' — flip every
    security tool off in the world's config so sanitize becomes a no-op."""
    cfg_path = _home_opentraces(box) / "config.json"
    cfg = json.loads(cfg_path.read_text())
    sec = cfg.get("security") or {}
    for tool, tool_cfg in sec.items():
        if isinstance(tool_cfg, dict) and "enabled" in tool_cfg:
            tool_cfg["enabled"] = False
    cfg["security"] = sec
    cfg_path.write_text(json.dumps(cfg, indent=2))
    return "all security tools disabled in world config"


def _fault_delete_events_ref(driver, box) -> str:
    """Break: 'the append-only event log is the canonical substrate' —
    delete refs/opentraces/local/events/v1 from the project repo."""
    project = str(box.root / "project")
    res = driver.exec(
        box,
        ["git", "-C", project, "update-ref", "-d", "refs/opentraces/local/events/v1"],
    )
    return f"deleted events ref (rc={res.returncode})"


def _fault_corrupt_trace_jsonl(driver, box) -> str:
    """Break: 'captured traces parse and serve reads' — truncate every
    captured trace JSONL to garbage."""
    projects = _home_opentraces(box) / "projects"
    hit = 0
    for trace in projects.glob("*/traces/*.jsonl"):
        trace.write_text('{"corrupted": tru\n')
        hit += 1
    return f"corrupted {hit} trace JSONL file(s)"


def _fault_truncate_companions(driver, box) -> str:
    """Break: 'bucket companions are readable evidence' — truncate every
    gzip companion (trail/context exports) to an invalid stream."""
    hit = 0
    for gz in _home_opentraces(box).rglob("*.jsonl.gz"):
        gz.write_bytes(gzip.compress(b"")[:8])
        hit += 1
    return f"truncated {hit} gzip companion(s)"


FAULTS: dict[str, Callable] = {
    "disable-security-tools": _fault_disable_security_tools,
    "delete-events-ref": _fault_delete_events_ref,
    "corrupt-trace-jsonl": _fault_corrupt_trace_jsonl,
    "truncate-companions": _fault_truncate_companions,
}


@dataclass
class Kill:
    guarantee: str
    fault: str
    checkpoint: str
    guards: list[str]


def load_guarantees(path: Path = GUARANTEES_PATH) -> list[Kill]:
    doc = tomllib.loads(path.read_text())
    out = []
    for raw in doc.get("guarantees", []):
        out.append(
            Kill(
                guarantee=str(raw["guarantee"]),
                fault=str(raw["fault"]),
                checkpoint=str(raw["checkpoint"]),
                guards=[str(g) for g in raw["guards"]],
            )
        )
    return out


def is_faultpoint(name: str) -> str | None:
    """Return the product faultpoint site for ``faultpoint:<site>`` names.

    Faultpoint faults arm a deterministic seam INSIDE the product
    (opentraces.core.faultpoints) via env vars, so they must be armed
    BEFORE the checkpoint world is built (the kill test handles env
    lifecycle + the checkpoint layer refuses cache I/O while armed).
    """
    if not name.startswith("faultpoint:"):
        return None
    site = name.split(":", 1)[1]
    from opentraces.core.faultpoints import SITES

    if site not in SITES:
        raise KeyError(f"unknown faultpoint site {site!r}; known: {sorted(SITES)}")
    return site


def known_fault(name: str) -> bool:
    if name in FAULTS:
        return True
    try:
        return is_faultpoint(name) is not None
    except KeyError:
        return False


def apply_fault(name: str, driver, box) -> str:
    fault = FAULTS.get(name)
    if fault is None:
        raise KeyError(f"unknown fault {name!r}; registered: {sorted(FAULTS)}")
    return fault(driver, box)
