"""Table tests for ``companion_field_type`` (#143 move 2).

A pure path-classifier (sibling of ``scanner._classify_tool``) that maps an
inlined ctx/trail leaf path onto the EXISTING four-member ``FieldType`` taxonomy
plus an ``is_path_leaf`` flag. No new ``FieldType`` member is introduced.
"""

from __future__ import annotations

import pytest

from opentraces.security.companion import companion_field_type
from opentraces.security.scanner import FieldType


@pytest.mark.parametrize(
    "path,expected_ft,expected_is_path",
    [
        # ctx messages content → GENERAL (NER-relevant surface), not a path leaf.
        ("context_resume_packet.messages_layer.content.messages[0].content", FieldType.GENERAL, False),
        ("context_resume_packet.messages_layer.content.messages[1].content", FieldType.GENERAL, False),
        # ctx tool_registry descriptions / tool inputs → TOOL_INPUT.
        ("context_resume_packet.tool_registry_layer.content.tools[0].description", FieldType.TOOL_INPUT, False),
        ("context_resume_packet.tool_registry_layer.content.tools[0].input_schema.foo", FieldType.TOOL_INPUT, False),
        # trail authored_text → GENERAL prose (visited so path/secret rules run over code).
        ("trail_anchors[0].authored_text", FieldType.GENERAL, False),
        # path-shaped leaves → is_path_leaf True (always path-anonymize).
        ("context_resume_packet.runtime_state_layer.content.cwd", FieldType.GENERAL, True),
        ("trail_anchors[0].file_path", FieldType.GENERAL, True),
        ("context_resume_packet.system_layer.content.claude_md_set[0].path", FieldType.GENERAL, True),
        ("trail_anchors[1].current_path", FieldType.GENERAL, True),
        ("context_resume_packet.tool_registry_layer.content.tools[0].source_path", FieldType.TOOL_INPUT, True),
        # default / unknown leaf → GENERAL, not a path leaf.
        ("trace.summary.what_happened", FieldType.GENERAL, False),
        ("trace.title", FieldType.GENERAL, False),
    ],
)
def test_companion_field_type_table(path, expected_ft, expected_is_path):
    ft, is_path_leaf = companion_field_type(path)
    assert ft == expected_ft
    assert is_path_leaf is expected_is_path


def test_returns_two_tuple_of_fieldtype_and_bool():
    ft, is_path_leaf = companion_field_type("anything.here")
    assert isinstance(ft, FieldType)
    assert isinstance(is_path_leaf, bool)


def test_empty_path_is_general_non_path():
    ft, is_path_leaf = companion_field_type("")
    assert ft == FieldType.GENERAL
    assert is_path_leaf is False
