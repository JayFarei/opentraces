"""Comprehensive tests for the enrichment pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from opentraces_schema.models import (
    Observation,
    Outcome,
    Step,
    TokenUsage,
    ToolCall,
    VCS,
)
from opentraces.enrichment.attribution import build_attribution
from opentraces.enrichment.dependencies import (
    extract_dependencies,
    extract_dependencies_from_steps,
)
from opentraces.enrichment.git_signals import (
    check_committed,
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
