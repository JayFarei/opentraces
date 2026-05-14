"""HuggingFace ``openai/privacy-filter`` PII NER model wrapper.

The model is a BERT-style token-classification head trained to identify
personally-identifiable information (PERSON, EMAIL, PHONE, etc.) in
free-form text. We expose a thin :class:`PrivacyFilterModel` wrapper that
lazy-imports ``transformers`` and caches a singleton ``pipeline`` so the
~500MB model load happens at most once per process.

The model is opt-in: it requires the ``transformers`` and ``torch`` packages
which are not part of the default ``opentraces`` install. Users opt in via
``opentraces setup privacy-filter`` (Phase 10 CLI command) which installs
the dependencies, prefetches the model, and flips
``cfg.security.privacy_filter.enabled = True``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PrivacyFilterSpan:
    """One PII span returned by the model."""

    entity_type: str
    matched_text: str
    start: int
    end: int
    score: float


class PrivacyFilterMissingError(RuntimeError):
    """Raised when the ``transformers`` package or model is unavailable."""


_MISSING_HINT = (
    "openai/privacy-filter requires the 'transformers' package and the model "
    "to be downloaded. Run 'opentraces setup privacy-filter' to install both."
)


class PrivacyFilterModel:
    """Per-process singleton wrapper around the HF token-classification pipeline.

    Construction is cheap; the heavy lifting happens on first :meth:`detect`
    call (model download / load). Re-use the same instance across calls to
    amortise the ~1s warm-up.
    """

    _shared: "PrivacyFilterModel | None" = None

    def __init__(
        self,
        model_name: str = "openai/privacy-filter",
        *,
        score_threshold: float = 0.7,
        device: int | str | None = None,
    ) -> None:
        self.model_name = model_name
        # Default threshold used when ``detect()`` is called without an
        # override; concurrent callers pass their own threshold explicitly
        # so they never race on this attribute.
        self.score_threshold = score_threshold
        self.device = device
        self._pipeline: Any = None
        self._available: bool | None = None

    # ------------------------------------------------------------------
    # Lazy load
    # ------------------------------------------------------------------

    @classmethod
    def shared(cls, model_name: str = "openai/privacy-filter") -> "PrivacyFilterModel":
        """Return a process-wide shared instance for the named model."""
        if cls._shared is None or cls._shared.model_name != model_name:
            cls._shared = cls(model_name=model_name)
        return cls._shared

    def _load(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        try:
            from transformers import pipeline as hf_pipeline  # type: ignore
        except ImportError as exc:
            self._available = False
            raise PrivacyFilterMissingError(_MISSING_HINT) from exc
        try:
            self._pipeline = hf_pipeline(
                "token-classification",
                model=self.model_name,
                aggregation_strategy="simple",
                device=self.device if self.device is not None else -1,
            )
        except Exception as exc:  # noqa: BLE001
            self._available = False
            raise PrivacyFilterMissingError(
                f"failed to load HF model {self.model_name!r}: {exc}"
            ) from exc
        self._available = True
        return self._pipeline

    def is_available(self) -> bool:
        """Return True iff the model can be loaded (probes once and caches)."""
        if self._available is not None:
            return self._available
        try:
            self._load()
        except PrivacyFilterMissingError:
            return False
        return True

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect(
        self,
        text: str,
        *,
        score_threshold: float | None = None,
    ) -> list[PrivacyFilterSpan]:
        """Return PII spans found in ``text`` with score >= threshold.

        ``score_threshold`` defaults to ``self.score_threshold`` but may be
        overridden per call. Pass an explicit value when the model is shared
        across callers that need different thresholds — relying on the
        instance attribute would race.
        """
        threshold = self.score_threshold if score_threshold is None else score_threshold
        if not text:
            return []
        try:
            pipe = self._load()
        except PrivacyFilterMissingError as exc:
            logger.warning("privacy-filter detect skipped: %s", exc)
            return []
        try:
            raw = pipe(text)
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning("privacy-filter inference failed: %s", exc)
            return []
        out: list[PrivacyFilterSpan] = []
        for entry in raw or []:
            try:
                score = float(entry.get("score", 0.0))
                if score < threshold:
                    continue
                start = int(entry.get("start", -1))
                end = int(entry.get("end", -1))
                if start < 0 or end <= start:
                    continue
                etype = str(entry.get("entity_group") or entry.get("entity") or "PII")
                matched = text[start:end]
                out.append(
                    PrivacyFilterSpan(
                        entity_type=etype,
                        matched_text=matched,
                        start=start,
                        end=end,
                        score=score,
                    )
                )
            except (TypeError, ValueError, KeyError):
                continue
        return out
