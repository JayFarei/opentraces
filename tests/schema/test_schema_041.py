"""Tests for plan 041 schema additions (R9-R19) on top of 0.3.0.

Every field is additive: existing 0.2.0 JSONL must still load cleanly, and
new fields default to None / empty so traces emitted before the bump remain
valid.
"""

from __future__ import annotations

import json

from opentraces_schema import (
    Agent,
    Attribution,
    AttributionConversation,
    AttributionFile,
    AttributionRange,
    GitLink,
    Task,
    TraceRecord,
)


class TestGitLink:
    def test_git_link_minimal(self):
        link = GitLink(vcs_type="git", revision="abc123", tier="tool_emitted")
        assert link.vcs_type == "git"
        assert link.revision == "abc123"
        assert link.tier == "tool_emitted"
        assert link.repo_url is None
        assert link.branch is None
        assert link.commit_reachable is None
        assert link.content_alive is None

    def test_git_link_full(self):
        link = GitLink(
            vcs_type="git",
            revision="abc123",
            repo_url="https://github.com/x/y",
            branch="main",
            tier="tool_emitted_with_divergence",
            commit_reachable=True,
            content_alive=False,
        )
        assert link.tier == "tool_emitted_with_divergence"
        assert link.branch == "main"
        assert link.commit_reachable is True
        assert link.content_alive is False

    def test_git_link_tier_values(self):
        # All four tiers defined by plan 041 R25 must be accepted.
        for tier in ("tool_emitted", "tool_emitted_with_divergence", "overlapping", "orphan"):
            link = GitLink(vcs_type="git", revision="r", tier=tier)
            assert link.tier == tier

    def test_jj_vcs_type(self):
        link = GitLink(vcs_type="jj", revision="change_xyz", tier="tool_emitted")
        assert link.vcs_type == "jj"


class TestAttributionRange041:
    def test_change_type_default_addition(self):
        r = AttributionRange(start_line=1, end_line=5)
        assert r.change_type == "addition"

    def test_change_type_values(self):
        for ct in ("addition", "modification", "deletion"):
            r = AttributionRange(start_line=1, end_line=5, change_type=ct)
            assert r.change_type == ct

    def test_range_original(self):
        r = AttributionRange(
            start_line=10,
            end_line=15,
            content_hash="murmur3:abc",
            confidence="high",
            original={"start_line": 9, "end_line": 14, "content_hash": "murmur3:old"},
        )
        assert r.original is not None
        assert r.original["start_line"] == 9
        assert r.original["content_hash"] == "murmur3:old"

    def test_range_contributor_override(self):
        r = AttributionRange(
            start_line=1,
            end_line=2,
            contributor={"type": "mixed", "model_id": "anthropic/claude-sonnet-4"},
        )
        assert r.contributor is not None
        assert r.contributor["type"] == "mixed"


class TestAttributionConversation041:
    def test_ids_map(self):
        c = AttributionConversation(
            contributor={"type": "ai"},
            ids={"anthropic": "msg_01xyz", "openai": ["resp_1", "resp_2"]},
        )
        assert c.ids is not None
        assert c.ids["anthropic"] == "msg_01xyz"
        assert c.ids["openai"] == ["resp_1", "resp_2"]

    def test_related_links(self):
        c = AttributionConversation(
            contributor={"type": "ai"},
            related=[
                {"type": "plan", "url": "opentraces://t/plan_3"},
                {"type": "issue", "url": "https://github.com/x/y/issues/42"},
            ],
        )
        assert c.related is not None
        assert len(c.related) == 2
        assert c.related[0]["type"] == "plan"


class TestAttribution041:
    def test_revision_pinning(self):
        a = Attribution(
            experimental=False,
            revision={"vcs_type": "git", "revision": "abc123"},
            files=[],
        )
        assert a.revision is not None
        assert a.revision["vcs_type"] == "git"

    def test_unaccounted_files(self):
        a = Attribution(
            experimental=True,
            files=[],
            unaccounted_files=["src/auth.py", "README.md"],
        )
        assert a.unaccounted_files == ["src/auth.py", "README.md"]

    def test_unaccounted_files_default_none(self):
        a = Attribution(files=[])
        assert a.unaccounted_files is None


class TestTask041:
    def test_repository_url(self):
        t = Task(repository_url="https://github.com/JayFarei/opentraces")
        assert t.repository_url == "https://github.com/JayFarei/opentraces"

    def test_repository_url_default_none(self):
        t = Task()
        assert t.repository_url is None


class TestTraceRecord041:
    def test_lifecycle_default(self):
        r = TraceRecord(
            trace_id="t", session_id="s", agent=Agent(name="claude-code")
        )
        assert r.lifecycle == "provisional"

    def test_lifecycle_final(self):
        r = TraceRecord(
            trace_id="t",
            session_id="s",
            agent=Agent(name="claude-code"),
            lifecycle="final",
        )
        assert r.lifecycle == "final"

    def test_git_links_default_empty(self):
        r = TraceRecord(
            trace_id="t", session_id="s", agent=Agent(name="claude-code")
        )
        assert r.git_links == []

    def test_git_links_populated(self):
        r = TraceRecord(
            trace_id="t",
            session_id="s",
            agent=Agent(name="claude-code"),
            git_links=[
                GitLink(vcs_type="git", revision="a", tier="tool_emitted"),
                GitLink(vcs_type="git", revision="b", tier="overlapping"),
            ],
        )
        assert len(r.git_links) == 2
        assert r.git_links[1].tier == "overlapping"


class TestBackwardCompatibility:
    def test_020_jsonl_loads_under_030(self):
        # A minimal 0.2.0-shaped record (no lifecycle, no git_links,
        # no range.original, etc.) must deserialize cleanly.
        legacy = {
            "schema_version": "0.2.0",
            "trace_id": "legacy-1",
            "session_id": "sess-legacy",
            "agent": {"name": "claude-code"},
            "task": {"description": "x"},
            "attribution": {
                "experimental": True,
                "files": [
                    {
                        "path": "a.py",
                        "conversations": [
                            {
                                "contributor": {"type": "ai"},
                                "url": "opentraces://trace/step_0",
                                "ranges": [
                                    {
                                        "start_line": 1,
                                        "end_line": 5,
                                        "content_hash": "abc123ef",
                                        "confidence": "high",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        }
        r = TraceRecord.model_validate_json(json.dumps(legacy))
        assert r.trace_id == "legacy-1"
        assert r.lifecycle == "provisional"  # default
        assert r.git_links == []
        assert r.attribution.unaccounted_files is None
        assert r.attribution.revision is None
        assert r.attribution.files[0].conversations[0].ids is None
        assert r.attribution.files[0].conversations[0].ranges[0].change_type == "addition"
        assert r.attribution.files[0].conversations[0].ranges[0].original is None
        assert r.task.repository_url is None

    def test_round_trip_preserves_041_fields(self):
        r = TraceRecord(
            trace_id="rt",
            session_id="s",
            agent=Agent(name="claude-code"),
            lifecycle="final",
            task=Task(repository_url="https://github.com/x/y"),
            git_links=[GitLink(vcs_type="git", revision="deadbeef", tier="tool_emitted")],
            attribution=Attribution(
                experimental=False,
                revision={"vcs_type": "git", "revision": "deadbeef"},
                unaccounted_files=["x.py"],
                files=[
                    AttributionFile(
                        path="a.py",
                        conversations=[
                            AttributionConversation(
                                contributor={"type": "ai", "model_id": "anthropic/claude-sonnet-4"},
                                url="opentraces://rt/step_0",
                                ids={"anthropic": "msg_01"},
                                related=[{"type": "plan", "url": "opentraces://rt/plan_1"}],
                                ranges=[
                                    AttributionRange(
                                        start_line=10,
                                        end_line=12,
                                        content_hash="murmur3:abc",
                                        confidence="high",
                                        change_type="modification",
                                        original={
                                            "start_line": 10,
                                            "end_line": 12,
                                            "content_hash": "murmur3:old",
                                        },
                                        contributor={"type": "mixed"},
                                    )
                                ],
                            )
                        ],
                    )
                ],
            ),
        )
        j = r.model_dump_json()
        back = TraceRecord.model_validate_json(j)
        assert back.lifecycle == "final"
        assert back.task.repository_url == "https://github.com/x/y"
        assert back.git_links[0].revision == "deadbeef"
        assert back.attribution.revision["revision"] == "deadbeef"
        assert back.attribution.unaccounted_files == ["x.py"]
        conv = back.attribution.files[0].conversations[0]
        assert conv.ids["anthropic"] == "msg_01"
        assert conv.related[0]["type"] == "plan"
        rng = conv.ranges[0]
        assert rng.change_type == "modification"
        assert rng.original["content_hash"] == "murmur3:old"
        assert rng.contributor["type"] == "mixed"
