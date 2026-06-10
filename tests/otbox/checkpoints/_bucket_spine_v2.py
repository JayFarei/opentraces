"""``c-bucket-spine-v2-one-trace-provisional`` — issue #42 PILOT.

Plan 080 phases B/C/D deferred this checkpoint family; this is the
smallest member, built to validate the build recipe for the rest.

Composes onto ``c-captured-real-session`` (which, on current main,
already leaves a full bucket-spine-v2 world behind: content-addressed
blobs under ``bucket/blobs/v1/<project>/context/``, the events mirror
under ``bucket/events/v1/batches/`` + ``index.json``, and the per-trace
envelope under ``bucket/traces/v1/<project>/<trace>/``). The delta:

  1. Runs ``opentraces bucket manifest --json`` inside the project to
     materialize ``bucket/manifest.json`` (the manifest is a lazy
     projection — the capture chain does not write it).
  2. Verifies the manifest landed with ``schema_version ==
     "opentraces.bucket.manifest.v2"`` and at least one trace entry,
     raising ``CheckpointError`` otherwise (a lying checkpoint must not
     enter the cache).

NOTE on honesty: "provisional" in the plan-080 naming means the
captured patches have NOT matured into firm Git Anchors. The parent
checkpoint runs ``setup watcher tick``, so this pilot world is closer
to "anchored" than "provisional". The real implementation should
either build provisional state (skip the watcher tick via a dedicated
parent) or collapse the provisional/anchored variants — see issue #42.
"""

from __future__ import annotations

import json

from ..drivers.base import Driver
from ..env import Box
from . import Checkpoint, CheckpointError, register

_CHECKPOINT_NAME = "c-bucket-spine-v2-one-trace-provisional"


def _check(result, label: str) -> None:
    if not result.ok:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME} step {label!r} failed (rc={result.returncode}): "
            f"{(result.stderr or result.stdout).strip()[:300]}"
        )


def _bucket_spine_v2_delta(driver: Driver, box: Box) -> None:
    cli = driver.cli_argv(box)

    # 1. Materialize the manifest projection.
    res = driver.exec(box, [*cli, "--json", "bucket", "manifest"])
    _check(res, "bucket manifest --json")

    # 2. Verify the world is genuinely v2 before it can be cached.
    text = res.stdout
    marker = "---OPENTRACES_JSON---"
    if marker in text:
        text = text.split(marker, 1)[1]
    try:
        doc = json.loads(text.strip())
    except ValueError as exc:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: bucket manifest emitted non-JSON: {exc}"
        ) from None
    manifest = doc.get("manifest") or doc
    schema = manifest.get("schema_version")
    if schema != "opentraces.bucket.manifest.v2":
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: manifest schema_version is {schema!r}, "
            "expected 'opentraces.bucket.manifest.v2'"
        )
    traces = manifest.get("traces") or []
    if not traces:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME}: manifest has zero traces; the parent "
            "checkpoint did not leave a captured trace in the bucket"
        )

    # 3. Audit for journey templating (mirrors the parent's audit keys; the
    #    bucket family's TOMLs address the trace via {trace_id}).
    audit = dict(box.notes.get("c_captured_session_audit") or {})
    audit["manifest_schema_version"] = schema
    audit["bucket_trace_count"] = len(traces)
    box.notes["c_bucket_spine_v2_audit"] = audit
    box.save()


register(
    Checkpoint(
        name=_CHECKPOINT_NAME,
        composed_from="c-captured-real-session",
        delta=_bucket_spine_v2_delta,
        description=(
            "Issue #42 pilot: bucket-spine-v2 world with ONE captured trace, "
            "manifest.json materialized and verified v2."
        ),
        requires=("cli", "git"),
        provides={
            "captured_traces": 1,
            "survival_states": ["alive_on_path"],
            "branch_commits": 1,
            "bucket_spine_v2_layout": True,
            "context_tree_built": True,
        },
    )
)
