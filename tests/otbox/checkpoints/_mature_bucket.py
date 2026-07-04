"""``c-mature-bucket`` — the scale-world perf recurrence guard (issue #213, W5).

The largest committed CI checkpoint holds 1-2 captured traces; the real
maintainer bucket holds ~2,075 traces / 54 GB with outlier ``trail.jsonl.gz``
companions of 200-230 MB. Three orders of magnitude apart, so every
O(corpus) fallback and full-companion load this repo has shipped (#87, #121,
#137, #208) was invisible under test and found on the real mature bucket.

``c-mature-bucket`` closes that gap with a synthetic-but-honest mature world,
built ON the plan-080 bucket layout (never the legacy ``seed.py`` layer, which
does not exercise the bucket readers where this class lives). It composes onto
``c-bucket-spine-v2-multi-trace-mixed`` (2 real captured, anchored traces) and:

  * clones the real captured TraceRecord to ~600 plan-080 bucket traces —
    each a genuine object-store envelope (``objects/traces/v1/.../current.json``
    + content-addressed record) AND a per-trace envelope
    (``traces/v1/.../trace.json`` + companions), so a whole-corpus scan
    (``iter_trace_record_objects`` -> ``TraceRecord.model_validate`` +
    ``bucket_security_state`` per record, TWICE per ``source_provenance_for_query``)
    genuinely re-incurs the #208 wedge cost;
  * appends ~50K schema-valid ``TrailEvent``s to the canonical Git event log,
    mirrored into ``bucket/events/v1/batches/`` — the 50K-event scale the
    perf-budgets docstring calls for;
  * plants ONE outlier trace with a >=50 MB ``trail.jsonl.gz`` companion
    (the real bucket's 200-230 MB outlier shape in miniature);
  * leaves ~60% of the context companions EMPTY (mirrors the real bucket's
    ~1200/2000 empty ``context.jsonl.gz``);
  * binds a trusted, deterministic bucket-reading dataset workflow with
    DEFERRED source provenance, so the journey's FIRST ``dataset run`` is the
    step that triggers the (pre-fix) whole-corpus provenance snapshot.

World-honesty guard: ``provides`` declares the dimensions and ``verify_provides``
re-probes BOTH the >=600 trace count AND that the outlier companion is genuinely
>=50 MB. A world that lets pre-fix code pass the ``mature-bucket-perf`` journey
budget is DISHONEST (cloned envelopes short-circuiting the O(N) paths); the
red-proof against the pre-R1 lineage is the acceptance gate for this checkpoint.

Cold-build cost (measured on the maintainer laptop, darwin/py3.14):
    ~MEASURED_MINUTES min wall / ~MEASURED_GB GB peak. ``cache=True`` +
    content-addressed snapshot make it a one-time cost locally; CI cold-builds
    it in the nightly ``scale`` lane only (never per-PR).
"""

from __future__ import annotations

import json

from ..env import Box, resolve_cli_argv
from . import Checkpoint, CheckpointError, register
from ._captured_helpers import check as _check_helper

_FAMILY = "c-mature-bucket"

# Target corpus size. Base contributes 2; we clone to comfortably clear the
# 600-trace precondition floor (the journey pins min_captured_traces=600).
_N_CLONES = 600
_N_EVENTS = 50_000
# Outlier trail companion floor. verify_provides refuses to cache a world
# whose largest trail.jsonl.gz is below this — the honesty guard.
_OUTLIER_MIN_BYTES = 50 * 1024 * 1024
# We aim a touch above the floor so gzip nondeterminism can never dip under it.
_OUTLIER_TARGET_BYTES = 56 * 1024 * 1024
# Fraction of clones that get a NON-empty context companion (~40%, so ~60%
# stay empty — the real bucket's empty-context ratio).
_CONTEXT_NONEMPTY_EVERY = 5  # 1-in-5 non-empty is ~20%; see _CONTEXT_NONEMPTY_MOD


def _check(result, label: str) -> None:
    _check_helper(result, checkpoint=_FAMILY, label=label)


def _cli_json(driver, box: Box, *args: str, label: str) -> dict:
    cli = resolve_cli_argv()
    result = driver.exec(box, [*cli, *args])
    _check(result, label)
    text = result.stdout
    marker = "---OPENTRACES_JSON---"
    if marker in text:
        text = text.split(marker, 1)[1]
    try:
        payload = json.loads(text.strip())
    except (ValueError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"{_FAMILY} step {label!r} emitted malformed JSON: {exc}") from None
    return payload if isinstance(payload, dict) else {}


# ---------------------------------------------------------------------------
# The in-box bulk cloner (runs under the box .testvenv against HEAD product code)
# ---------------------------------------------------------------------------
_CLONER_SRC = r'''
import gzip
import json
import sys
import time
from pathlib import Path

from opentraces.core import paths as ot_paths
from opentraces.core.bucket_trace_records import (
    iter_trace_record_objects,
    write_trace_record,
)
from opentraces.core.bucket_envelope import _write_per_trace_envelope
from opentraces.core.bucket_layout import (
    trace_v1_context_path,
    trace_v1_trail_path,
)
from opentraces.core.trails.event_log import append_event_batch
from opentraces.core.trails.models import TrailEventDraft

params = json.loads(Path(sys.argv[1]).read_text())
n_clones = int(params["n_clones"])
n_events = int(params["n_events"])
outlier_target_bytes = int(params["outlier_target_bytes"])
context_nonempty_mod = int(params["context_nonempty_mod"])
project_dir = Path(params["project"])

# ---- 0. resolve the template trace + project slug ------------------------
base_objs = iter_trace_record_objects()
if not base_objs:
    raise SystemExit("mature-bucket cloner: no base TraceRecord objects to clone")
template = base_objs[0]
slug = template.project_slug
base_record = template.record

# ---- 1. ~50K schema-valid TrailEvents to the canonical Git log -----------
# Chunked so no single batch tree is pathologically large; the bucket events
# mirror is populated from these by the subsequent `bucket repair`.
CHUNK = 1000
emitted_events = 0
while emitted_events < n_events:
    this = min(CHUNK, n_events - emitted_events)
    drafts = [
        TrailEventDraft(
            event_type="otbox_scale_filler",
            payload={"note": "otbox mature-bucket scale filler", "n": emitted_events + i},
            capture_method=["transcript_reconstruction"],
        )
        for i in range(this)
    ]
    append_event_batch(project_dir, drafts, writer="otbox-mature-bucket-fixture")
    emitted_events += this

# ---- 2. clone the record to ~600 bucket traces ---------------------------
# Each clone lands BOTH in the object store (write_trace_record -> current.json,
# the whole-corpus scan surface) AND as a per-trace envelope with empty
# companions (_write_per_trace_envelope -> trace.json, the workflow read
# surface + the reconcile "already present" marker so `bucket repair` never
# re-projects it and never pays an O(events) per-trace walk).
clone_ids = []
context_nonempty = 0
for i in range(n_clones):
    tid = "mature-clone-%04d" % i
    rec = base_record.model_copy(deep=True)
    rec.trace_id = tid
    write_trace_record(rec, project_slug=slug, source_layer="otbox-mature-clone")
    _write_per_trace_envelope(slug, tid, rec, [], [])
    clone_ids.append(tid)
    # ~1-in-N clones get a small NON-EMPTY context companion; the rest keep
    # the empty companion _write_per_trace_envelope just wrote.
    if context_nonempty_mod > 0 and (i % context_nonempty_mod == 0):
        ctx_line = json.dumps(
            {"trace_id": tid, "event_type": "otbox_context_filler", "payload": {"n": i}},
            separators=(",", ":"),
            sort_keys=True,
        )
        body = (ctx_line + "\n").encode("utf-8")
        cp = trace_v1_context_path(slug, tid)
        with gzip.GzipFile(filename="", mode="wb", fileobj=open(cp, "wb"), mtime=0) as gz:
            gz.write(body)
        context_nonempty += 1

# ---- 3. one outlier trace with a >=50 MB trail.jsonl.gz companion --------
# Overwrite the outlier clone's (currently empty) trail companion with a large,
# poorly-compressible gzip stream of schema-shaped JSONL. Random hex per line
# keeps gzip near 1:1 so we hit the byte floor without a multi-GB file.
outlier_id = clone_ids[0]
outlier_path = trace_v1_trail_path(slug, outlier_id)
import os as _os

with open(outlier_path, "wb") as raw:
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        line_no = 0
        while raw.tell() < outlier_target_bytes:
            # ~64 KiB of incompressible hex per line, 8 lines per size check.
            for _ in range(8):
                blob = _os.urandom(65536).hex()
                rec_line = json.dumps(
                    {
                        "trace_id": outlier_id,
                        "event_type": "otbox_outlier_filler",
                        "payload": {"n": line_no, "blob": blob},
                    },
                    separators=(",", ":"),
                )
                gz.write((rec_line + "\n").encode("utf-8"))
                line_no += 1
            gz.flush()
outlier_bytes = outlier_path.stat().st_size

# ---- 4. register the clones in the project state.json --------------------
# `min_captured_traces` counts state.json trace entries (the cross-layout
# ground truth). These ARE captured traces of this world, so registering them
# there is correct, not cosmetic.
state_path = ot_paths.PROJECTS_DIR / slug / "state.json"
state = json.loads(state_path.read_text()) if state_path.exists() else {"traces": {}}
state.setdefault("traces", {})
now = time.time()
for tid in clone_ids:
    state["traces"].setdefault(
        tid,
        {"trace_id": tid, "session_id": "", "status": "committed", "created_at": now},
    )
state_path.parent.mkdir(parents=True, exist_ok=True)
state_path.write_text(json.dumps(state))

print(json.dumps({
    "slug": slug,
    "clones": len(clone_ids),
    "events_emitted": emitted_events,
    "outlier_trace_id": outlier_id,
    "outlier_bytes": outlier_bytes,
    "context_nonempty": context_nonempty,
    "state_trace_count": len(state["traces"]),
}))
'''


# ---------------------------------------------------------------------------
# The dataset workflow (trusted, deterministic, bucket-reading, DEFERRED prov)
# ---------------------------------------------------------------------------
_WORKFLOW_SKILL = """---
name: mature-bucket-curator
description: Project one deterministic row per bucket trace
mode: agent-skill
---

# mature-bucket-curator

Project one candidate row per trace in the private bucket.
"""

# Brace-free (dict() only) so nothing downstream re-templates it. Reads every
# bucket trace envelope and emits one deterministic row per trace_id.
_WORKFLOW_BUILD_ROWS = '''import glob
import json
import os

otdir = os.environ.get("OT_OPENTRACES_DIR") or os.path.expanduser("~/.opentraces")
troot = os.path.join(otdir, "bucket", "traces", "v1")
recs = []
for tj in glob.glob(os.path.join(troot, "*", "*", "trace.json")):
    try:
        rec = json.load(open(tj, encoding="utf-8"))
    except Exception:
        continue
    tid = rec.get("trace_id")
    if not tid:
        continue
    recs.append(tid)
recs.sort()
out = open(os.environ["OT_DATASET_OUTPUT"], "w", encoding="utf-8")
for tid in recs:
    row = dict(source_trace_id=tid, source_unit_id="tu:" + tid + ":trace", summary="row for " + tid)
    out.write(json.dumps(row, sort_keys=True) + "\\n")
out.close()
'''

_WORKFLOW_SCHEMA = (
    '{"type": "object", "required": ["source_trace_id", "source_unit_id", "summary"], '
    '"properties": {"source_trace_id": {"type": "string"}, '
    '"source_unit_id": {"type": "string"}, "summary": {"type": "string"}}, '
    '"additionalProperties": false}\n'
)


def _mature_delta(driver, box: Box) -> None:
    paths = driver.paths(box)
    project = paths["project"]
    home = paths["home"]
    opentraces_dir = paths["opentraces_dir"]
    testvenv_py = f"{project}/.testvenv/bin/python"

    # 1. Stage + run the bulk cloner under the box product interpreter.
    params = {
        "project": project,
        "n_clones": _N_CLONES,
        "n_events": _N_EVENTS,
        "outlier_target_bytes": _OUTLIER_TARGET_BYTES,
        "context_nonempty_mod": _CONTEXT_NONEMPTY_EVERY,
    }
    params_path = f"{home}/_mature_bucket_params.json"
    script_path = f"{home}/_mature_bucket_cloner.py"
    driver.put_text(box, params_path, json.dumps(params))
    driver.put_text(box, script_path, _CLONER_SRC)
    cloned = driver.exec(box, [testvenv_py, script_path, params_path], cwd=project)
    _check(cloned, "bulk cloner")
    try:
        summary = json.loads(cloned.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise CheckpointError(
            f"{_FAMILY}: cloner emitted no summary ({cloned.stdout[-400:]!r})"
        ) from None
    if int(summary.get("outlier_bytes") or 0) < _OUTLIER_MIN_BYTES:
        raise CheckpointError(
            f"{_FAMILY}: outlier trail companion is {summary.get('outlier_bytes')} bytes, "
            f"< the {_OUTLIER_MIN_BYTES} floor — dishonest world"
        )

    # 2. `bucket repair` mirrors the ~50K events into bucket/events/v1 and
    #    rebuilds manifest.json with all ~600 rows (the O(1) read-model HEAD's
    #    source_provenance_for_query reads). Envelopes already exist, so the
    #    reconcile loop is a no-op (never an O(events) per-trace walk).
    repair = _cli_json(driver, box, "bucket", "repair", "--json", label="bucket repair")
    if (repair.get("repair") or {}).get("errors"):
        raise CheckpointError(
            f"{_FAMILY}: bucket repair reported errors: {repair['repair']['errors']}"
        )

    # 3. Install a trusted bucket-reading workflow + bind a dataset to it with
    #    DEFERRED provenance (dataset new does NOT run it). The journey's first
    #    `dataset run` is the step that captures the bucket snapshot — the #208
    #    wedge surface on pre-fix code.
    wf_root = f"{opentraces_dir}/workflows/mature-bucket-curator"
    driver.put_text(box, f"{wf_root}/SKILL.md", _WORKFLOW_SKILL)
    driver.put_text(box, f"{wf_root}/scripts/build_rows.py", _WORKFLOW_BUILD_ROWS)
    driver.put_text(box, f"{project}/mature-schema.json", _WORKFLOW_SCHEMA)
    new = _cli_json(
        driver, box,
        "dataset", "new", "maturescale",
        "--workflow", "mature-bucket-curator",
        "--schema", f"{project}/mature-schema.json",
        "--json",
        label="dataset new",
    )
    if (new.get("dataset") or {}).get("manifest", {}).get("workflow", {}).get("skill") != "mature-bucket-curator":
        raise CheckpointError(
            f"{_FAMILY}: dataset new did not bind the mature-bucket-curator workflow "
            f"({json.dumps(new)[:300]})"
        )

    # 4. Record the audit the journey templates read (largest companion, outlier
    #    id, counts) and the runner-exposed session audit keys.
    audit = box.notes.setdefault("c_mature_bucket_audit", {})
    audit.update({
        "slug": summary.get("slug"),
        "clone_count": summary.get("clones"),
        "events_emitted": summary.get("events_emitted"),
        "outlier_trace_id": summary.get("outlier_trace_id"),
        "outlier_bytes": summary.get("outlier_bytes"),
        "context_nonempty": summary.get("context_nonempty"),
        "state_trace_count": summary.get("state_trace_count"),
        "dataset_name": "maturescale",
    })
    box.save()


register(
    Checkpoint(
        name="c-mature-bucket",
        composed_from="c-bucket-spine-v2-multi-trace-mixed",
        delta=_mature_delta,
        cache=True,
        description=(
            "c-bucket-spine-v2-multi-trace-mixed cloned to ~600 plan-080 bucket "
            "traces + ~50K TrailEvents + one >=50 MB outlier trail companion + "
            "~60% empty context companions + a trusted deferred-provenance dataset "
            "workflow. The scale-world perf recurrence guard (issue #213); "
            "verify_provides re-probes the >=600 count AND the outlier >=50 MB."
        ),
        provides={
            "captured_traces": _N_CLONES,
            "context_tree_built": True,
            "outlier_trail_companion_min_bytes": _OUTLIER_MIN_BYTES,
        },
    )
)
