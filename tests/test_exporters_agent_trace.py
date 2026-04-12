"""Plan 041 R31: Agent Trace v0.1.0 export + import round-trip tests."""

from __future__ import annotations

import json

from opentraces.exporters.agent_trace import (
    export_to_jsonl, from_agent_trace, to_agent_trace,
)
from opentraces_schema import (
    Agent, Attribution, AttributionConversation, AttributionFile,
    AttributionRange, Task, TraceRecord,
)


def _trace(**overrides):
    base = TraceRecord(
        trace_id="t-1", session_id="s",
        agent=Agent(name="claude-code"),
        task=Task(repository_url="https://github.com/x/y"),
        attribution=Attribution(
            experimental=False,
            revision={"vcs_type": "git", "revision": "abc123"},
            files=[AttributionFile(
                path="src/app.py",
                conversations=[AttributionConversation(
                    contributor={"type": "ai", "model_id": "anthropic/claude-sonnet-4"},
                    url="opentraces://t-1/step_0",
                    ids={"anthropic": "msg_xy"},
                    related=[{"type": "plan", "url": "opentraces://t-1/plan_1"}],
                    ranges=[AttributionRange(
                        start_line=10, end_line=12,
                        content_hash="murmur3:abc",
                        confidence="high",
                        change_type="modification",
                        original={"start_line": 10, "end_line": 12, "content_hash": "murmur3:old"},
                        contributor={"type": "mixed"},
                    )],
                )],
            )],
            unaccounted_files=["src/sed_touched.py"],
        ),
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


class TestToAgentTrace:
    def test_schema_field_set(self):
        data = to_agent_trace(_trace())
        assert data["schema"] == "agent-trace/v0.1.0"
        assert data["trace_id"] == "t-1"

    def test_attribution_fields_round_trip(self):
        data = to_agent_trace(_trace())
        attr = data["attribution"]
        assert attr["revision"] == {"vcs_type": "git", "revision": "abc123"}
        assert attr["unaccounted_files"] == ["src/sed_touched.py"]
        f0 = attr["files"][0]
        assert f0["path"] == "src/app.py"
        conv = f0["conversations"][0]
        assert conv["contributor"]["model_id"] == "anthropic/claude-sonnet-4"
        assert conv["ids"] == {"anthropic": "msg_xy"}
        assert conv["related"][0]["type"] == "plan"
        rng = conv["ranges"][0]
        assert rng["content_hash"] == "murmur3:abc"
        assert rng["change_type"] == "modification"
        assert rng["original"]["content_hash"] == "murmur3:old"
        assert rng["contributor"]["type"] == "mixed"

    def test_no_attribution_yields_null(self):
        t = _trace()
        t.attribution = None
        data = to_agent_trace(t)
        assert data["attribution"] is None

    def test_repository_url_forwarded(self):
        data = to_agent_trace(_trace())
        assert data["repository_url"] == "https://github.com/x/y"


class TestImportAgentTrace:
    def test_round_trip_through_opentraces_schema(self):
        data = to_agent_trace(_trace())
        # Simulate ingest: parse agent-trace, then build opentraces record.
        parsed = from_agent_trace(data)
        assert parsed["trace_id"] == "t-1"
        # Shape is acceptable input to TraceRecord.model_validate with
        # minimal required fields filled in.
        shell = {
            "trace_id": parsed["trace_id"],
            "session_id": "imported",
            "agent": {"name": "imported"},
            "attribution": parsed["attribution"],
            "task": {"repository_url": parsed.get("repository_url")},
        }
        rec = TraceRecord.model_validate(shell)
        assert rec.attribution.revision == {"vcs_type": "git", "revision": "abc123"}
        r0 = rec.attribution.files[0].conversations[0].ranges[0]
        assert r0.content_hash == "murmur3:abc"
        assert r0.contributor["type"] == "mixed"


class TestExportToJsonl:
    def test_writes_one_line_per_trace(self, tmp_path):
        out = tmp_path / "out.jsonl"
        n = export_to_jsonl([_trace(), _trace()], out)
        assert n == 2
        lines = [json.loads(l) for l in out.read_text().splitlines()]
        assert len(lines) == 2
        assert all(l["schema"] == "agent-trace/v0.1.0" for l in lines)
