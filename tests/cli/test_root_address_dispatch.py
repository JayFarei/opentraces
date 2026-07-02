"""Integration tests for the v7 F5 address-first root dispatch.

``opentraces <ref>`` routes to ``opentraces trace get <ref>`` when the
first token is NOT a registered command and passes the conservative
address gate — and behaves byte-identically to before in every other
case. The load-bearing invariants:

* KNOWN COMMANDS ALWAYS WIN — ``get_command`` (hidden included) is
  consulted before any address logic, so no verb can be shadowed.
* Typos keep Click's exact "No such command" error, Did-you-mean intact.
* Tier-2 short prefixes dispatch only when they resolve to a real trace,
  and the RESOLVED full id is forwarded (trace get is exact-id only).
* Colon step refs dispatch to trace get and (with F1 integrated)
  resolve — ``:N`` / ``:last`` to a step card, ``:A-B`` to a span slice
  — never "No such command". (F1 owns the selector resolution; this
  file pins the DISPATCH.)
* Short-prefix honesty: ``trace get <short-prefix>`` itself still exits
  6 — the shortcut's tier-2 resolution is deliberate dispatcher-side
  sugar, not a trace-get behavior change.
"""

from __future__ import annotations

import json
from pathlib import Path

import click
from click.testing import CliRunner

from opentraces.cli import main
from opentraces_schema import Agent, Step, ToolCall, TraceRecord

UUID = "3f9a1c2e-5b6d-4e7f-8a9b-0c1d2e3f4a5b"
SIBLING_UUID = "3f9a1c2e-1111-4e7f-8a9b-0c1d2e3f4a5b"  # shares the 8-char prefix
PREFIX = UUID[:8]


def _enroll_project(project_dir: Path, project_id: str) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": project_id})
    )


def _write_project_trace(project_dir: Path, record: TraceRecord) -> None:
    from opentraces.core.config import get_project_traces_dir

    traces_dir = get_project_traces_dir(project_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{record.trace_id}.jsonl").write_text(record.model_dump_json() + "\n")


def _trace(trace_id: str) -> TraceRecord:
    steps = [
        Step(step_index=1, role="user", content="please fix the parser"),
        Step(
            step_index=2,
            role="agent",
            tool_calls=[
                ToolCall(
                    tool_call_id="tc-1",
                    tool_name="Edit",
                    input={"file_path": "src/parser.py", "old_string": "a", "new_string": "b"},
                )
            ],
        ),
        Step(step_index=3, role="agent", content="Done."),
    ]
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id[:8]}",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "F5 dispatch fixture"},
        steps=steps,
    )


def _seed(tmp_path: Path, *trace_ids: str) -> Path:
    """Enroll a project, write traces, rebuild the index. Returns project dir."""
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    for tid in trace_ids:
        _write_project_trace(project, _trace(tid))
    rebuild = CliRunner().invoke(main, ["trace", "index", "--json"])
    assert rebuild.exit_code == 0, rebuild.output
    return project


# ---------------------------------------------------------------------------
# known commands always win
# ---------------------------------------------------------------------------


def test_known_commands_win_even_if_gate_would_dispatch(monkeypatch):
    """get_command is consulted BEFORE any address logic.

    Force the gate to claim every token is an address; registered verbs
    (visible AND hidden) must still dispatch normally, proving the
    ordering is structural rather than an accident of the grammar.
    """
    import opentraces.cli._address as address_mod

    calls: list[str] = []

    def greedy_gate(token, **kwargs):
        calls.append(token)
        return token

    monkeypatch.setattr(address_mod, "root_dispatch_token", greedy_gate)

    runner = CliRunner()
    # Visible root command.
    res = runner.invoke(main, ["status", "--help"])
    assert res.exit_code == 0, res.output
    assert "status" in res.output
    # Hidden root command (hidden != removed; must not be shadowed).
    hidden = [n for n, c in main.commands.items() if getattr(c, "hidden", False)]
    assert hidden, "expected at least one hidden root command"
    res = runner.invoke(main, [hidden[0], "--help"])
    assert res.exit_code == 0, res.output
    # The gate never ran for registered names.
    assert calls == []


# ---------------------------------------------------------------------------
# address dispatch — byte-identical to trace get
# ---------------------------------------------------------------------------


def test_full_uuid_dispatches_byte_identical_to_trace_get(tmp_path):
    _seed(tmp_path, UUID)
    runner = CliRunner()
    via_shortcut = runner.invoke(main, [UUID, "--json"])
    assert via_shortcut.exit_code == 0, via_shortcut.output
    via_get = runner.invoke(main, ["trace", "get", UUID, "--json"])
    assert via_get.exit_code == 0, via_get.output
    assert via_shortcut.output == via_get.output
    payload = json.loads(via_shortcut.output)
    assert payload["schema_version"] == "opentraces.trace.get.v1"
    assert payload["trace"]["trace_id"] == UUID


def test_flags_forward_to_trace_get(tmp_path):
    _seed(tmp_path, UUID)
    runner = CliRunner()
    via_shortcut = runner.invoke(main, [UUID, "--bursts", "--json"])
    assert via_shortcut.exit_code == 0, via_shortcut.output
    via_get = runner.invoke(main, ["trace", "get", UUID, "--bursts", "--json"])
    assert via_get.exit_code == 0, via_get.output
    assert via_shortcut.output == via_get.output
    assert "bursts" in json.loads(via_shortcut.output)


def test_root_level_json_flag_reaches_dispatched_trace_get(tmp_path):
    """``opentraces --json <ref>`` — the D1 make_context hook injects
    --json into the dispatched trace get."""
    _seed(tmp_path, UUID)
    runner = CliRunner()
    res = runner.invoke(main, ["--json", UUID])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)  # pure JSON — D1 contract
    assert payload["schema_version"] == "opentraces.trace.get.v1"


def test_ot_scheme_dispatches_to_trace_get(tmp_path, monkeypatch):
    """ot:// refs dispatch to trace get's existing ot:// branch —
    byte-identical output, never "No such command"."""
    project = _seed(tmp_path, UUID)
    monkeypatch.chdir(project)
    ref = f"ot://trace/{UUID}/patches/p1/trail"
    runner = CliRunner()
    via_shortcut = runner.invoke(main, [ref, "--json"])
    via_get = runner.invoke(main, ["trace", "get", ref, "--json"])
    assert via_shortcut.exit_code == via_get.exit_code, via_shortcut.output
    assert via_shortcut.output == via_get.output
    assert "No such command" not in via_shortcut.output


# ---------------------------------------------------------------------------
# colon step refs — dispatch only (step resolution is F1's job)
# ---------------------------------------------------------------------------


def test_colon_step_ref_dispatches_and_resolves(tmp_path):
    """``<uuid>:<selector>`` reaches trace get AND resolves.

    This file pins the DISPATCH (never "No such command"); F1 owns the
    selector resolution. With F1 integrated the dispatched token
    resolves: ``:N`` / ``:last`` land on a step card and ``:A-B`` on a
    span slice — all rc=0 for a seeded trace, none a missing command.
    """
    _seed(tmp_path, UUID)
    runner = CliRunner()
    for selector in ("3", "last", "2-5"):
        res = runner.invoke(main, [f"{UUID}:{selector}"])
        assert res.exit_code == 0, (selector, res.output)
        assert "No such command" not in res.output


def test_colon_form_with_non_uuid_left_part_still_errors():
    res = CliRunner().invoke(main, ["foo:bar"])
    assert res.exit_code == 2
    assert "No such command 'foo:bar'" in res.output


# ---------------------------------------------------------------------------
# tier-2 short prefixes — semantic gate
# ---------------------------------------------------------------------------


def test_short_prefix_resolves_and_dispatches_full_id(tmp_path, monkeypatch):
    project = _seed(tmp_path, UUID)
    monkeypatch.chdir(project)
    runner = CliRunner()
    via_shortcut = runner.invoke(main, [PREFIX, "--json"])
    assert via_shortcut.exit_code == 0, via_shortcut.output
    payload = json.loads(via_shortcut.output)
    # The resolved FULL id was forwarded — output names the real trace.
    assert payload["trace"]["trace_id"] == UUID
    via_get = runner.invoke(main, ["trace", "get", UUID, "--json"])
    assert via_shortcut.output == via_get.output


def test_short_prefix_honesty_trace_get_itself_stays_exact_id(tmp_path, monkeypatch):
    """Documented residual asymmetry: the canonical verb does NOT get the
    prefix resolution — ``trace get <short-prefix>`` still exits 6 exactly
    like before F5. Deliberate: the tier-2 sugar lives in the dispatcher;
    widening trace get's own resolver is F1-follow-up territory."""
    project = _seed(tmp_path, UUID)
    monkeypatch.chdir(project)
    res = CliRunner().invoke(main, ["trace", "get", PREFIX, "--json"])
    assert res.exit_code == 6, res.output
    assert f"Trace not found: {PREFIX}" in res.output


def test_hex_word_without_matching_trace_keeps_no_such_command(tmp_path, monkeypatch):
    project = _seed(tmp_path, UUID)
    monkeypatch.chdir(project)
    res = CliRunner().invoke(main, ["deadbeef"])
    assert res.exit_code == 2, res.output
    assert "No such command 'deadbeef'" in res.output


def test_ambiguous_prefix_falls_through_to_no_such_command(tmp_path, monkeypatch):
    project = _seed(tmp_path, UUID, SIBLING_UUID)
    monkeypatch.chdir(project)
    res = CliRunner().invoke(main, [PREFIX])
    assert res.exit_code == 2, res.output
    assert f"No such command '{PREFIX}'" in res.output


# ---------------------------------------------------------------------------
# typo safety + framing flags
# ---------------------------------------------------------------------------


def test_typo_error_is_byte_identical_with_did_you_mean():
    res = CliRunner().invoke(main, ["stauts"])
    assert res.exit_code == 2
    assert "No such command 'stauts'" in res.output
    assert "Did you mean" in res.output
    assert "status" in res.output


def test_version_and_help_bypass_the_gate():
    runner = CliRunner()
    res = runner.invoke(main, ["--version"])
    assert res.exit_code == 0, res.output
    res = runner.invoke(main, ["--help"], color=False)
    assert res.exit_code == 0
    # The discoverability epilog line ships with the dispatch.
    assert "ot <trace-id>[:<step>]" in res.output
    assert "ot trace get <ref>" in res.output


def test_resilient_parsing_defers_to_super(monkeypatch):
    """Shell completion (resilient parsing) never runs the gate or the
    tier-2 read probe."""
    import opentraces.cli._address as address_mod

    def exploding_gate(token, **kwargs):  # pragma: no cover - guard
        raise AssertionError("gate must not run under resilient_parsing")

    monkeypatch.setattr(address_mod, "root_dispatch_token", exploding_gate)
    ctx = click.Context(main, info_name="opentraces", resilient_parsing=True)
    name, cmd, rest = main.resolve_command(ctx, [UUID])
    assert cmd is None  # Click's resilient-parsing contract, unchanged
