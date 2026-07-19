from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import opentraces.core.arena.run_store as run_store_module
import pytest
from click.testing import CliRunner
from opentraces.cli import main
from opentraces.core import bucket_store, ingest
from opentraces.core.arena.contract import build_result
from opentraces.core.arena.run_store import RunIntegrityError, RunStore
import opentraces.core.arena.trace_return as trace_return_module
from opentraces.core.arena.trace_return import TraceReturnError, return_run_as_trace
from opentraces.core.bucket_trace_records import read_bucket_record_for_trace
from opentraces.core.config import Config


RUN_ID = "run_20260714T120000000000Z_abcdef123456"
TRACE_ID = "d70e8530-430d-5260-9b59-3f61f57a2a13"


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "product"
    project.mkdir()
    (project / ".opentraces.json").write_text(
        json.dumps(
            {
                "marker_version": "2",
                "project_id": "1234567890abcdef1234567890abcdef",
            }
        ),
        encoding="utf-8",
    )
    return project


def _finalized_run(
    tmp_path: Path,
    monkeypatch,
    *,
    claim: str = "Publishing reaches the configured remote.",
    commit_shaped_action: bool = False,
    first_stdout: str = "published demo\n",
    omit_first_stdout: bool = False,
    pins: dict[str, object] | None = None,
) -> tuple[RunStore, Path]:
    monkeypatch.setattr(run_store_module, "_new_run_id", lambda: RUN_ID)
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    draft = store.begin()
    draft.write_text("source/scenario.py", "def test_publish(): pass\n")
    draft.write_json(
        "source/source.json",
        {
            "nodeid": "tests/scenarios/test_publish.py::test_publish",
            "claim": claim,
            "scenario_path": "tests/scenarios/test_publish.py",
            "repository": "JayFarei/opentraces",
            "commit": "2ab03ac637e",
            "dirty_diff_digest": None,
            "copied_source_path": "source/scenario.py",
        },
    )
    actions = [
        (
            ["opentraces", "dataset", "publish", "demo"],
            ".",
            first_stdout,
            "",
            0,
            17,
            "2026-07-14T12:00:01Z",
        ),
        (
            ["opentraces", "dataset", "status", "demo", "--json"],
            "/workspace",
            '{"published":true}\n',
            "warning retained\n",
            0,
            9,
            "2026-07-14T12:00:02Z",
        ),
    ]
    if commit_shaped_action:
        actions[0] = (
            ["git", "commit", "-m", "done"],
            ".",
            "[main abc1234] done\n",
            "",
            0,
            17,
            "2026-07-14T12:00:01Z",
        )
    for ordinal, (argv, cwd, stdout, stderr, returncode, duration_ms, started_at) in enumerate(
        actions, start=1
    ):
        action = f"actions/{ordinal:04d}"
        draft.write_json(
            f"{action}/invocation.json",
            {
                "ordinal": ordinal,
                "argv": argv,
                "env_pins": {"HF_TOKEN": "sha256:token-pin"},
                "cwd": cwd,
                "started_at": started_at,
            },
        )
        if not (omit_first_stdout and ordinal == 1):
            draft.write_text(f"{action}/stdout", stdout)
        draft.write_text(f"{action}/stderr", stderr)
        draft.write_json(f"{action}/timing.json", {"schemaVersion": 1})
        draft.write_json(
            f"{action}/result.json",
            {
                "returncode": returncode,
                "duration_ms": duration_ms,
                "stdout_ref": f"{action}/stdout",
                "stderr_ref": f"{action}/stderr",
                "timing_ref": f"{action}/timing.json",
            },
        )

    result = build_result(
        run_id=draft.run_id,
        claim=claim,
        nodeid="tests/scenarios/test_publish.py::test_publish",
        source_ref="source/scenario.py",
        execution_mode="direct",
        started_at="2026-07-14T12:00:00Z",
        duration_ms=3000,
        execution_status="complete",
        verdict="pass",
        reason=None,
        verifiers=[],
        evidence={"complete": True, "requirements": []},
        recordings={"rewatchable": False, "channels": []},
        artifacts=[],
        capture=None,
        pins=(
            pins
            if pins is not None
            else {
                "product": {
                    "commit": "2ab03ac637e",
                    "worktree": "clean",
                    "dirty_diff_digest": None,
                }
            }
        ),
    )
    return store, draft.finalize(result)


def test_verified_run_returns_as_one_deterministic_manufactured_trace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, run_path = _finalized_run(tmp_path, monkeypatch)
    project = _project(tmp_path)

    first = return_run_as_trace(
        run_path,
        project_dir=project,
        store=store,
        cfg=Config(),
    )
    first_bytes = first.model_dump_json()
    second = return_run_as_trace(
        run_path,
        project_dir=project,
        store=store,
        cfg=Config(),
    )

    assert first.trace_id == TRACE_ID
    assert second.trace_id == TRACE_ID
    assert second.model_dump_json() == first_bytes
    assert first.session_id == RUN_ID
    assert first.execution_context == "runtime"
    assert first.task.description == "Publishing reaches the configured remote."
    assert first.outcome.success is None
    assert first.context_tree_summary == {}
    assert first.patches == []
    assert [step.step_index for step in first.steps] == [1, 2, 3, 4]
    assert [step.role for step in first.steps] == ["user", "agent", "agent", "agent"]
    assert first.steps[0].content == "Publishing reaches the configured remote."
    assert [step.tool_calls[0].input["argv"] for step in first.steps[1:3]] == [
        ["opentraces", "dataset", "publish", "demo"],
        ["opentraces", "dataset", "status", "demo", "--json"],
    ]
    assert first.steps[1].tool_calls[0].duration_ms == 17
    assert first.steps[1].observations[0].content == "published demo\n"
    assert first.steps[2].observations[0].content == (
        '{"published":true}\n\n[stderr]\nwarning retained\n'
    )
    assert first.steps[3].content == (
        f"Bench run {RUN_ID} completed; its verdict remains in the stored run."
    )

    stored = read_bucket_record_for_trace(TRACE_ID)
    assert stored is not None
    assert stored.source_layer == "manufactured"
    assert stored.record.model_dump_json() == first_bytes


def test_commit_shaped_action_does_not_grade_the_manufactured_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, run_path = _finalized_run(
        tmp_path,
        monkeypatch,
        commit_shaped_action=True,
    )

    returned = return_run_as_trace(
        run_path,
        project_dir=_project(tmp_path),
        store=store,
        cfg=Config(),
    )

    assert returned.outcome.success is None
    assert returned.outcome.committed is False
    assert returned.outcome.commit_sha is None


@pytest.mark.parametrize(
    "pins",
    [
        {},
        {"product": {"commit": "2ab03ac637e", "worktree": "clean"}},
        {
            "product": {
                "commit": "2ab03ac637e",
                "worktree": "unknown",
                "dirty_diff_digest": None,
            }
        },
        {
            "product": {
                "commit": "2ab03ac637e",
                "worktree": "dirty",
                "dirty_diff_digest": None,
            }
        },
        {
            "product": {
                "commit": "2ab03ac637e",
                "worktree": "clean",
                "dirty_diff_digest": "sha256:" + "a" * 64,
            }
        },
        {
            "product": {
                "commit": "",
                "worktree": "dirty",
                "dirty_diff_digest": "sha256:not-a-digest",
            }
        },
    ],
)
def test_missing_partial_or_malformed_product_pin_is_refused(
    tmp_path: Path,
    monkeypatch,
    pins: dict[str, object],
) -> None:
    store, run_path = _finalized_run(tmp_path, monkeypatch, pins=pins)

    with pytest.raises(TraceReturnError, match="product pin"):
        return_run_as_trace(
            run_path,
            project_dir=_project(tmp_path),
            store=store,
            cfg=Config(),
        )

    assert read_bucket_record_for_trace(TRACE_ID) is None


def test_security_pipeline_preserves_canonical_claim_but_sanitizes_other_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    claim_token = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"
    output_token = "ghp_zyxwvutsrqponmlkjihgfedcba0987654321"
    claim = f"The scanner recognizes {claim_token}."
    store, run_path = _finalized_run(
        tmp_path,
        monkeypatch,
        claim=claim,
        first_stdout=f"observed {output_token}\n",
    )
    cfg = Config()
    cfg.security.regex.enabled = True

    returned = return_run_as_trace(
        run_path,
        project_dir=_project(tmp_path),
        store=store,
        cfg=cfg,
    )

    assert returned.task.description == claim
    assert returned.steps[0].content == claim
    assert output_token not in (returned.steps[1].observations[0].content or "")
    assert "[REDACTED]" in (returned.steps[1].observations[0].content or "")
    assert "regex" in returned.metadata["security"]["tools_applied"]


def test_tampered_run_is_refused_before_any_trace_is_written(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, run_path = _finalized_run(tmp_path, monkeypatch)
    stdout = run_path / "actions" / "0001" / "stdout"
    stdout.chmod(0o600)
    stdout.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(RunIntegrityError, match="actions/0001/stdout"):
        return_run_as_trace(
            run_path,
            project_dir=_project(tmp_path),
            store=store,
            cfg=Config(),
        )

    assert read_bucket_record_for_trace(TRACE_ID) is None


def test_missing_declared_action_output_is_refused_before_trace_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, run_path = _finalized_run(
        tmp_path,
        monkeypatch,
        omit_first_stdout=True,
    )
    assert store.verify(run_path) is True

    with pytest.raises(TraceReturnError, match="missing action output.*stdout"):
        return_run_as_trace(
            run_path,
            project_dir=_project(tmp_path),
            store=store,
            cfg=Config(),
        )

    assert read_bucket_record_for_trace(TRACE_ID) is None


def test_returned_run_resolves_through_standard_trace_read_verbs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, run_path = _finalized_run(tmp_path, monkeypatch)
    return_run_as_trace(
        run_path,
        project_dir=_project(tmp_path),
        store=store,
        cfg=Config(),
    )

    runner = CliRunner()
    got = runner.invoke(main, ["trace", "get", TRACE_ID, "--json"])
    mapped = runner.invoke(main, ["trace", "map", TRACE_ID, "--json"])
    sliced = runner.invoke(
        main,
        [
            "trace",
            "slice",
            TRACE_ID,
            "--from-step",
            "1",
            "--to-step",
            "3",
            "--json",
        ],
    )
    queried = runner.invoke(
        main,
        [
            "trace",
            "query",
            "--lex",
            "configured remote",
            "--unknown-success",
            "--json",
        ],
    )

    assert got.exit_code == 0, got.output
    assert mapped.exit_code == 0, mapped.output
    assert sliced.exit_code == 0, sliced.output
    assert queried.exit_code == 0, queried.output
    assert json.loads(got.output)["trace"]["trace_id"] == TRACE_ID
    mapped_payload = json.loads(mapped.output)
    assert mapped_payload["trace_id"] == TRACE_ID
    assert json.loads(sliced.output)["slices"][0]["trace_id"] == TRACE_ID
    assert TRACE_ID in {
        candidate["trace_id"] for candidate in json.loads(queried.output)["candidates"]
    }


def test_existing_bucket_writer_callers_keep_the_canonical_source_layer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        ingest,
        "get_project_dir",
        lambda _project: SimpleNamespace(name="existing-project"),
    )
    monkeypatch.setattr(
        bucket_store,
        "write_trace_record",
        lambda _record, **kwargs: seen.append(kwargs["source_layer"]),
    )
    monkeypatch.setattr(bucket_store, "write_raw_source_artifact", lambda *_a, **_k: None)

    ingest.write_trace_to_bucket(
        SimpleNamespace(trace_id="existing-canonical-trace"),
        tmp_path,
        parser_name="claude-code",
        source_jsonl=tmp_path / "session.jsonl",
        trace_record_only=True,
    )

    assert seen == ["canonical"]


class _TrackingTextHandle:
    """Wraps a text handle and records the size of every ``read`` call."""

    def __init__(self, handle, log: list[tuple[int | None, int]]):
        self._handle = handle
        self._log = log

    def read(self, size: int | None = -1) -> str:
        data = self._handle.read(size)
        self._log.append((size, len(data)))
        return data

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._handle, name)


def test_large_action_output_is_previewed_without_reading_the_whole_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # RED control for #320: a valid but oversized stored stdout must not be read
    # in full by the trace-return reader, even though the returned trace stores
    # only a bounded preview. RunStore.verify still hashes the whole file in
    # BINARY mode; only the text-mode trace-return read is bounded here.
    marker = "\n[output truncated; full bytes remain in the stored run]\n"
    big_stdout = "a" * 200_000
    store, run_path = _finalized_run(tmp_path, monkeypatch, first_stdout=big_stdout)
    project = _project(tmp_path)

    target = (run_path / "actions" / "0001" / "stdout").resolve()
    assert target.stat().st_size > trace_return_module._OUTPUT_LIMIT_CHARS
    text_reads: list[tuple[int | None, int]] = []
    real_open = Path.open

    def _tracking_open(self, mode="r", *args, **kwargs):
        handle = real_open(self, mode, *args, **kwargs)
        if self.resolve() == target and "b" not in mode:
            return _TrackingTextHandle(handle, text_reads)
        return handle

    monkeypatch.setattr(Path, "open", _tracking_open)

    returned = return_run_as_trace(
        run_path,
        project_dir=project,
        store=store,
        cfg=Config(),
    )

    content = returned.steps[1].observations[0].content
    assert content is not None
    # The visible preview is unchanged: bounded to the limit and terminated by
    # the canonical truncation marker with the leading bytes preserved.
    assert len(content) == trace_return_module._OUTPUT_LIMIT_CHARS
    assert content.endswith(marker)
    assert content.startswith("a" * 100)
    # The trace-return reader touched the file in text mode ...
    assert text_reads, "the oversized stdout was never read in text mode"
    # ... but never issued an unbounded read and never consumed the whole file.
    assert all(size not in (-1, None) for size, _ in text_reads)
    assert sum(returned_len for _, returned_len in text_reads) <= (
        trace_return_module._OUTPUT_LIMIT_CHARS + 1
    )


_LIMIT = trace_return_module._OUTPUT_LIMIT_CHARS


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        # Exact preview-bound boundary: 4096 chars fit untruncated ...
        ("exactly_at_limit_ascii", b"a" * _LIMIT),
        # ... and 4097 chars is the first truncating length.
        ("one_char_past_limit_ascii", b"a" * (_LIMIT + 1)),
        ("far_past_limit_ascii", b"a" * 200_000),
        # A 4-byte character sits exactly astride the preview boundary: it is
        # the (limit+1)-th character the bounded reader fetches to detect
        # overflow, so its bytes straddle the read window.
        (
            "multibyte_char_straddles_preview_boundary",
            ("a" * _LIMIT + "\U0001f389" + "b" * 100).encode("utf-8"),
        ),
        # Multibyte characters throughout: internal buffer chunks split
        # 2-byte sequences at arbitrary byte offsets.
        ("multibyte_throughout", ("é" * (_LIMIT + 500)).encode("utf-8")),
        # Malformed bytes early in an oversized file: errors="replace" parity
        # with the old whole-file decode.
        ("malformed_bytes_early", b"ok \xff\xfe bytes" + b"x" * (_LIMIT * 2)),
        # A truncated multibyte sequence right at the preview boundary.
        ("malformed_truncated_sequence_at_boundary", b"a" * _LIMIT + b"\xf0\x9f" + b"c" * 500),
        # A truncated multibyte sequence at EOF in an under-limit file: the
        # incremental decoder's final flush must match the whole-read decode.
        ("malformed_truncated_sequence_at_eof", b"tail\xf0\x9f"),
        # Universal-newline translation parity: \r\n and lone \r both decode
        # to \n exactly as Path.read_text did, before the char count.
        ("crlf_newlines_past_limit", b"line\r\n" * 1200),
        ("bare_cr_newlines_past_limit", b"line\r" * 1200),
        ("empty_file", b""),
    ],
)
def test_bounded_preview_is_byte_identical_to_whole_read_then_slice(
    tmp_path: Path,
    name: str,
    payload: bytes,
) -> None:
    # Review-strengthening for #320 (PR #352): the bounded reader must produce
    # BYTE-IDENTICAL previews to the old implementation (whole-file
    # Path.read_text(errors="replace") then slice), across the exact preview
    # boundary, multibyte straddles, malformed bytes, and newline translation.
    path = tmp_path / name
    path.write_bytes(payload)

    # The old implementation, verbatim: whole-file decode, then truncate.
    whole = path.read_text(encoding="utf-8", errors="replace")
    if len(whole) <= _LIMIT:
        expected, expected_remaining = whole, _LIMIT - len(whole)
    else:
        marker = "\n[output truncated; full bytes remain in the stored run]\n"
        keep = max(0, _LIMIT - len(marker))
        expected = whole[:keep] + marker[: _LIMIT - keep]
        expected_remaining = 0

    got, got_remaining = trace_return_module._bounded_text(path, _LIMIT)

    assert got == expected
    assert got_remaining == expected_remaining
    assert len(got) <= _LIMIT
