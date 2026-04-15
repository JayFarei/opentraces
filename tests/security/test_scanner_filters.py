"""Part E tests: field-filter calibration + base64 blob detection.

Covers the safe-to-skip path suffix/prefix lists adopted from
pi-trace-sanitizer's reference implementation, plus the base64 image
heuristic (sibling ``mimeType`` key + value length >= 256).

These helpers are consumed directly by Tier 1.8 LLM PII detection and
are exposed as module-level API so future tiers can share the same
filter logic.
"""

from __future__ import annotations

from opentraces.security.scanner import (
    _BASE64_MIN_LEN,
    _SAFE_FIELD_PREFIXES,
    _SAFE_FIELD_SUFFIXES,
    is_base64_blob,
    is_safe_field_path,
)


class TestSafeFieldPath:
    def test_suffix_type(self) -> None:
        assert is_safe_field_path("steps.0.type") is True

    def test_suffix_id(self) -> None:
        assert is_safe_field_path("steps.12.tool_calls.0.id") is True

    def test_suffix_timestamp(self) -> None:
        assert is_safe_field_path("meta.timestamp") is True

    def test_suffix_stop_reason(self) -> None:
        assert is_safe_field_path("steps.3.stopReason") is True

    def test_suffix_model(self) -> None:
        assert is_safe_field_path("steps.0.model") is True

    def test_suffix_provider(self) -> None:
        assert is_safe_field_path("steps.0.provider") is True

    def test_suffix_parent_id(self) -> None:
        assert is_safe_field_path("steps.0.parentId") is True

    def test_suffix_mime_type(self) -> None:
        assert is_safe_field_path("content.0.mimeType") is True

    def test_prefix_usage(self) -> None:
        assert is_safe_field_path("usage.input_tokens") is True

    def test_prefix_message_usage(self) -> None:
        assert is_safe_field_path("message.usage.total_tokens") is True

    def test_content_field_is_not_safe(self) -> None:
        assert is_safe_field_path("steps.0.content") is False

    def test_reasoning_is_not_safe(self) -> None:
        assert is_safe_field_path("steps.0.reasoning_content") is False

    def test_tool_input_is_not_safe(self) -> None:
        assert is_safe_field_path("steps.0.tool_calls.0.input.command") is False

    def test_empty_path_is_not_safe(self) -> None:
        assert is_safe_field_path("") is False

    def test_constants_cover_plan(self) -> None:
        # Plan: .type .id .timestamp .stopReason .model .provider .parentId .mimeType
        required = {".type", ".id", ".timestamp", ".stopReason",
                    ".model", ".provider", ".parentId", ".mimeType"}
        assert required.issubset(set(_SAFE_FIELD_SUFFIXES))
        assert "usage." in _SAFE_FIELD_PREFIXES
        assert "message.usage." in _SAFE_FIELD_PREFIXES


class TestBase64Blob:
    def _blob(self, length: int) -> str:
        return "A" * length

    def test_blob_with_sibling_mime_and_long_value(self) -> None:
        siblings = {"mimeType": "image/png"}
        assert is_base64_blob(self._blob(_BASE64_MIN_LEN), siblings) is True

    def test_blob_with_sibling_mime_but_short_value(self) -> None:
        siblings = {"mimeType": "image/png"}
        assert is_base64_blob(self._blob(_BASE64_MIN_LEN - 1), siblings) is False

    def test_blob_without_sibling_mime(self) -> None:
        siblings: dict[str, object] = {"other": "value"}
        assert is_base64_blob(self._blob(1024), siblings) is False

    def test_empty_siblings(self) -> None:
        assert is_base64_blob(self._blob(1024), {}) is False

    def test_threshold_is_256(self) -> None:
        assert _BASE64_MIN_LEN == 256

    def test_non_string_value_is_not_blob(self) -> None:
        siblings = {"mimeType": "image/png"}
        assert is_base64_blob(12345, siblings) is False  # type: ignore[arg-type]
