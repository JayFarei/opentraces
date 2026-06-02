"""Static ordered registry of security/privacy tools.

Canonical execution order: cheap deterministic detectors first, expensive
LLM-backed detectors after, transformers, judges last. Session-level LLM
review (``opentraces dataset review``) is intentionally NOT here — it's an
on-demand workflow, not part of per-record sanitize.

The registry is intentionally a static tuple. The set is small,
security-critical, and any behavioural change bumps ``SECURITY_VERSION``.
"""

from __future__ import annotations

from typing import Any, Iterator, Sequence

from . import ToolInfo
from .regex_tool import RegexDetector
from .entropy_tool import EntropyDetector
from .trufflehog_tool import TruffleHogDetector
from .privacy_filter_tool import PrivacyFilterDetector
from .llm_pii_tool import LLMPIIDetectorTool
from .business_logic_tool import BusinessLogicDetector
from .path_anonymizer_tool import PathAnonymizerTransformer
from .capsule_scope_tool import CapsuleScopeTransformer
from .classifier_tool import ClassifierJudge


_TOOLS: tuple[Any, ...] = (
    RegexDetector(),
    EntropyDetector(),
    TruffleHogDetector(),
    PrivacyFilterDetector(),
    LLMPIIDetectorTool(),
    BusinessLogicDetector(),     # detector — runs before transformers/judge
    PathAnonymizerTransformer(),
    CapsuleScopeTransformer(),   # transformer — field-path exclusion
    ClassifierJudge(),
)

_BY_NAME: dict[str, Any] = {t.name: t for t in _TOOLS}


def iter_tools() -> Iterator[Any]:
    yield from _TOOLS


def all_tools() -> Sequence[Any]:
    return _TOOLS


def get(name: str) -> Any:
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KeyError(f"unknown security tool: {name!r}") from None


def iter_enabled(cfg: Any) -> Iterator[Any]:
    for tool in _TOOLS:
        if tool.enabled(cfg):
            yield tool


def describe_all(cfg: Any) -> list[ToolInfo]:
    return [tool.describe(cfg) for tool in _TOOLS]
