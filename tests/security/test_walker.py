"""Unit tests for the shared walker helpers."""

from __future__ import annotations

from opentraces.security.walker import (
    ensure_security_metadata,
    locate_substrings,
    redact_spans,
)


class TestLocateSubstrings:
    def test_finds_single_occurrence(self):
        assert locate_substrings("the key is AKIA123", ["AKIA123"]) == [
            ("AKIA123", 11, 18)
        ]

    def test_finds_every_occurrence(self):
        out = locate_substrings("xx yy xx", ["xx"])
        assert out == [("xx", 0, 2), ("xx", 6, 8)]

    def test_multiple_substrings(self):
        out = locate_substrings("a=AAA b=BBB", ["AAA", "BBB"])
        assert ("AAA", 2, 5) in out
        assert ("BBB", 8, 11) in out

    def test_missing_substring_yields_nothing(self):
        assert locate_substrings("nothing here", ["secret"]) == []

    def test_empty_substring_skipped(self):
        assert locate_substrings("abc", ["", "b"]) == [("b", 1, 2)]


class TestRedactSpans:
    def test_replaces_span(self):
        assert redact_spans("key AKIA123 end", [(4, 11)]) == "key [REDACTED] end"

    def test_merges_overlapping_spans(self):
        # Overlapping spans collapse into one placeholder.
        assert redact_spans("abcdef", [(1, 4), (2, 5)]) == "a[REDACTED]f"

    def test_no_spans_returns_input(self):
        assert redact_spans("untouched", []) == "untouched"


class TestEnsureSecurityMetadata:
    def test_creates_missing_block(self):
        class _Rec:
            metadata: dict = {}

        rec = _Rec()
        rec.metadata = {}
        sec = ensure_security_metadata(rec)
        assert sec == {}
        assert rec.metadata["security"] is sec

    def test_replaces_non_dict_value(self):
        class _Rec:
            metadata: dict = {}

        rec = _Rec()
        rec.metadata = {"security": "corrupt"}
        sec = ensure_security_metadata(rec)
        assert sec == {}
        assert rec.metadata["security"] is sec

    def test_returns_existing_block(self):
        class _Rec:
            metadata: dict = {}

        rec = _Rec()
        existing = {"tools_applied": ["regex"]}
        rec.metadata = {"security": existing}
        assert ensure_security_metadata(rec) is existing
