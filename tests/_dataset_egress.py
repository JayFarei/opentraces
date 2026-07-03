"""Test helper: neutralize the #194 dataset-publish egress clearance gate.

Dataset publish (#194) authorizes every selected row's SOURCE trace through the
shared egress clearance predicate against a fresh push-time bucket manifest.
Pre-#194 publish tests build datasets from SYNTHETIC trace ids that have no
bucket entry, so the real manifest would withhold every row. Those tests assert
the publish PIPELINE (staging, retries, permission, schema-ahead, review), not
the clearance gate, so they seed a push-time manifest that clears exactly the
traces each local dataset references.

The gate itself is covered by ``tests/core/test_dataset_publish_clearance.py``.
"""

from __future__ import annotations


def neutralize_dataset_egress(monkeypatch) -> None:
    import opentraces.core.datasets as ds

    def _all_cleared() -> dict:
        traces: list[dict] = []
        seen: set[str] = set()
        for dataset in ds.list_datasets():
            for entry in ds.read_row_index(dataset.name):
                trace_id = ds._index_entry_source_trace(entry)
                if trace_id and trace_id not in seen:
                    seen.add(trace_id)
                    traces.append(
                        {
                            "trace_id": trace_id,
                            "status": {"known": True, "syncable": True},
                        }
                    )
        return {"digest": "sha256:tests-egress-cleared", "traces": traces}

    monkeypatch.setattr(ds, "push_clearance_manifest", _all_cleared)
