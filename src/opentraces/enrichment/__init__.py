"""Enrichment pipeline: git signals, attribution, dependencies, metrics."""

from .attribution import build_attribution
from .dependencies import extract_dependencies, extract_dependencies_from_steps
from .git_signals import check_committed, detect_vcs, extract_git_signals
from .metrics import compute_metrics
from .snippets import detect_language, estimate_line_range, extract_edited_lines

__all__ = [
    "build_attribution",
    "check_committed",
    "compute_metrics",
    "detect_language",
    "detect_vcs",
    "estimate_line_range",
    "extract_dependencies",
    "extract_dependencies_from_steps",
    "extract_edited_lines",
    "extract_git_signals",
]
