"""``dataset verify`` — reproduces / bucket-advanced / integrity-failure (#193).

RED-first coverage for the explainability enforcement: verify re-executes the
bound workflow against the bucket (with recorded answers for judgment
workflows) in a side-effect-free mode, canonicalizes the re-run rows, and
classifies the outcome into EXACTLY three verdicts by BYTE comparison against
the stored ``data/train.jsonl``:

* ``reproduces`` — the re-run rows are byte-identical to the stored rows;
* ``bucket-advanced`` — stored rows differ only because the bucket watermark
  moved past the recorded one; the delta is enumerated;
* ``integrity-failure`` — rows differ unexplainably OR a stored row was
  hand-mutated (payload_hash mismatch); non-zero exit.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from opentraces.core import paths
from opentraces.core.datasets import dataset_path
from opentraces.core.workflows import load_workflow, workflows_dir


# A deterministic builder that projects candidate rows from a controllable file
# under the bucket root (a trusted workflow is handed OT_OPENTRACES_DIR). A
# ``$OT_JUDGMENT_SIDECAR`` handshake is honored when the packet carries no
# answers so the recorded-answers verify path is exercised.
_BUILDER = r'''
import json, os, sys

otdir = os.environ.get("OT_OPENTRACES_DIR", "")
packet = json.load(open(os.environ["OT_RUN_PACKET"], encoding="utf-8"))
answers = packet.get("answers") or {}

need_judgment = os.path.join(otdir, "NEEDS_JUDGMENT") if otdir else ""
if need_judgment and os.path.exists(need_judgment) and not answers:
    sidecar = os.environ.get("OT_JUDGMENT_SIDECAR")
    with open(sidecar, "w", encoding="utf-8") as fh:
        json.dump([{"id": "keep?", "prompt": "Keep this row?"}], fh)
    sys.exit(10)

candidates = []
cand_path = os.path.join(otdir, "verify_candidates.jsonl")
if os.path.exists(cand_path):
    for line in open(cand_path, encoding="utf-8"):
        line = line.strip()
        if line:
            candidates.append(json.loads(line))

rows = []
for c in candidates:
    row = dict(c)
    if answers:
        row["summary"] = row["summary"] + " [judged]"
    rows.append(row)

with open(os.environ["OT_DATASET_OUTPUT"], "w", encoding="utf-8") as fh:
    for r in rows:
        fh.write(json.dumps(r, sort_keys=True) + "\n")
'''


_SKILL_MD = (
    "---\n"
    "name: {name}\n"
    "description: Deterministic verify builder\n"
    "mode: agent-skill\n"
    "---\n\n# {name}\n\nProject candidate rows.\n"
)


def _schema() -> dict:
    return {
        "type": "object",
        "required": ["source_trace_id", "source_unit_id", "summary"],
        "properties": {
            "source_trace_id": {"type": "string"},
            "source_unit_id": {"type": "string"},
            "summary": {"type": "string"},
        },
        "additionalProperties": False,
    }


def _install_workflow(name: str) -> str:
    pkg = workflows_dir() / name
    (pkg / "scripts").mkdir(parents=True, exist_ok=True)
    (pkg / "SKILL.md").write_text(_SKILL_MD.format(name=name), encoding="utf-8")
    (pkg / "scripts" / "build_rows.py").write_text(_BUILDER, encoding="utf-8")
    return load_workflow(name).digest


def _candidate(trace: str) -> dict:
    return {
        "source_trace_id": trace,
        "source_unit_id": f"tu:{trace}:trace",
        "summary": f"Work on {trace}.",
    }


def _write_candidates(candidates: list[dict]) -> None:
    with (paths.OPENTRACES_DIR / "verify_candidates.jsonl").open(
        "w", encoding="utf-8"
    ) as fh:
        for c in candidates:
            fh.write(json.dumps(c) + "\n")


def _create_and_run(name: str, candidates: list[dict], monkeypatch, *, watermark):
    from opentraces.core import workflow_runner
    from opentraces.core.datasets import create_dataset

    digest = _install_workflow(f"{name}-curator")
    create_dataset(
        name,
        workflow_skill=f"{name}-curator",
        workflow_digest=digest,
        row_schema=_schema(),
    )
    _write_candidates(candidates)
    monkeypatch.setattr(workflow_runner, "bucket_watermark", lambda: watermark)
    workflow_runner.run_dataset_workflow(
        name, executor="script", scope={"scope": "all-projects"}
    )


def _run_verify(name: str) -> tuple[int, dict]:
    from opentraces.cli.dataset import dataset_group

    result = CliRunner().invoke(dataset_group, ["verify", name, "--json"])
    payload = json.loads(result.output) if result.output.strip() else {}
    return result.exit_code, payload


def test_verify_untouched_dataset_reproduces(monkeypatch):
    from opentraces.core import dataset_verify

    wm = {"manifest_digest": "d0", "last_write_at": "2026-01-15T00:00:00Z"}
    _create_and_run(
        "verify-repro", [_candidate("trace-a"), _candidate("trace-b")], monkeypatch, watermark=wm
    )
    monkeypatch.setattr(dataset_verify, "bucket_watermark", lambda: wm)

    code, payload = _run_verify("verify-repro")
    assert code == 0, payload
    assert payload["status"] == "reproduces"
    assert payload["verdict"] == "reproduces"
    assert payload["schema_version"] == "opentraces.dataset.verify.v1"
    assert payload["byte_identical"] is True
    assert payload["stored_row_count"] == 2


def test_verify_reports_bucket_advanced_with_enumerated_delta(monkeypatch):
    from opentraces.core import dataset_verify

    w0 = {"manifest_digest": "d0", "last_write_at": "2026-01-15T00:00:00Z"}
    _create_and_run("verify-adv", [_candidate("trace-a")], monkeypatch, watermark=w0)
    # A new trace lands in the bucket AFTER the recorded watermark.
    _write_candidates([_candidate("trace-a"), _candidate("trace-b")])
    w1 = {"manifest_digest": "d1", "last_write_at": "2026-02-15T00:00:00Z"}
    monkeypatch.setattr(dataset_verify, "bucket_watermark", lambda: w1)

    code, payload = _run_verify("verify-adv")
    assert code == 0, payload
    assert payload["status"] == "bucket-advanced"
    assert payload["stored_row_count"] == 1
    assert payload["reproduced_row_count"] == 2
    delta_summaries = {json.loads(line)["source_trace_id"] for line in payload["delta"]}
    assert delta_summaries == {"trace-b"}


def test_verify_hand_mutated_stored_row_is_integrity_failure(monkeypatch):
    from opentraces.core import dataset_verify

    wm = {"manifest_digest": "d0", "last_write_at": "2026-01-15T00:00:00Z"}
    _create_and_run("verify-mutate", [_candidate("trace-a")], monkeypatch, watermark=wm)
    monkeypatch.setattr(dataset_verify, "bucket_watermark", lambda: wm)

    train = dataset_path("verify-mutate") / "data" / "train.jsonl"
    lines = train.read_text(encoding="utf-8").splitlines()
    mutated = json.loads(lines[0])
    mutated["summary"] = "HAND MUTATED"
    train.write_text(json.dumps(mutated, sort_keys=True) + "\n", encoding="utf-8")

    code, payload = _run_verify("verify-mutate")
    assert code != 0
    assert payload["status"] == "integrity-failure"
    assert payload["mutated_rows"], payload


def test_verify_recorded_answers_workflow_reproduces_deterministically(monkeypatch):
    from opentraces.core import dataset_verify, workflow_runner
    from opentraces.core.datasets import create_dataset

    digest = _install_workflow("verify-judge-curator")
    create_dataset(
        "verify-judge",
        workflow_skill="verify-judge-curator",
        workflow_digest=digest,
        row_schema=_schema(),
    )
    _write_candidates([_candidate("trace-a")])
    # Mark the builder as judgment-bearing: it exits rc=10 UNLESS answers are
    # supplied.
    (paths.OPENTRACES_DIR / "NEEDS_JUDGMENT").write_text("1", encoding="utf-8")
    wm = {"manifest_digest": "d0", "last_write_at": "2026-01-15T00:00:00Z"}
    monkeypatch.setattr(workflow_runner, "bucket_watermark", lambda: wm)

    answers = {"keep?": {"decision": "keep"}}
    workflow_runner.run_dataset_workflow(
        "verify-judge",
        executor="script",
        scope={"scope": "all-projects"},
        answers=answers,
    )
    monkeypatch.setattr(dataset_verify, "bucket_watermark", lambda: wm)

    # Verify must thread the recorded answers back so the judgment builder
    # re-runs deterministically (no rc=10) and reproduces byte-identically.
    code, payload = _run_verify("verify-judge")
    assert code == 0, payload
    assert payload["status"] == "reproduces"
    assert payload["byte_identical"] is True
