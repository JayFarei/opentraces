"""Tests for plan 041 R8: Attribution.unaccounted_files surfaces files
changed at the filesystem level that weren't produced by any Edit/Write
tool call (typically Bash-applied edits: sed -i, codemods).
"""

from __future__ import annotations

from opentraces.enrichment.attribution import build_attribution
from opentraces_schema import Observation, Step, TokenUsage, ToolCall


def _step(idx, tool_calls=None, observations=None, model=None):
    return Step(
        step_index=idx,
        role="agent",
        tool_calls=tool_calls or [],
        observations=observations or [],
        token_usage=TokenUsage(),
        model=model,
    )


def _edit(path, old, new, call_id="e"):
    return ToolCall(
        tool_call_id=call_id,
        tool_name="Edit",
        input={"file_path": path, "old_string": old, "new_string": new},
    )


PATCH_MULTI = """\
diff --git a/src/app.py b/src/app.py
index 111..222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,1 +1,1 @@
-old
+new
diff --git a/src/bash_touched.py b/src/bash_touched.py
index 333..444 100644
--- a/src/bash_touched.py
+++ b/src/bash_touched.py
@@ -5,2 +5,3 @@
+line added by sed
"""


class TestUnaccountedFromCommitPatch:
    def test_patch_file_not_edited_is_unaccounted(self):
        tc = _edit("src/app.py", "old", "new")
        steps = [_step(0, tool_calls=[tc])]
        attr = build_attribution(steps, outcome_patch=PATCH_MULTI)
        assert attr is not None
        assert attr.unaccounted_files is not None
        assert "src/bash_touched.py" in attr.unaccounted_files
        assert "src/app.py" not in attr.unaccounted_files

    def test_all_patch_files_attributed_none_unaccounted(self):
        # Attribute both files via Edits → no unaccounted.
        tc1 = _edit("src/app.py", "old", "new", call_id="e1")
        tc2 = _edit("src/bash_touched.py", "foo", "line added by sed", call_id="e2")
        steps = [_step(0, tool_calls=[tc1]), _step(1, tool_calls=[tc2])]
        attr = build_attribution(steps, outcome_patch=PATCH_MULTI)
        # unaccounted_files may be None (never populated) or empty list.
        assert not attr.unaccounted_files


class TestUnaccountedFromEndStateChangedFiles:
    def test_end_state_changed_files_surface_unaccounted(self):
        # No commit patch, but the Stop hook captured `git diff HEAD`
        # showing two changed files. Only one was touched by Edit.
        tc = _edit("/abs/src/app.py", "old", "new")
        steps = [_step(0, tool_calls=[tc])]
        attr = build_attribution(
            steps,
            outcome_patch=None,
            end_state_changed_files=["src/app.py", "src/bash_codemod.py"],
        )
        assert attr.unaccounted_files is not None
        assert "src/bash_codemod.py" in attr.unaccounted_files
        assert "src/app.py" not in attr.unaccounted_files

    def test_end_state_ignores_already_attributed_basename(self):
        # Edit on absolute path, hook reports repo-relative. Suffix match
        # should treat the Edit as already-attributing src/app.py.
        tc = _edit("/Users/x/proj/src/app.py", "o", "n")
        steps = [_step(0, tool_calls=[tc])]
        attr = build_attribution(
            steps,
            end_state_changed_files=["src/app.py"],
        )
        assert not attr.unaccounted_files

    def test_no_inputs_means_unaccounted_is_none(self):
        tc = _edit("src/app.py", "old", "new")
        steps = [_step(0, tool_calls=[tc])]
        attr = build_attribution(steps)
        assert attr.unaccounted_files is None
