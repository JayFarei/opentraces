"""``c-mature-bucket`` — the scale-world perf recurrence guard (issue #213, W5).

The largest committed CI checkpoint holds 1-2 captured traces; the real
maintainer bucket holds ~2,075 traces / 54 GB with outlier ``trail.jsonl.gz``
companions of 200-230 MB. Three orders of magnitude apart, so every
O(corpus) fallback and full-companion load this repo has shipped (#87, #121,
#137, #208) was invisible under test and found on the real mature bucket.

``c-mature-bucket`` closes that gap with a synthetic-but-honest mature world,
built ON the plan-080 bucket layout (never the legacy ``seed.py`` layer, which
does not exercise the bucket readers where this class lives). It composes onto
``c-bucket-spine-v2-one-trace-provisional`` (1 real captured trace on the
plan-080 bucket layout) and:

NOTE on the compose base (issue #213 lane, escalated): the issue names
``c-bucket-spine-v2-multi-trace-mixed`` as the base. That base (and its
``-one-trace-anchored`` ancestor) does NOT build on feat/seal-family HEAD in
this environment: ``_anchor_first_trace`` asserts the manifest row's
``summary.anchored_count >= 1``, but although the post-commit correlation
succeeds (``notes_written: true``, tier ``tool_emitted``) AND a real
``git_anchor_created`` event lands in the canonical event log, ``bucket repair``
projects ``anchored_count: 0`` into the manifest — a pre-existing anchor→manifest
projection discrepancy unrelated to this lane. The perf recurrence guard is
ANCHOR-INDEPENDENT (all four steps — dataset run, capsule seal, bucket status,
trail track — read the bucket corpus / event log, never a git anchor), and the
whole-corpus wedge this checkpoint reproduces comes entirely from the 600 cloned
object-store envelopes + 50K events + outlier companion added below. So we
compose on the last buildable ancestor (``-one-trace-provisional``); the anchor
dimension is escalated as a base-checkpoint blocker, not worked around in the
budgets.

Onto that provisional base this delta:

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
re-probes ALL of: the >=600 trace count, the outlier companion genuinely >=50 MB,
the REAL plan-080 v2 object-store + per-trace envelope counts on disk
(``bucket_trace_envelopes_min`` — never the builder's own state.json bookkeeping;
external review CRITICAL 2), and the ~50K-event scale via the events-mirror head
sequence (``trail_events_min`` — external review RECOMMENDATION B). A world that
lets pre-fix code pass the ``mature-bucket-perf`` journey budget is DISHONEST
(cloned envelopes short-circuiting the O(N) paths); the red-proof against the
pre-R1 lineage is the acceptance gate for this checkpoint.

Red-proof ARMING (external review CRITICAL 1): the ``maturescale`` dataset is
created WITH a candidate query (``--query-name maturescale-query``). Without it,
``dataset new`` binds ``candidate_query=None`` and ``dataset run``'s
``_ensure_run_source_provenance`` returns early — the journey would never touch
``source_provenance_for_query``, the exact whole-corpus scan the #208 wedge lived
in, and a pre-R1 run would stay green on a disarmed world. With the query marker,
deferred provenance is written at create time and the journey's FIRST
``dataset run`` refreshes the real bucket snapshot through the wedge surface.

Cold-build cost (measured on the maintainer laptop, darwin, python3.14 box
interpreter, full ancestor chain + green journey run): ~13.4 min wall (~804 s,
dominated by the O(600 traces + 50K events) ``bucket repair``) / ~105 MB
compressed snapshot on disk. ``cache=True`` + content-addressed snapshot make it
a one-time cost locally; CI cold-builds it in the nightly ``scale`` lane only
(never per-PR).

Red/green proof of the honest ARMED world (issue #213 loopcraft condition,
re-run 2026-07-04 after the external-review fixes invalidated the original red
evidence, on the SAME built snapshot): the ``dataset run`` step is 0.61 s on
this branch (bounded ``read_persisted_manifest_capped`` reader) and 50.3 s on
the pre-R1 lineage (parent of ea7506d913f, the whole-corpus
``source_provenance_for_query`` scan) — a ~83x gap that trips the 15 s ceiling
on pre-fix code and clears it on HEAD. The world is honest: cloned envelopes do
NOT short-circuit the O(N) path. The built world measured 241 non-empty / 360
empty context companions (~60% empty, the real bucket's ratio) with events head
sequence 50019.
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
# Context-companion empty ratio (issue #213 external review, RECOMMENDATION A):
# the real mature bucket is ~60% empty context companions (~1200/2000). We write
# a NON-empty companion for _CONTEXT_NONEMPTY_NUM out of every _CONTEXT_NONEMPTY_DEN
# clones -> 2/5 = 40% non-empty / 60% empty, matching the spec. (The prior
# 1-in-5 modulo yielded 20% non-empty / 80% empty — dishonestly emptier than the
# real world it mirrors.)
_CONTEXT_NONEMPTY_NUM = 2
_CONTEXT_NONEMPTY_DEN = 5


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
from opentraces.core.trails.event_log import append_event_batch
from opentraces.core.trails.models import TrailEventDraft

params = json.loads(Path(sys.argv[1]).read_text())
n_clones = int(params["n_clones"])
n_events = int(params["n_events"])
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
# the whole-corpus scan surface that pre-fix source_provenance_for_query pays
# per record, TWICE) AND as a per-trace envelope (_write_per_trace_envelope ->
# trace.json, the workflow read surface). Companions are written EMPTY here;
# the outlier trail + the ~40% non-empty context companions are (re)written
# AFTER `bucket repair` by the companion writer, because repair re-projects
# every per-trace companion from the event log and the clones carry no
# per-trace events — so a pre-repair large companion would be silently wiped.
clone_ids = []
for i in range(n_clones):
    tid = "mature-clone-%04d" % i
    rec = base_record.model_copy(deep=True)
    rec.trace_id = tid
    write_trace_record(rec, project_slug=slug, source_layer="otbox-mature-clone")
    _write_per_trace_envelope(slug, tid, rec, [], [])
    clone_ids.append(tid)

# The FIRST clone is the outlier target (its >=50 MB trail companion is planted
# post-repair). Naming it here keeps the id stable for the companion writer.
outlier_id = clone_ids[0]

# ---- 3. register the clones in the project state.json --------------------
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
    "state_trace_count": len(state["traces"]),
}))
'''


# ---------------------------------------------------------------------------
# The post-repair companion writer (runs AFTER `bucket repair`, which
# re-projects — and so empties — every per-trace companion). Plants the one
# >=50 MB outlier trail companion + the ~40%-non-empty context companions the
# real mature bucket exhibits. Nothing downstream re-projects these companions
# after this point, so they persist into the cached snapshot.
# ---------------------------------------------------------------------------
_COMPANION_SRC = r'''
import gzip
import json
import os
import sys
from pathlib import Path

from opentraces.core.bucket_layout import (
    trace_v1_context_path,
    trace_v1_trail_path,
)

params = json.loads(Path(sys.argv[1]).read_text())
slug = params["slug"]
outlier_id = params["outlier_id"]
outlier_target_bytes = int(params["outlier_target_bytes"])
context_nonempty_num = int(params["context_nonempty_num"])
context_nonempty_den = int(params["context_nonempty_den"])
n_clones = int(params["n_clones"])

# ---- 1. the >=50 MB outlier trail companion ------------------------------
# A poorly-compressible gzip stream of schema-shaped JSONL. Random hex per line
# keeps gzip near 1:1 so we clear the byte floor without a multi-GB file.
outlier_path = trace_v1_trail_path(slug, outlier_id)
with open(outlier_path, "wb") as raw:
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        line_no = 0
        while raw.tell() < outlier_target_bytes:
            for _ in range(8):  # ~64 KiB incompressible hex per line
                blob = os.urandom(65536).hex()
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

# ---- 2. ~40% non-empty context companions (~60% stay empty) --------------
# Non-empty for NUM of every DEN clones (2/5 -> 40% non-empty, 60% empty).
context_nonempty = 0
if context_nonempty_num > 0 and context_nonempty_den > 0:
    for i in range(n_clones):
        if (i % context_nonempty_den) >= context_nonempty_num:
            continue
        tid = "mature-clone-%04d" % i
        ctx_line = json.dumps(
            {"trace_id": tid, "event_type": "otbox_context_filler", "payload": {"n": i}},
            separators=(",", ":"),
            sort_keys=True,
        )
        body = (ctx_line + "\n").encode("utf-8")
        cp = trace_v1_context_path(slug, tid)
        with open(cp, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
                gz.write(body)
        context_nonempty += 1

print(json.dumps({
    "outlier_bytes": outlier_bytes,
    "context_nonempty": context_nonempty,
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
    outlier_id = summary.get("outlier_trace_id")
    if not outlier_id:
        raise CheckpointError(f"{_FAMILY}: cloner returned no outlier_trace_id")

    # 2. `bucket repair` mirrors the ~50K events into bucket/events/v1 and
    #    rebuilds manifest.json with all ~600 object-store rows (what the O(1)
    #    read-model HEAD's source_provenance_for_query and `bucket status`
    #    read). NOTE: repair RE-PROJECTS every per-trace companion from the
    #    event log, so the clones' trail/context companions are emptied here —
    #    the outlier + non-empty context companions are (re)written AFTER this
    #    by the companion writer (step 3), never before.
    repair = _cli_json(driver, box, "bucket", "repair", "--json", label="bucket repair")
    if (repair.get("repair") or {}).get("errors"):
        raise CheckpointError(
            f"{_FAMILY}: bucket repair reported errors: {repair['repair']['errors']}"
        )

    # 3. Post-repair companion writer: plant the >=50 MB outlier trail companion
    #    and the ~40%-non-empty context companions. Repair does not run again in
    #    this delta, so these persist into the cached snapshot. The outlier-bytes
    #    honesty check lives HERE (not on the cloner's pre-repair write, which
    #    repair would wipe).
    comp_params = {
        "slug": summary.get("slug"),
        "outlier_id": outlier_id,
        "outlier_target_bytes": _OUTLIER_TARGET_BYTES,
        "context_nonempty_num": _CONTEXT_NONEMPTY_NUM,
        "context_nonempty_den": _CONTEXT_NONEMPTY_DEN,
        "n_clones": _N_CLONES,
    }
    comp_params_path = f"{home}/_mature_bucket_companion_params.json"
    comp_script_path = f"{home}/_mature_bucket_companions.py"
    driver.put_text(box, comp_params_path, json.dumps(comp_params))
    driver.put_text(box, comp_script_path, _COMPANION_SRC)
    comp = driver.exec(box, [testvenv_py, comp_script_path, comp_params_path], cwd=project)
    _check(comp, "companion writer")
    try:
        comp_summary = json.loads(comp.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise CheckpointError(
            f"{_FAMILY}: companion writer emitted no summary ({comp.stdout[-400:]!r})"
        ) from None
    outlier_bytes = int(comp_summary.get("outlier_bytes") or 0)
    if outlier_bytes < _OUTLIER_MIN_BYTES:
        raise CheckpointError(
            f"{_FAMILY}: outlier trail companion is {outlier_bytes} bytes, "
            f"< the {_OUTLIER_MIN_BYTES} floor — dishonest world"
        )
    summary["outlier_bytes"] = outlier_bytes
    summary["context_nonempty"] = comp_summary.get("context_nonempty")

    # 5. Install a trusted bucket-reading workflow + bind a dataset to it with
    #    DEFERRED provenance (dataset new does NOT run it). The journey's first
    #    `dataset run` is the step that captures the bucket snapshot — the #208
    #    wedge surface on pre-fix code.
    wf_root = f"{opentraces_dir}/workflows/mature-bucket-curator"
    driver.put_text(box, f"{wf_root}/SKILL.md", _WORKFLOW_SKILL)
    driver.put_text(box, f"{wf_root}/scripts/build_rows.py", _WORKFLOW_BUILD_ROWS)
    driver.put_text(box, f"{project}/mature-schema.json", _WORKFLOW_SCHEMA)
    # --query-name is LOAD-BEARING, not cosmetic (issue #213 external review,
    # CRITICAL 1): without a candidate query `dataset new` binds
    # candidate_query=None, and `run_dataset_workflow._ensure_run_source_provenance`
    # returns early when candidate_query is None — so the journey's `dataset run`
    # NEVER touches `source_provenance_for_query`, the exact whole-corpus scan the
    # #208 wedge lived in. Binding a query marker writes DEFERRED source provenance
    # at create time, so the first `dataset run` refreshes the real bucket snapshot
    # through `source_provenance_for_query(candidate_query, include_bucket_snapshot=True)`
    # — the O(corpus) path on pre-R1 code (bucket_manifest + trace_record_snapshot,
    # two full model_validate passes over all ~600 records) and the O(1) persisted
    # read on HEAD. This is what arms the red-proof.
    new = _cli_json(
        driver, box,
        "dataset", "new", "maturescale",
        "--workflow", "mature-bucket-curator",
        "--schema", f"{project}/mature-schema.json",
        "--query-name", "maturescale-query",
        "--query-scope", "all-projects",
        "--json",
        label="dataset new",
    )
    if (new.get("dataset") or {}).get("manifest", {}).get("candidate_query") is None:
        raise CheckpointError(
            f"{_FAMILY}: dataset new did NOT bind a candidate_query — the #208 "
            f"red-proof is disarmed (deferred source_provenance_for_query would "
            f"never run). ({json.dumps(new)[:300]})"
        )
    if (new.get("dataset") or {}).get("manifest", {}).get("workflow", {}).get("skill") != "mature-bucket-curator":
        raise CheckpointError(
            f"{_FAMILY}: dataset new did not bind the mature-bucket-curator workflow "
            f"({json.dumps(new)[:300]})"
        )

    # 6. Record the audit the journey templates read (largest companion, outlier
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
        composed_from="c-bucket-spine-v2-one-trace-provisional",
        delta=_mature_delta,
        cache=True,
        description=(
            "c-bucket-spine-v2-one-trace-provisional cloned to ~600 plan-080 bucket "
            "traces + ~50K TrailEvents + one >=50 MB outlier trail companion + "
            "~60% empty context companions + a trusted deferred-provenance dataset "
            "workflow ARMED with a candidate query (--query-name) so `dataset run` "
            "hits source_provenance_for_query. The scale-world perf recurrence "
            "guard (issue #213); verify_provides re-probes the >=600 count, the "
            "outlier >=50 MB, the real v2 object-store/envelope counts, AND the "
            ">=50K events-mirror head sequence."
        ),
        provides={
            "captured_traces": _N_CLONES,
            "context_tree_built": True,
            "outlier_trail_companion_min_bytes": _OUTLIER_MIN_BYTES,
            # Issue #213 external review CRITICAL 2: re-probe REAL plan-080 v2
            # bucket objects on disk (object-store current.json + per-trace
            # trace.json envelopes), NOT the builder-written state.json rows.
            "bucket_trace_envelopes_min": _N_CLONES,
            # RECOMMENDATION B: re-probe the ~50K-event scale from the events
            # mirror so the events dimension can't silently shrink.
            "trail_events_min": _N_EVENTS,
        },
    )
)
