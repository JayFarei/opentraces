"""Comprehensive tests for the enrichment pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from opentraces_schema.models import (
    Observation,
    Step,
    TokenUsage,
    ToolCall,
)
from opentraces.enrichment.attribution import build_attribution
from opentraces.enrichment.dependencies import (
    extract_dependencies,
    extract_dependencies_from_imports,
    extract_dependencies_from_steps,
    infer_language_ecosystem,
)
from opentraces.enrichment.git_signals import (
    MAX_VCS_DIFF_CHARS,
    check_committed,
    detect_commits_from_steps,
    detect_vcs,
    extract_git_signals,
)
from opentraces.enrichment.metrics import compute_metrics
from opentraces.enrichment.snippets import (
    detect_language,
    estimate_line_range,
    extract_edited_lines,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_step(
    index: int = 0,
    tool_calls: list[ToolCall] | None = None,
    observations: list[Observation] | None = None,
    token_usage: TokenUsage | None = None,
    model: str | None = None,
    timestamp: str | None = None,
) -> Step:
    return Step(
        step_index=index,
        role="agent",
        tool_calls=tool_calls or [],
        observations=observations or [],
        token_usage=token_usage or TokenUsage(),
        model=model,
        timestamp=timestamp,
    )


def _make_edit_tc(
    file_path: str,
    old_string: str,
    new_string: str,
    call_id: str = "tc_1",
) -> ToolCall:
    return ToolCall(
        tool_call_id=call_id,
        tool_name="Edit",
        input={
            "file_path": file_path,
            "old_string": old_string,
            "new_string": new_string,
        },
    )


def _make_write_tc(
    file_path: str,
    content: str,
    call_id: str = "tc_w1",
) -> ToolCall:
    return ToolCall(
        tool_call_id=call_id,
        tool_name="Write",
        input={
            "file_path": file_path,
            "content": content,
        },
    )


def _make_bash_tc(command: str, call_id: str = "tc_b1") -> ToolCall:
    return ToolCall(
        tool_call_id=call_id,
        tool_name="Bash",
        input={"command": command},
    )


# ---------------------------------------------------------------------------
# Git signals tests
# ---------------------------------------------------------------------------

class TestDetectVCS:
    """Tests for detect_vcs."""

    @patch("opentraces.enrichment.git_signals._run_git")
    def test_not_a_git_repo(self, mock_run):
        mock_run.return_value = (False, "")
        vcs = detect_vcs(Path("/tmp/nope"))
        assert vcs.type == "none"
        assert vcs.base_commit is None

    @patch("opentraces.enrichment.git_signals._run_git")
    def test_git_repo(self, mock_run):
        def side_effect(args, cwd):
            if args[0] == "rev-parse" and "--is-inside-work-tree" in args:
                return (True, "true")
            elif args[0] == "rev-parse" and "--abbrev-ref" in args:
                return (True, "main")
            elif args[0] == "rev-parse" and "HEAD" in args:
                return (True, "abc123def456")
            elif args[0] == "diff":
                return (True, "some diff")
            return (False, "")

        mock_run.side_effect = side_effect
        vcs = detect_vcs(Path("/tmp/myrepo"))
        assert vcs.type == "git"
        assert vcs.base_commit == "abc123def456"
        assert vcs.branch == "main"
        assert vcs.diff == "some diff"

    @patch("opentraces.enrichment.git_signals._run_git")
    def test_git_repo_truncates_large_diff(self, mock_run):
        large_diff = "x" * (MAX_VCS_DIFF_CHARS + 25)

        def side_effect(args, cwd):
            if args[0] == "rev-parse" and "--is-inside-work-tree" in args:
                return (True, "true")
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                return (True, "main")
            if args[0] == "rev-parse" and "HEAD" in args:
                return (True, "abc123def456")
            if args[0] == "diff":
                return (True, large_diff)
            return (False, "")

        mock_run.side_effect = side_effect
        vcs = detect_vcs(Path("/tmp/myrepo"))
        assert vcs.type == "git"
        assert vcs.diff is not None
        assert len(vcs.diff) == MAX_VCS_DIFF_CHARS
        assert len(vcs.diff) < len(large_diff)
        assert "[TRUNCATED opentraces.vcs.diff omitted_chars=25]" in vcs.diff


class TestCheckCommitted:
    """Tests for check_committed."""

    @patch("opentraces.enrichment.git_signals._run_git")
    def test_not_a_git_repo(self, mock_run):
        mock_run.return_value = (False, "")
        outcome = check_committed(Path("/tmp"), "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
        assert outcome.committed is False

    @patch("opentraces.enrichment.git_signals._run_git")
    def test_no_commits_in_range(self, mock_run):
        def side_effect(args, cwd):
            if args[0] == "rev-parse":
                return (True, "true")
            if args[0] == "log":
                return (True, "")
            return (False, "")

        mock_run.side_effect = side_effect
        outcome = check_committed(Path("/tmp"), "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
        assert outcome.committed is False

    @patch("opentraces.enrichment.git_signals._run_git")
    def test_commit_found(self, mock_run):
        def side_effect(args, cwd):
            if args[0] == "rev-parse":
                return (True, "true")
            if args[0] == "log":
                return (True, "deadbeef1234")
            if args[0] == "diff":
                return (True, "+added line")
            return (False, "")

        mock_run.side_effect = side_effect
        outcome = check_committed(Path("/tmp"), "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
        assert outcome.committed is True
        assert outcome.commit_sha == "deadbeef1234"
        assert outcome.patch == "+added line"


class TestExtractGitSignals:
    """Tests for extract_git_signals."""

    @patch("opentraces.enrichment.git_signals._run_git")
    def test_non_git_returns_none_vcs(self, mock_run):
        mock_run.return_value = (False, "")
        vcs, outcome = extract_git_signals("/tmp/nope")
        assert vcs.type == "none"
        assert outcome.committed is False


# ---------------------------------------------------------------------------
# Commit detection from session steps
# ---------------------------------------------------------------------------

class TestDetectCommitsFromSteps:
    """Tests for detect_commits_from_steps (session-data-only commit detection)."""

    def test_no_bash_calls(self):
        """Sessions without Bash calls have no commits."""
        steps = [_make_step(0, tool_calls=[
            ToolCall(tool_call_id="tc1", tool_name="Read", input={"file_path": "/foo"}),
        ])]
        outcome = detect_commits_from_steps(steps)
        assert outcome.committed is False

    def test_bash_without_git_commit(self):
        """Bash calls that aren't git commit don't trigger."""
        steps = [_make_step(0, tool_calls=[_make_bash_tc("ls -la")])]
        outcome = detect_commits_from_steps(steps)
        assert outcome.committed is False

    def test_git_commit_with_success(self):
        """Bash git commit with [branch sha] output -> committed=True."""
        tc = ToolCall(
            tool_call_id="tc1",
            tool_name="Bash",
            input={"command": 'git commit -m "fix: the bug"'},
        )
        obs = Observation(
            source_call_id="tc1",
            content="[main abc1234] fix: the bug\n 2 files changed, 10 insertions(+)",
        )
        steps = [Step(
            step_index=1, role="agent",
            tool_calls=[tc], observations=[obs],
        )]
        outcome = detect_commits_from_steps(steps)
        assert outcome.committed is True
        assert outcome.commit_sha == "abc1234"
        assert outcome.description == "fix: the bug"
        assert outcome.signal_confidence == "derived"

    def test_git_commit_root_commit(self):
        """First commit in a repo has (root-commit) in output."""
        tc = ToolCall(
            tool_call_id="tc1",
            tool_name="Bash",
            input={"command": 'git commit -m "initial commit"'},
        )
        obs = Observation(
            source_call_id="tc1",
            content="[main (root-commit) def5678] initial commit\n 1 file changed",
        )
        steps = [Step(
            step_index=1, role="agent",
            tool_calls=[tc], observations=[obs],
        )]
        outcome = detect_commits_from_steps(steps)
        assert outcome.committed is True
        assert outcome.commit_sha == "def5678"

    def test_git_commit_no_output(self):
        """git commit command but no observation -> not committed."""
        tc = ToolCall(
            tool_call_id="tc1",
            tool_name="Bash",
            input={"command": 'git commit -m "wip"'},
        )
        obs = Observation(
            source_call_id="tc1",
            content="",  # empty output (maybe failed)
        )
        steps = [Step(
            step_index=1, role="agent",
            tool_calls=[tc], observations=[obs],
        )]
        outcome = detect_commits_from_steps(steps)
        assert outcome.committed is False

    def test_git_commit_error_output(self):
        """git commit that fails (nothing to commit) -> not committed."""
        tc = ToolCall(
            tool_call_id="tc1",
            tool_name="Bash",
            input={"command": 'git commit -m "wip"'},
        )
        obs = Observation(
            source_call_id="tc1",
            content="On branch main\nnothing to commit, working tree clean",
        )
        steps = [Step(
            step_index=1, role="agent",
            tool_calls=[tc], observations=[obs],
        )]
        outcome = detect_commits_from_steps(steps)
        assert outcome.committed is False

    def test_multiple_commits_uses_last(self):
        """Multiple commits in one session -> uses the last SHA."""
        tc1 = ToolCall(
            tool_call_id="tc1", tool_name="Bash",
            input={"command": 'git commit -m "first"'},
        )
        obs1 = Observation(
            source_call_id="tc1",
            content="[main aaa1111] first\n 1 file changed",
        )
        tc2 = ToolCall(
            tool_call_id="tc2", tool_name="Bash",
            input={"command": 'git commit -m "second"'},
        )
        obs2 = Observation(
            source_call_id="tc2",
            content="[main bbb2222] second\n 1 file changed",
        )
        steps = [
            Step(step_index=1, role="agent", tool_calls=[tc1], observations=[obs1]),
            Step(step_index=2, role="agent", tool_calls=[tc2], observations=[obs2]),
        ]
        outcome = detect_commits_from_steps(steps)
        assert outcome.committed is True
        assert outcome.commit_sha == "bbb2222"
        assert outcome.description == "second"

    def test_heredoc_commit_message(self):
        """git commit with heredoc message (Claude Code's common pattern)."""
        tc = ToolCall(
            tool_call_id="tc1",
            tool_name="Bash",
            input={"command": """git commit -m "$(cat <<'EOF'\nfeat: add login\n\nCo-Authored-By: Claude\nEOF\n)\" """},
        )
        obs = Observation(
            source_call_id="tc1",
            content="[main cafe123] feat: add login\n 3 files changed, 45 insertions(+)",
        )
        steps = [Step(
            step_index=1, role="agent",
            tool_calls=[tc], observations=[obs],
        )]
        outcome = detect_commits_from_steps(steps)
        assert outcome.committed is True
        assert outcome.commit_sha == "cafe123"

    def test_feature_branch_commit(self):
        """Commit to a feature branch with slashes."""
        tc = ToolCall(
            tool_call_id="tc1",
            tool_name="Bash",
            input={"command": 'git commit -m "fix stuff"'},
        )
        obs = Observation(
            source_call_id="tc1",
            content="[feature/auth-flow 9876543] fix stuff\n 1 file changed",
        )
        steps = [Step(
            step_index=1, role="agent",
            tool_calls=[tc], observations=[obs],
        )]
        outcome = detect_commits_from_steps(steps)
        assert outcome.committed is True
        assert outcome.commit_sha == "9876543"


# ---------------------------------------------------------------------------
# Attribution tests
# ---------------------------------------------------------------------------

class TestBuildAttribution:
    """Tests for build_attribution."""

    def test_no_edits_returns_none(self):
        steps = [_make_step(0, tool_calls=[_make_bash_tc("ls")])]
        result = build_attribution(steps)
        assert result is None

    def test_single_edit(self):
        tc = _make_edit_tc("/src/app.py", "old code", "new code")
        steps = [_make_step(0, tool_calls=[tc])]
        attr = build_attribution(steps)

        assert attr is not None
        assert attr.experimental is True
        assert len(attr.files) == 1
        assert attr.files[0].path == "/src/app.py"
        # Single edit -> high confidence
        conv = attr.files[0].conversations[0]
        assert conv.ranges[0].confidence == "high"

    def test_write_new_file(self):
        content = "line1\nline2\nline3\n"
        tc = _make_write_tc("/src/new.py", content)
        steps = [_make_step(0, tool_calls=[tc])]
        attr = build_attribution(steps)

        assert attr is not None
        assert len(attr.files) == 1
        assert attr.files[0].path == "/src/new.py"
        conv = attr.files[0].conversations[0]
        assert conv.ranges[0].start_line == 1
        assert conv.ranges[0].end_line == 3

    def test_multi_edit_no_overlap(self):
        # Write file first, then edit two different parts
        write_tc = _make_write_tc("/src/f.py", "aaa\nbbb\nccc\nddd\neee\n", call_id="w1")
        edit1 = _make_edit_tc("/src/f.py", "aaa", "AAA", call_id="e1")
        edit2 = _make_edit_tc("/src/f.py", "eee", "EEE", call_id="e2")

        steps = [
            _make_step(0, tool_calls=[write_tc]),
            _make_step(1, tool_calls=[edit1]),
            _make_step(2, tool_calls=[edit2]),
        ]
        attr = build_attribution(steps)

        assert attr is not None
        assert len(attr.files) == 1
        # 3 steps touch this file -> medium confidence (write + 2 edits, no overlap after write)
        file_attr = attr.files[0]
        assert len(file_attr.conversations) == 3

    def test_overlapping_edits_low_confidence(self):
        # Two edits to the same line range
        write_tc = _make_write_tc("/src/f.py", "line1\nline2\nline3\n", call_id="w1")
        edit1 = _make_edit_tc("/src/f.py", "line1", "LINE1", call_id="e1")
        edit2 = _make_edit_tc("/src/f.py", "LINE1", "LINE1_v2", call_id="e2")

        steps = [
            _make_step(0, tool_calls=[write_tc]),
            _make_step(1, tool_calls=[edit1]),
            _make_step(2, tool_calls=[edit2]),
        ]
        attr = build_attribution(steps)

        assert attr is not None
        # All three touch the file, and edits overlap on line 1 -> low confidence
        file_attr = attr.files[0]
        for conv in file_attr.conversations:
            for r in conv.ranges:
                assert r.confidence == "low"

    def test_content_hash_present(self):
        tc = _make_edit_tc("/src/app.py", "old", "new")
        steps = [_make_step(0, tool_calls=[tc])]
        attr = build_attribution(steps)

        assert attr is not None
        r = attr.files[0].conversations[0].ranges[0]
        assert r.content_hash is not None
        assert len(r.content_hash) == 8  # md5 truncated to 8 hex

    def test_with_outcome_patch(self):
        tc = _make_edit_tc("/src/app.py", "old", "new")
        steps = [_make_step(0, tool_calls=[tc])]
        patch = """--- a/src/app.py
+++ b/src/app.py
@@ -1,1 +1,1 @@
-old
+new
--- a/src/other.py
+++ b/src/other.py
@@ -5,2 +5,3 @@
+unaccounted
"""
        attr = build_attribution(steps, outcome_patch=patch)
        assert attr is not None
        # src/app.py is attributed, src/other.py is unaccounted
        paths = {f.path for f in attr.files}
        assert "/src/app.py" in paths


# ---------------------------------------------------------------------------
# Dependencies tests
# ---------------------------------------------------------------------------

class TestExtractDependencies:
    """Tests for extract_dependencies from manifest files."""

    def test_package_json(self, tmp_path):
        pj = tmp_path / "package.json"
        pj.write_text(json.dumps({
            "dependencies": {"react": "^18.0", "next": "^14.0"},
            "devDependencies": {"jest": "^29.0"},
        }))
        deps = extract_dependencies(tmp_path)
        assert deps == ["jest", "next", "react"]

    def test_requirements_txt(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("flask>=3.0\nrequests==2.31\n# comment\npydantic~=2.0\n")
        deps = extract_dependencies(tmp_path)
        assert deps == ["flask", "pydantic", "requests"]

    def test_pyproject_toml(self, tmp_path):
        pp = tmp_path / "pyproject.toml"
        pp.write_text("""[project]
name = "myapp"
dependencies = [
    "click>=8.0",
    "rich",
    "pydantic>=2.0",
]
""")
        deps = extract_dependencies(tmp_path)
        assert deps == ["click", "pydantic", "rich"]

    def test_gemfile(self, tmp_path):
        gf = tmp_path / "Gemfile"
        gf.write_text("""source 'https://rubygems.org'
gem 'rails', '~> 7.0'
gem 'pg'
gem 'puma', '>= 5.0'
""")
        deps = extract_dependencies(tmp_path)
        assert deps == ["pg", "puma", "rails"]

    def test_go_mod(self, tmp_path):
        gm = tmp_path / "go.mod"
        gm.write_text("""module example.com/myapp

go 1.21

require (
\tgithub.com/gin-gonic/gin v1.9.1
\tgithub.com/lib/pq v1.10.9
)
""")
        deps = extract_dependencies(tmp_path)
        assert deps == ["github.com/gin-gonic/gin", "github.com/lib/pq"]

    def test_no_manifests(self, tmp_path):
        deps = extract_dependencies(tmp_path)
        assert deps == []


class TestExtractDependenciesFromSteps:
    """Tests for extracting dependencies from Bash tool calls."""

    def test_npm_install(self):
        steps = [_make_step(0, tool_calls=[_make_bash_tc("npm install lodash axios")])]
        deps = extract_dependencies_from_steps(steps)
        assert deps == ["axios", "lodash"]

    def test_pip_install(self):
        steps = [_make_step(0, tool_calls=[_make_bash_tc("pip install flask>=3.0 requests")])]
        deps = extract_dependencies_from_steps(steps)
        assert deps == ["flask", "requests"]

    def test_gem_install(self):
        steps = [_make_step(0, tool_calls=[_make_bash_tc("gem install rails bundler")])]
        deps = extract_dependencies_from_steps(steps)
        assert deps == ["bundler", "rails"]

    def test_no_install_commands(self):
        steps = [_make_step(0, tool_calls=[_make_bash_tc("ls -la")])]
        deps = extract_dependencies_from_steps(steps)
        assert deps == []

    def test_ignores_flags(self):
        steps = [_make_step(0, tool_calls=[_make_bash_tc("npm install --save-dev jest")])]
        deps = extract_dependencies_from_steps(steps)
        assert deps == ["jest"]


# ---------------------------------------------------------------------------
# Metrics tests
# ---------------------------------------------------------------------------

class TestComputeMetrics:
    """Tests for compute_metrics."""

    def test_token_aggregation(self):
        steps = [
            _make_step(0, token_usage=TokenUsage(input_tokens=100, output_tokens=50)),
            _make_step(1, token_usage=TokenUsage(input_tokens=200, output_tokens=100)),
        ]
        m = compute_metrics(steps)
        assert m.total_steps == 2
        assert m.total_input_tokens == 300
        assert m.total_output_tokens == 150

    def test_cache_hit_rate(self):
        steps = [
            _make_step(0, token_usage=TokenUsage(
                input_tokens=100, cache_read_tokens=300,
            )),
        ]
        m = compute_metrics(steps)
        # cache_hit_rate = 300 / (100 + 300) = 0.75
        assert m.cache_hit_rate == 0.75

    def test_cache_hit_rate_zero_tokens(self):
        steps = [_make_step(0)]
        m = compute_metrics(steps)
        assert m.cache_hit_rate is None

    def test_duration_from_timestamps(self):
        steps = [
            _make_step(0, timestamp="2026-03-27T10:00:00Z"),
            _make_step(1, timestamp="2026-03-27T10:05:00Z"),
        ]
        m = compute_metrics(steps)
        assert m.total_duration_s == 300.0

    def test_cost_estimation_sonnet(self):
        steps = [
            _make_step(
                0,
                model="anthropic/claude-sonnet-4-20250514",
                token_usage=TokenUsage(input_tokens=1_000_000, output_tokens=100_000),
            ),
        ]
        m = compute_metrics(steps)
        # input: 1M * $3/1M = $3, output: 100k * $15/1M = $1.5 -> $4.5
        assert m.estimated_cost_usd is not None
        assert abs(m.estimated_cost_usd - 4.5) < 0.01

    def test_cost_estimation_opus(self):
        steps = [
            _make_step(
                0,
                model="anthropic/claude-opus-4-20250514",
                token_usage=TokenUsage(input_tokens=1_000_000, output_tokens=100_000),
            ),
        ]
        m = compute_metrics(steps)
        # input: 1M * $15/1M = $15, output: 100k * $75/1M = $7.5 -> $22.5
        assert m.estimated_cost_usd is not None
        assert abs(m.estimated_cost_usd - 22.5) < 0.01

    def test_cost_estimation_haiku(self):
        steps = [
            _make_step(
                0,
                model="anthropic/claude-haiku-3.5-20250514",
                token_usage=TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000),
            ),
        ]
        m = compute_metrics(steps)
        # input: 1M * $0.80/1M = $0.80, output: 1M * $4/1M = $4 -> $4.80
        assert m.estimated_cost_usd is not None
        assert abs(m.estimated_cost_usd - 4.8) < 0.01

    def test_custom_pricing(self):
        steps = [
            _make_step(
                0,
                model="anthropic/claude-sonnet-4",
                token_usage=TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000),
            ),
        ]
        custom = {"sonnet": {"input": 10.0, "output": 30.0, "cache_read": 1.0}}
        m = compute_metrics(steps, pricing=custom)
        # input: 1M * $10/1M = $10, output: 1M * $30/1M = $30 -> $40
        assert m.estimated_cost_usd is not None
        assert abs(m.estimated_cost_usd - 40.0) < 0.01

    def test_no_steps(self):
        m = compute_metrics([])
        assert m.total_steps == 0
        assert m.total_input_tokens == 0
        assert m.estimated_cost_usd is None

    def test_single_timestamp_no_duration(self):
        steps = [_make_step(0, timestamp="2026-03-27T10:00:00Z")]
        m = compute_metrics(steps)
        assert m.total_duration_s is None


# ---------------------------------------------------------------------------
# Snippets / language detection tests
# ---------------------------------------------------------------------------

class TestDetectLanguage:
    """Tests for detect_language."""

    @pytest.mark.parametrize(
        "path, expected",
        [
            ("main.py", "python"),
            ("index.js", "javascript"),
            ("App.tsx", "tsx"),
            ("style.css", "css"),
            ("config.yaml", "yaml"),
            ("data.json", "json"),
            ("Makefile", None),
            ("script.sh", "shell"),
            ("lib.rs", "rust"),
            ("main.go", "go"),
            ("App.vue", "vue"),
            ("Component.svelte", "svelte"),
            ("query.sql", "sql"),
            ("main.zig", "zig"),
            ("page.html", "html"),
            ("settings.toml", "toml"),
            ("app.dart", "dart"),
            ("main.kt", "kotlin"),
            ("app.swift", "swift"),
            ("program.c", "c"),
            ("program.cpp", "cpp"),
            ("header.h", "c"),
            ("app.cs", "csharp"),
            ("index.php", "php"),
            ("config.zsh", "shell"),
            ("notes.md", "markdown"),
            ("config.yml", "yaml"),
            ("lib.ex", "elixir"),
            ("test.exs", "elixir"),
            ("core.clj", "clojure"),
            ("Main.scala", "scala"),
            ("analysis.r", "r"),
            ("script.lua", "lua"),
            ("main.nim", "nim"),
            ("Main.java", "java"),
        ],
    )
    def test_extensions(self, path, expected):
        assert detect_language(path) == expected

    def test_dockerfile_special(self):
        assert detect_language("Dockerfile") == "dockerfile"
        assert detect_language("path/to/Dockerfile") == "dockerfile"

    def test_unknown_extension(self):
        assert detect_language("file.xyz") is None


class TestEstimateLineRange:
    """Tests for estimate_line_range."""

    def test_single_line(self):
        assert estimate_line_range("hello", 1) == (1, 1)

    def test_multi_line(self):
        assert estimate_line_range("a\nb\nc\n", 1) == (1, 3)

    def test_offset(self):
        assert estimate_line_range("a\nb\n", 10) == (10, 11)

    def test_empty(self):
        assert estimate_line_range("", 1) == (1, 1)

    def test_no_trailing_newline(self):
        assert estimate_line_range("a\nb\nc", 1) == (1, 3)


class TestExtractEditedLines:
    """Tests for extract_edited_lines."""

    def test_with_file_content(self):
        content = "line1\nline2\nline3\nline4\n"
        start, end = extract_edited_lines("line2", "NEW2\nNEW2b", content)
        assert start == 2
        assert end == 3

    def test_no_file_content(self):
        start, end = extract_edited_lines("old", "new")
        assert start is None
        assert end is None

    def test_old_not_found(self):
        content = "aaa\nbbb\n"
        start, end = extract_edited_lines("zzz", "new", content)
        assert start is None
        assert end is None

    def test_first_line(self):
        content = "first\nsecond\nthird\n"
        start, end = extract_edited_lines("first", "FIRST", content)
        assert start == 1
        assert end == 1


# ---------------------------------------------------------------------------
# Language ecosystem inference tests
# ---------------------------------------------------------------------------

def _make_tc(tool_name: str, **kwargs) -> ToolCall:
    """Helper to create a ToolCall with arbitrary input."""
    return ToolCall(
        tool_call_id="tc_test",
        tool_name=tool_name,
        input=kwargs,
    )


class TestInferLanguageEcosystem:
    """Tests for infer_language_ecosystem."""

    def test_py_and_tsx_files(self):
        steps = [
            _make_step(0, tool_calls=[
                _make_tc("Edit", file_path="/src/app.py", old_string="a", new_string="b"),
                _make_tc("Edit", file_path="/src/App.tsx", old_string="a", new_string="b"),
            ]),
        ]
        result = infer_language_ecosystem(steps)
        assert result == ["python", "typescript"]

    def test_only_md_files(self):
        steps = [
            _make_step(0, tool_calls=[
                _make_tc("Read", path="/docs/README.md"),
            ]),
        ]
        result = infer_language_ecosystem(steps)
        assert result == []

    def test_bash_npm_install(self):
        steps = [
            _make_step(0, tool_calls=[
                _make_bash_tc("npm install foo"),
            ]),
        ]
        result = infer_language_ecosystem(steps)
        assert result == ["javascript"]

    def test_empty_steps(self):
        result = infer_language_ecosystem([])
        assert result == []

    def test_deduplication(self):
        steps = [
            _make_step(0, tool_calls=[
                _make_tc("Edit", file_path="/src/a.py", old_string="a", new_string="b"),
                _make_tc("Edit", file_path="/src/b.py", old_string="a", new_string="b"),
            ]),
        ]
        result = infer_language_ecosystem(steps)
        assert result == ["python"]

    def test_bash_python_command(self):
        steps = [
            _make_step(0, tool_calls=[
                _make_bash_tc("python -m pytest tests/"),
            ]),
        ]
        result = infer_language_ecosystem(steps)
        assert result == ["python"]

    def test_bash_cargo(self):
        steps = [
            _make_step(0, tool_calls=[
                _make_bash_tc("cargo build --release"),
            ]),
        ]
        result = infer_language_ecosystem(steps)
        assert result == ["rust"]

    def test_bash_go(self):
        steps = [
            _make_step(0, tool_calls=[
                _make_bash_tc("go test ./..."),
            ]),
        ]
        result = infer_language_ecosystem(steps)
        assert result == ["go"]

    def test_multiple_ecosystems_from_extensions(self):
        steps = [
            _make_step(0, tool_calls=[
                _make_tc("Edit", file_path="/src/main.rs", old_string="a", new_string="b"),
                _make_tc("Edit", file_path="/src/lib.go", old_string="a", new_string="b"),
                _make_tc("Edit", file_path="/src/app.rb", old_string="a", new_string="b"),
            ]),
        ]
        result = infer_language_ecosystem(steps)
        assert result == ["go", "ruby", "rust"]

    def test_path_key_also_works(self):
        steps = [
            _make_step(0, tool_calls=[
                _make_tc("Read", path="/src/main.swift"),
            ]),
        ]
        result = infer_language_ecosystem(steps)
        assert result == ["swift"]


# ---------------------------------------------------------------------------
# Import-based dependency extraction tests
# ---------------------------------------------------------------------------

def _make_obs_step(content: str) -> Step:
    """Helper to create a step with a single observation containing the given content."""
    return _make_step(
        0,
        observations=[Observation(source_call_id="tc_1", content=content)],
    )


class TestExtractDependenciesFromImports:
    """Tests for extract_dependencies_from_imports."""

    def test_python_from_import_with_arrow(self):
        """U+2192 arrow line numbers are stripped correctly."""
        step = _make_obs_step("  42\u2192from pydantic import BaseModel")
        result = extract_dependencies_from_imports([step])
        assert "pydantic" in result

    def test_python_internal_package_filtered(self):
        """Internal packages matching project_name are filtered."""
        step = _make_obs_step("from backend.api import router")
        result = extract_dependencies_from_imports([step], project_name="backend")
        assert result == []

    def test_js_import_type_extracts_package_not_type(self):
        """import type { Stripe } from 'stripe' extracts 'stripe', not 'type'."""
        step = _make_obs_step("import type { Stripe } from 'stripe'")
        result = extract_dependencies_from_imports([step])
        assert "stripe" in result
        assert "type" not in result

    def test_js_import_react(self):
        step = _make_obs_step("import React from 'react'")
        result = extract_dependencies_from_imports([step])
        assert "react" in result

    def test_python_stdlib_filtered(self):
        step = _make_obs_step("import os")
        result = extract_dependencies_from_imports([step])
        assert "os" not in result

    def test_js_require(self):
        step = _make_obs_step("const express = require('express')")
        result = extract_dependencies_from_imports([step])
        assert "express" in result

    def test_relative_import_filtered(self):
        step = _make_obs_step("import SectionRule from '../components/SectionRule'")
        result = extract_dependencies_from_imports([step])
        assert result == []

    def test_node_builtin_filtered(self):
        step = _make_obs_step("import fs from 'fs'")
        result = extract_dependencies_from_imports([step])
        assert "fs" not in result

    def test_scoped_npm_package(self):
        step = _make_obs_step("import { useQuery } from '@tanstack/react-query'")
        result = extract_dependencies_from_imports([step])
        assert "@tanstack/react-query" in result

    def test_path_alias_filtered(self):
        step = _make_obs_step("import { Button } from '@/components/Button'")
        result = extract_dependencies_from_imports([step])
        assert result == []

    def test_python_from_import_simple(self):
        step = _make_obs_step("from flask import Flask")
        result = extract_dependencies_from_imports([step])
        assert "flask" in result

    def test_python_import_simple(self):
        step = _make_obs_step("import flask")
        result = extract_dependencies_from_imports([step])
        assert "flask" in result

    def test_go_import(self):
        step = _make_obs_step('import "github.com/gin-gonic/gin"')
        result = extract_dependencies_from_imports([step])
        assert "github.com/gin-gonic/gin" in result

    def test_ruby_require(self):
        step = _make_obs_step("require 'sinatra'")
        result = extract_dependencies_from_imports([step])
        assert "sinatra" in result

    def test_common_internal_names_filtered(self):
        step = _make_obs_step("from utils import helper\nfrom config import settings")
        result = extract_dependencies_from_imports([step])
        assert "utils" not in result
        assert "config" not in result

    def test_camelcase_component_filtered(self):
        """CamelCase names starting with uppercase are filtered (React components)."""
        step = _make_obs_step("import MyComponent from 'MyComponent'")
        result = extract_dependencies_from_imports([step])
        assert "MyComponent" not in result

    def test_well_known_package_bypasses_heuristics(self):
        """Well-known packages pass even if heuristics would reject."""
        # 'test' is in COMMON_INTERNAL_NAMES but 'pytest' is in WELL_KNOWN_PACKAGES
        step = _make_obs_step("import pytest")
        result = extract_dependencies_from_imports([step])
        assert "pytest" in result

    def test_empty_observations(self):
        step = _make_step(0)
        result = extract_dependencies_from_imports([step])
        assert result == []

    def test_merges_with_install_commands(self):
        """Results include deps from both imports and install commands."""
        step = Step(
            step_index=0,
            role="agent",
            tool_calls=[_make_bash_tc("pip install requests")],
            observations=[Observation(source_call_id="tc_1", content="from flask import Flask")],
            token_usage=TokenUsage(),
        )
        result = extract_dependencies_from_imports([step])
        assert "flask" in result
        assert "requests" in result
