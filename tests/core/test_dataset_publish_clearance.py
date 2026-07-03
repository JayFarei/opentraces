"""Dataset publish through the shared clearance predicate (#194).

RED-first coverage for Door B of the one egress predicate (ADR-0008 §3):

* a row sourced from an unscanned/withheld trace blocks the WHOLE publish with
  an enumerable ``{published, refused}`` partition and ZERO bytes egressed
  (mirrors bucket sync's Door-A ``BucketPushWithheldError``);
* cleared rows publish exactly as today (golden path unchanged);
* the refusal reads the SAME predicate bucket sync reads — proven by wiring the
  two doors against ONE manifest and asserting they agree (one seam).
"""

from __future__ import annotations

import pytest

from opentraces.core import datasets
from opentraces.core.datasets import (
    DatasetPublishWithheldError,
    add_dataset_remote,
    append_rows,
    create_dataset,
    dataset_egress_clearance,
    publish_dataset,
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


def _row(trace: str) -> dict:
    return {
        "source_trace_id": trace,
        "source_unit_id": f"tu:{trace}:trace",
        "summary": f"Work on {trace}.",
    }


def _manifest(
    *,
    cleared: set[str] = frozenset(),
    not_cleared: set[str] = frozenset(),
    unknown: set[str] = frozenset(),
) -> dict:
    traces: list[dict] = []
    for tid in sorted(cleared):
        traces.append({"trace_id": tid, "status": {"known": True, "syncable": True}})
    for tid in sorted(not_cleared):
        traces.append({"trace_id": tid, "status": {"known": True, "syncable": False}})
    for tid in sorted(unknown):
        traces.append({"trace_id": tid, "status": {"known": False}})
    return {"digest": "sha256:test-manifest", "traces": traces}


def _make_publishable_dataset(name: str, trace: str) -> None:
    create_dataset(
        name,
        workflow_skill="curator",
        workflow_digest="sha256:wf",
        row_schema=_schema(),
        publication_policy={"review": "auto"},
    )
    add_dataset_remote(name, f"me/{name}", visibility="private")
    append_rows(name, [_row(trace)], run_id="run-1", privacy_tier="medium")


def test_cleared_rows_publish_exactly_as_today(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    _make_publishable_dataset("pub-cleared", "trace-cleared")
    monkeypatch.setattr(
        datasets, "push_clearance_manifest", lambda: _manifest(cleared={"trace-cleared"})
    )

    summary = publish_dataset("pub-cleared", contributor="tester")
    assert summary.uploaded is True
    assert summary.new_row_count == 1
    remote_rows = "\n".join(
        p.read_text()
        for p in (tmp_path / "remotes" / "me" / "pub-cleared" / "data").glob("*.jsonl")
    )
    assert "trace-cleared" in remote_rows


def test_unscanned_source_trace_blocks_publish_with_zero_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    _make_publishable_dataset("pub-unscanned", "trace-unscanned")
    # The source trace is absent from the fresh push-time manifest -> unknown ->
    # withheld (conservative: absence of a recorded clearance is never "safe").
    monkeypatch.setattr(datasets, "push_clearance_manifest", lambda: _manifest())

    with pytest.raises(DatasetPublishWithheldError) as excinfo:
        publish_dataset("pub-unscanned", contributor="tester")

    partition = excinfo.value.partition
    assert partition["published"] == []
    assert len(partition["refused"]) == 1
    refused = partition["refused"][0]
    assert refused["trace_id"] == "trace-unscanned"
    assert refused["reason"] == "not_cleared_for_egress"
    assert refused["sub_reason"] == "status_unknown"
    assert excinfo.value.exit_code == 9
    # ZERO bytes egressed: no data shard was ever written to the remote.
    remote_data = tmp_path / "remotes" / "me" / "pub-unscanned" / "data"
    assert not remote_data.exists() or not list(remote_data.glob("*.jsonl"))


def test_withheld_syncable_false_trace_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    _make_publishable_dataset("pub-withheld", "trace-unfiltered")
    monkeypatch.setattr(
        datasets,
        "push_clearance_manifest",
        lambda: _manifest(not_cleared={"trace-unfiltered"}),
    )

    with pytest.raises(DatasetPublishWithheldError) as excinfo:
        publish_dataset("pub-withheld", contributor="tester")
    refused = excinfo.value.partition["refused"][0]
    assert refused["sub_reason"] == "syncable_false"


def test_refusal_reads_the_same_predicate_as_bucket_sync(monkeypatch):
    """One seam: dataset publish and bucket sync partition the SAME manifest
    identically (both read ``egress_clearance``)."""
    from opentraces.core.bucket_sync import partition_from_manifest

    _make_publishable_dataset_noremote("seam-ds", cleared="trace-ok", withheld="trace-bad")

    manifest = _manifest(cleared={"trace-ok"}, unknown={"trace-bad"})
    monkeypatch.setattr(datasets, "push_clearance_manifest", lambda: manifest)

    # Door A (bucket sync) partition of the manifest.
    door_a = partition_from_manifest(manifest)
    a_withheld = {w["trace_id"]: w["sub_reason"] for w in door_a["withheld"]}
    assert door_a["pushed"] == ["trace-ok"]
    assert a_withheld == {"trace-bad": "status_unknown"}

    # Door B (dataset publish) partition of the SAME manifest for this dataset's
    # rows: the withheld trace's sub_reason matches Door A byte-for-byte.
    from opentraces.core.datasets import read_row_index

    row_ids = [e.row_id for e in read_row_index("seam-ds")]
    door_b = dataset_egress_clearance("seam-ds", row_ids, manifest=manifest)
    b_withheld = {r["trace_id"]: r["sub_reason"] for r in door_b["refused"]}
    assert b_withheld == {"trace-bad": "status_unknown"}
    # The one cleared trace is published by Door B and pushed by Door A.
    assert set(door_b["published"]) and "trace-ok" in door_a["pushed"]


def _make_publishable_dataset_noremote(name: str, *, cleared: str, withheld: str) -> None:
    create_dataset(
        name,
        workflow_skill="curator",
        workflow_digest="sha256:wf",
        row_schema=_schema(),
        publication_policy={"review": "auto"},
    )
    append_rows(name, [_row(cleared)], run_id="run-1", privacy_tier="medium")
    append_rows(name, [_row(withheld)], run_id="run-2", privacy_tier="medium")


# --- Synthetic / source-less row exemption -------------------------------
#
# The ONE bundled template (skill-command-trajectory-eval-v1) mints synthetic
# ``raw:<row-key>`` source ids (build_rows.py: ``safe_trace_id = f"raw:{key}"``)
# that reference NO private-bucket trace. Before the exemption those rows made
# the template categorically UNPUBLISHABLE: each synthetic id resolved to
# ``status_unknown`` and aborted the WHOLE publish with exit 9, zero bytes. A
# row that references no bucket trace at all has no private bytes to withhold
# (it was already sanitized through the dataset row floor), so it is
# clearance-EXEMPT. A REAL trace id that is merely un-backfilled / unscanned
# (a valid bare trace address) must STILL refuse — that is the existing
# `trace-unscanned` coverage above and the second half of the contrast test.


def _synthetic_row(row_key: str) -> dict:
    """A row shaped like the bundled template's output: a synthetic
    ``raw:<row-key>`` source id that references no bucket trace."""
    return {
        "source_trace_id": f"raw:{row_key}",
        "source_unit_id": f"raw-unit:{row_key}",
        "summary": f"Synthetic trajectory row {row_key}.",
    }


def _make_publishable_synthetic_dataset(name: str, row_key: str) -> None:
    create_dataset(
        name,
        workflow_skill="curator",
        workflow_digest="sha256:wf",
        row_schema=_schema(),
        publication_policy={"review": "auto"},
    )
    add_dataset_remote(name, f"me/{name}", visibility="private")
    append_rows(name, [_synthetic_row(row_key)], run_id="run-1", privacy_tier="medium")


def test_synthetic_raw_source_id_rows_publish_exempt(tmp_path, monkeypatch):
    """The bundled template's synthetic ``raw:<row-key>`` rows publish out of the
    box: no private bucket trace stands behind them, so a fresh empty push-time
    manifest must NOT abort the whole publish."""
    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    _make_publishable_synthetic_dataset("pub-synthetic", "raw-row-000001")
    # Empty manifest: no trace resolves. A REAL trace id would be refused here;
    # the synthetic id is exempt because it names no bucket trace at all.
    monkeypatch.setattr(datasets, "push_clearance_manifest", lambda: _manifest())

    summary = publish_dataset("pub-synthetic", contributor="tester")
    assert summary.uploaded is True
    assert summary.new_row_count == 1
    remote_rows = "\n".join(
        p.read_text()
        for p in (tmp_path / "remotes" / "me" / "pub-synthetic" / "data").glob("*.jsonl")
    )
    assert "raw:raw-row-000001" in remote_rows


def test_egress_clearance_exempts_synthetic_but_refuses_real_uncleared(monkeypatch):
    """One predicate, two verdicts: a synthetic ``raw:`` row is EXEMPT while a
    REAL uncleared bucket-trace row in the SAME dataset is still REFUSED."""
    create_dataset(
        "mixed-ds",
        workflow_skill="curator",
        workflow_digest="sha256:wf",
        row_schema=_schema(),
        publication_policy={"review": "auto"},
    )
    append_rows(
        "mixed-ds", [_synthetic_row("raw-row-000001")], run_id="run-1", privacy_tier="medium"
    )
    append_rows(
        "mixed-ds", [_row("trace-real-uncleared")], run_id="run-2", privacy_tier="medium"
    )

    manifest = _manifest()  # empty: the real trace is unknown / unscanned.
    monkeypatch.setattr(datasets, "push_clearance_manifest", lambda: manifest)

    from opentraces.core.datasets import read_row_index

    row_ids = [e.row_id for e in read_row_index("mixed-ds")]
    partition = dataset_egress_clearance("mixed-ds", row_ids, manifest=manifest)

    # The synthetic row is published (exempt); the real uncleared row refused.
    refused_traces = {r["trace_id"] for r in partition["refused"]}
    assert refused_traces == {"trace-real-uncleared"}
    assert len(partition["published"]) == 1
    # The refused REAL trace carries the un-backfilled/unscanned sub_reason.
    assert partition["refused"][0]["sub_reason"] == "status_unknown"
