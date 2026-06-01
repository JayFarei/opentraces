"""Deterministic citation-groundedness — the zero-circularity RLVR leg (br/69 Challenge 1).

A finding that cites a file / line / snippet that does NOT appear in the evidence the review
actually read is a hallucination BY CONSTRUCTION — machine-checkable, tamper-resistant, no
judge and no human label needed [Tülu 3 RLVR, DeepSeek-R1]. This is the strongest possible
legitimizer for a per-finding negative.

Its limit is equally important and stated honestly: it catches FABRICATED citations, not
SEMANTIC MISREADS (a finding that cites real code but mischaracterizes what it does). The cited
file is present, so this check correctly returns *grounded* — and the misread is routed to human
ratification, never silently passed and never agent-self-labeled.

No judgment here: the agent only SURFACED the candidate citation; this module mechanically
verifies it against the trace's own evidence.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any


def trace_evidence_blob(trace_json: Any) -> str:
    """Concatenate everything the review observed/produced (tool inputs+outputs+content), lower-cased.

    A cited file the review actually read leaves its path/content in this blob; a fabricated
    citation does not. Robust to trace shape — we walk the whole structure and collect strings.
    """
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            parts.append(node)

    walk(trace_json)
    return "\n".join(parts).lower()


#: only tokens that look like real FILE references (have a known code/doc extension) are
#: subject to the citation-existence check; bare symbols are not citation-existence claims.
_FILE_RE = re.compile(r"[\w./\-]+\.(?:py|md|toml|json|jsonl|yaml|yml|rst|mdx|txt|cfg|ini|sh|lock)\b",
                      re.IGNORECASE)


def _basename(p: str) -> str:
    return os.path.basename(p.strip().strip("`'\"").split(":")[0]).lower()


def repo_file_basenames(repo_root: str) -> set[str]:
    """Lowercased basenames of every file in the repo — the ground-truth citation universe.

    Checking against the repo (not the possibly-truncated trace evidence) avoids the
    false-fabrication bug where a real cited file is absent from a bounded trace packet.
    """
    out: set[str] = set()
    skip = {".git", "node_modules", ".venv", "__pycache__", ".mypy_cache", "dist", "build"}
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            out.add(f.lower())
    return out


@dataclass(frozen=True)
class GroundednessVerdict:
    finding_id: str
    cited: tuple[str, ...]
    citation_grounded: bool          # every cited file appears in what the review read
    unread_citations: tuple[str, ...]  # cited files absent from the evidence (fabrications)
    label: str                       # "fabricated_citation" | "citation_present" | "no_citation"

    def to_dict(self) -> dict:
        return {
            "finding_id": self.finding_id, "cited": list(self.cited),
            "citation_grounded": self.citation_grounded,
            "unread_citations": list(self.unread_citations), "label": self.label,
        }


def check_finding(finding: dict, present_basenames: set[str]) -> GroundednessVerdict:
    """Mechanically verify a finding's cited FILES exist in the ground-truth universe.

    ``present_basenames`` is the set of real file basenames (e.g. the repo filesystem, optionally
    unioned with trace evidence). Only file-extension tokens are checked; bare symbols are not
    citation-existence claims and are ignored. A cited file absent from the universe is a
    FABRICATION (a real, zero-circularity negative). A present file is ``citation_present`` — which
    does NOT clear a SEMANTIC misread; that is correctly routed to ratification, not passed here.
    """
    text = " ".join([
        str(finding.get("claim_quote", "")),
        " ".join(str(c) for c in finding.get("cited_paths", []) or []),
        " ".join(str(c) for c in finding.get("cited_lines", []) or []),
    ])
    file_tokens = _FILE_RE.findall(text) + [
        c for c in (finding.get("cited_paths", []) or []) if _FILE_RE.search(str(c))
    ]
    bases = sorted({_basename(c) for c in file_tokens if _basename(c)})
    if not bases:
        return GroundednessVerdict(str(finding.get("finding_id", "?")), (), True, (), "no_citation")
    unread = tuple(b for b in bases if b not in present_basenames)
    grounded = not unread
    label = "citation_present" if grounded else "fabricated_citation"
    return GroundednessVerdict(str(finding.get("finding_id", "?")), tuple(bases), grounded, unread, label)


def check_trace(present_basenames: set[str], findings: list[dict]) -> list[GroundednessVerdict]:
    return [check_finding(f, present_basenames) for f in findings]
