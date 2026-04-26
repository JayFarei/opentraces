"""``opentraces trail attach`` CLI surface."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.trails import (
    GitObjectID,
    TrailEventDraft,
    append_event_batch,
    read_events,
)


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _hash_object(repo: Path, content: str) -> str:
    proc = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=content,
        text=True,
        capture_output=True,
        check=True,
    )
    return proc.stdout.strip()


def _setup_trace_with_unanchored_patch(
    repo: Path, trace_id: str, before_text: str, after_text: str
) -> str:
    target = repo / "auth.py"
    target.write_text(before_text)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed auth"], cwd=repo, check=True)
    before_blob = GitObjectID(hex=_hash_object(repo, before_text))
    after_blob = GitObjectID(hex=_hash_object(repo, after_text))

    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=trace_id,
                generation_index=0,
                step_index=1,
                capture_method=["hook_pretooluse", "hook_posttooluse"],
                payload={
                    "trace_patch_id": f"tracepatch-sha256:{trace_id}-step1",
                    "snapshot_before_id": "snapshot-pre",
                    "snapshot_after_id": "snapshot-post",
                    "file_path": "auth.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": "    return True\n",
                    "raw_authored_hash": "sha256:fixture",
                    "git_clean_hash": "sha256:fixture",
                    "before_blob_id": before_blob.model_dump(mode="json"),
                    "after_blob_id": after_blob.model_dump(mode="json"),
                    "limitations": [],
                },
            )
        ],
        writer="capture-claude-code",
    )

    target.write_text(after_text)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "apply true"], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


def test_trail_attach_cli_emits_manual_attach_anchor(tmp_path: Path) -> None:
    """``ot trail attach`` produces the same anchor evidence the post-commit
    correlator would have, tagged with ``manual_attach`` so consumers can
    distinguish manual from automatic capture."""
    _init_repo(tmp_path)
    commit_sha = _setup_trace_with_unanchored_patch(
        tmp_path,
        trace_id="tr1",
        before_text="def authorize():\n    return False\n",
        after_text="def authorize():\n    return True\n",
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "trail",
            "attach",
            "--trace",
            "tr1",
            "--commit",
            commit_sha,
            "--project",
            str(tmp_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["trace_id"] == "tr1"
    assert payload["commit_ref"] == commit_sha
    assert len(payload["created_anchors"]) == 1
    anchor = payload["created_anchors"][0]
    assert anchor["evidence_tier"] == "exact_range_hash"
    assert anchor["source"] == "manual-attach"

    events = read_events(tmp_path)
    new_anchor_events = [
        e for e in events
        if e.event_type == "git_anchor_created"
        and e.payload.get("trace_patch_id") == anchor["trace_patch_id"]
    ]
    assert len(new_anchor_events) == 1
    assert new_anchor_events[0].capture_method == ["manual_attach"]


def test_trail_attach_cli_reports_no_new_anchors_when_nothing_to_do(
    tmp_path: Path,
) -> None:
    """Re-invoking attach against an already-attached commit succeeds with
    zero new anchors and an explanatory message."""
    _init_repo(tmp_path)
    commit_sha = _setup_trace_with_unanchored_patch(
        tmp_path,
        trace_id="tr1",
        before_text="x\n",
        after_text="y\n",
    )
    runner = CliRunner()
    runner.invoke(
        main,
        ["trail", "attach", "--trace", "tr1", "--commit", commit_sha,
         "--project", str(tmp_path)],
    )
    result = runner.invoke(
        main,
        ["trail", "attach", "--trace", "tr1", "--commit", commit_sha,
         "--project", str(tmp_path), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["created_anchors"] == []
