"""BKT-1 negative-space egress verifier (issue #62).

Claim under test: *raw traces never leave the bucket without explicit
opt-in.* The honest way to prove a negative is to drive the full
capture+publish arc inside a box and assert that the ONLY thing that
appeared on any egress surface is attributable to the one explicit
``dataset publish`` — and that nothing raw came with it.

Two egress surfaces are watched recursively, before the arc and after
every stage:

  (a) the dataset fake remote (``OPENTRACES_PLAN058_FAKE_REMOTE_ROOT``
      = ``box.fake_remote``) — where ``dataset publish`` lands shards;
  (b) a fake *bucket* remote root, configured via
      ``bucket.remote.provider=fake`` (the seam
      ``core/bucket_store.py::fake_remote_root`` reads). The arc never
      runs ``bucket remote push``, so this surface must stay at zero
      bytes — that is the load-bearing "configured-but-never-pushed"
      claim.

The arc mirrors the argv of
``catalogue/journeys/agent-session-to-published-dataset.toml`` (plan
064 R3/R5, checkpoint ``c-captured-real-session``) but runs as a pytest
slice because the ledger diff + gzip raw-content scan exceed the
journey assertion vocabulary.

Hermeticity: the arc runs under the standard ``isolated_env`` (HF
tokens stripped) PLUS ``HF_ENDPOINT`` pointed at a connection-refusing
address. The arc must still complete end-to-end; a control check proves
the deny seam is live. This demonstrates no arc step silently required
the real network.

Anti-vacuity: ``test_inject_raw_egress_turns_check_red`` applies the
``inject-raw-egress`` world-fault (``mutations.py``) mid-arc and asserts
the negative-space check goes RED — a guard that cannot fail is worthless.

OUT of scope (do not add): a live-HF arm. The negative-space property is
meaningless against a real remote, so there is no ``live_hf`` lane here.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.otbox.drivers import get_driver
from tests.otbox.checkpoints import resolve_checkpoint
from tests.otbox.mutations import inject_raw_egress

# A connection-refusing endpoint: nothing listens on TCP/9 (discard).
DENY_ENDPOINT = "http://127.0.0.1:9"

# The repo id the arc publishes to (mirrors the journey TOML). Its
# subtree under the dataset fake remote is the ONLY place new files may
# legitimately appear.
PUBLISH_REPO = "captured/dataset"

# The local dataset the arc creates (mirrors the journey TOML).
DATASET_NAME = "captured-session"


@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state():
    """otbox boxes isolate HOME via the driver; neutralise the repo-wide
    conftest autouse fixture (same override as test_agent_session_slice)."""
    yield


@pytest.fixture
def driver():
    return get_driver("local")


# --------------------------------------------------------------------------
# Egress ledger — the verifier's evidence object
# --------------------------------------------------------------------------
def _snapshot_surface(root: Path) -> dict[str, tuple[str, int]]:
    """Recursive (relpath -> (sha256, size)) over every file under ``root``."""
    out: dict[str, tuple[str, int]] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            data = p.read_bytes()
            out[str(p.relative_to(root))] = (
                hashlib.sha256(data).hexdigest(),
                len(data),
            )
    return out


@dataclass
class EgressLedger:
    """Before/after snapshots of both egress surfaces across the arc."""

    dataset_remote: Path
    bucket_remote: Path
    stages: list[tuple[str, dict, dict]] = field(default_factory=list)

    def record(self, stage: str) -> None:
        self.stages.append(
            (
                stage,
                _snapshot_surface(self.dataset_remote),
                _snapshot_surface(self.bucket_remote),
            )
        )

    def baseline_dataset(self) -> dict:
        return self.stages[0][1]

    def final_dataset(self) -> dict:
        return self.stages[-1][1]

    def final_bucket(self) -> dict:
        return self.stages[-1][2]

    def dataset_diff(self) -> dict[str, tuple[str, int]]:
        """New or changed files on the dataset remote, baseline -> final."""
        base = self.baseline_dataset()
        final = self.final_dataset()
        return {
            rel: meta
            for rel, meta in final.items()
            if base.get(rel) != meta
        }


def _is_attributable_to_publish(relpath: str) -> bool:
    """A diffed dataset-remote path is attributable to the explicit publish
    iff it lives under the published repo's subtree AND is one of the
    public-surface file kinds the publish writes.

    No wildcard broader than the published repo subtree: any path outside
    ``<repo>/`` fails immediately. Within the subtree the allowed set is
    closed — data shard(s), ``dataset_infos.json``, the dataset card
    (``README.md``), the row/contract schema(s), and the fake-remote
    control-plane markers created by ``dataset remote create`` (``.fake_*``
    — metadata, not raw trace data).
    """
    prefix = PUBLISH_REPO + "/"
    if not relpath.startswith(prefix):
        return False
    tail = relpath[len(prefix):]
    if tail.startswith("data/") and tail.endswith(".jsonl"):
        return True
    if tail == "dataset_infos.json":
        return True
    if tail == "README.md":
        return True
    if tail.startswith("schemas/"):
        return True
    if tail in {"quality.json"}:
        return True
    # Control-plane markers from `dataset remote create` / fake head — these
    # carry repo visibility + a fake commit pointer, never trace content.
    if tail in {".fake_head", ".fake_meta.json"}:
        return True
    return False


def _gunzip_if_needed(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix == ".gz" or path.name.endswith(".jsonl.gz"):
        try:
            return gzip.decompress(data)
        except OSError:
            return data
    return data


def _scan_egressed_files_for_markers(
    surfaces: list[Path], markers: list[str]
) -> list[tuple[str, str]]:
    """Return (marker, file) pairs where a raw marker leaked onto an egress
    surface. Gunzips .gz before scanning."""
    hits: list[tuple[str, str]] = []
    marker_bytes = [(m, m.encode("utf-8")) for m in markers]
    for root in surfaces:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            blob = _gunzip_if_needed(p)
            for m, mb in marker_bytes:
                if mb in blob:
                    hits.append((m, str(p)))
    return hits


# --------------------------------------------------------------------------
# Approved-rows ground truth — read from the LOCAL dataset, never the remote
# --------------------------------------------------------------------------
def _canonical_row(row: dict) -> str:
    # Mirrors core/datasets.py::_canonical_json (the shard line format).
    return json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _approved_local_rows(box) -> list[str]:
    """Canonical-JSON lines of the local dataset rows in publishable /
    published state — the ONLY rows allowed to egress. This is pre-egress
    ground truth (publication_state.json + row_index.jsonl + local data
    files); it must never be derived from the remote under test."""
    dataset_dir = box.home / ".opentraces" / "datasets" / DATASET_NAME
    state_path = dataset_dir / ".opentraces" / "publication_state.json"
    index_path = dataset_dir / ".opentraces" / "row_index.jsonl"
    assert state_path.exists(), f"missing publication state: {state_path}"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    approved_ids = {
        row_id
        for row_id, entry in state.get("rows", {}).items()
        if entry.get("status") in {"publishable", "published"}
    }
    rows: list[str] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry["row_id"] not in approved_ids:
            continue
        data_lines = (dataset_dir / entry["data_file"]).read_text(
            encoding="utf-8"
        ).splitlines()
        rows.append(_canonical_row(json.loads(data_lines[entry["line"] - 1])))
    return rows


def _published_remote_rows(box) -> list[str]:
    """Canonical-JSON lines of every row currently on the published repo's
    data/ subtree (live read — the actual final egress state)."""
    rows: list[str] = []
    remote_data = box.fake_remote / PUBLISH_REPO / "data"
    if not remote_data.exists():
        return rows
    for shard in sorted(remote_data.glob("*.jsonl")):
        for line in shard.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(_canonical_row(json.loads(line)))
    return rows


# --------------------------------------------------------------------------
# Marker selection — raw content the row projection drops
# --------------------------------------------------------------------------
def _row_corpus(box) -> str:
    """The legitimate egress content: the LOCAL approved rows concatenated.
    A valid raw marker must be ABSENT from this corpus (so the
    ``source_trace_id`` lineage, which legitimately rides the row, is never
    a marker). Deliberately NOT read from the remote shards — deriving the
    exclusion corpus from the egress output under test would let a leaked
    raw string filter itself out of the marker set (adversary finding 2,
    issue #62)."""
    return "\n".join(_approved_local_rows(box))


def _select_raw_markers(box, trace_id: str, min_markers: int = 2) -> list[str]:
    """Pick >=2 raw marker strings from the captured trace's bucket
    ``trace.json`` that are ABSENT from the published rows.

    Strategy (source-agnostic across artifact / synthetic): walk every
    string in the raw trace record, keep human/command-shaped strings
    (contain whitespace, 12..200 chars) that do not appear in the row
    corpus and do not contain the trace_id. Whitespace excludes
    hashes/uuids/paths-without-spaces; the row-corpus exclusion excludes
    the legitimate lineage fields."""
    bucket = box.home / ".opentraces" / "bucket" / "traces" / "v1"
    trace_json = None
    for tj in bucket.rglob("trace.json"):
        if trace_id in str(tj):
            trace_json = tj
            break
    if trace_json is None:  # fall back to any captured trace.json
        for tj in bucket.rglob("trace.json"):
            trace_json = tj
            break
    assert trace_json is not None, "no bucket trace.json found to mine markers from"
    record = json.loads(trace_json.read_text(encoding="utf-8"))

    found: list[str] = []
    seen: set[str] = set()

    def walk(obj, depth=0):
        if depth > 10:
            return
        if isinstance(obj, str):
            s = obj
            if (
                12 <= len(s) <= 200
                and (" " in s or "\n" in s)
                and trace_id not in s
                and s not in seen
            ):
                seen.add(s)
                found.append(s)
        elif isinstance(obj, dict):
            for v in obj.values():
                walk(v, depth + 1)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, depth + 1)

    walk(record)

    row_corpus = _row_corpus(box)
    markers = [s for s in found if s not in row_corpus]
    assert len(markers) >= min_markers, (
        f"could not select >={min_markers} raw markers absent from the "
        f"published rows; got {len(markers)}: {markers[:5]}"
    )
    return markers[:max(min_markers, 3)]


# --------------------------------------------------------------------------
# Arc driver — mirrors agent-session-to-published-dataset.toml argv
# --------------------------------------------------------------------------
def _configure_bucket_fake_remote(box) -> Path:
    """Configure (but never push) a fake bucket remote via config so
    ``fake_remote_root()`` resolves it — the claim must read
    'configured-but-never-pushed', not 'env var present'."""
    fake_bucket = box.root / "fake-bucket-remote"
    fake_bucket.mkdir(parents=True, exist_ok=True)
    cfg_path = box.home / ".opentraces" / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.setdefault("bucket", {})
    cfg["bucket"].setdefault("remote", {})
    cfg["bucket"]["remote"].update(
        {
            "enabled": True,
            "provider": "fake",
            "url": f"file://{fake_bucket}",
        }
    )
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return fake_bucket


def _arc_env_extra() -> dict[str, str]:
    # HF_ENDPOINT deny seam: any arc step that silently reached the real
    # network would hang/fail against a connection-refused address. The
    # fake-remote seam keeps the arc fully offline, so it must still pass.
    return {"HF_ENDPOINT": DENY_ENDPOINT}


def _exec(driver, box, argv, *, check=True):
    base = driver.cli_argv(box)
    res = driver.exec(box, base + list(argv), env_extra=_arc_env_extra())
    if check:
        assert res.returncode == 0, (
            f"arc step {argv} failed rc={res.returncode}\n"
            f"STDOUT:\n{res.stdout[-1500:]}\nSTDERR:\n{res.stderr[-1500:]}"
        )
    return res


def _seed_row_files(box, trace_id: str) -> None:
    proj = box.project
    row = {
        "source_trace_id": trace_id,
        "source_unit_id": f"tu:{trace_id}:trace",
        "summary": "captured: add farewell() helper to src/app.py",
    }
    (proj / "captured_rows.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )
    schema = {
        "type": "object",
        "properties": {
            "source_trace_id": {"type": "string"},
            "source_unit_id": {"type": "string"},
            "summary": {"type": "string"},
        },
        "required": ["source_trace_id", "summary"],
    }
    (proj / "captured_schema.json").write_text(
        json.dumps(schema), encoding="utf-8"
    )


def _drive_arc(driver, box, ledger: EgressLedger, *, inject_fault=None):
    """Run the capture -> publish arc, recording the ledger before the arc
    and after each stage. ``inject_fault`` (callable(box, repo) -> str), if
    given, fires after ``dataset remote create`` (the subtree exists) and
    before publish — the mid-arc egress injection used by the kill test."""
    trace_id = box.notes["c_captured_session_audit"]["trace_id"]
    ledger.record("baseline")

    _exec(driver, box, ["trace", "index", "--json"])
    ledger.record("trace-index")

    res = _exec(driver, box, ["trace", "query", "farewell", "--limit", "5", "--json"])
    assert trace_id in res.stdout, "captured trace did not surface in trace query"
    ledger.record("trace-query")

    res = _exec(driver, box, ["trace", "discover", "farewell", "--json", "--limit", "5"])
    assert trace_id in res.stdout, "captured trace did not surface in trace discover"
    ledger.record("trace-discover")

    _seed_row_files(box, trace_id)
    _exec(
        driver,
        box,
        [
            "dataset", "new", "captured-session",
            "--rows-file", "captured_rows.jsonl",
            "--schema", "captured_schema.json",
        ],
    )
    ledger.record("dataset-new")

    _exec(
        driver,
        box,
        ["dataset", "remote", "create", "captured-session", PUBLISH_REPO, "--private"],
    )
    ledger.record("remote-create")

    if inject_fault is not None:
        note = inject_fault(box, PUBLISH_REPO)
        ledger.record(f"inject:{note}")

    _exec(driver, box, ["dataset", "review", "approve", "captured-session", "--all"])
    ledger.record("review-approve")

    res = _exec(driver, box, ["dataset", "publish", "captured-session"])
    assert "published: rows=1" in res.stdout, res.stdout
    ledger.record("publish")
    return trace_id


# --------------------------------------------------------------------------
# Negative-space evaluation — the verdict
# --------------------------------------------------------------------------
def _negative_space_violations(box, ledger: EgressLedger, markers: list[str]):
    """Return a list of human-readable violation strings (empty == PASS).

    The negative-space property holds iff:
      1. every diffed dataset-remote path is attributable to the explicit
         publish (under the published repo subtree, allowed kind);
      2. the fake bucket remote root has zero files (never pushed);
      3. no raw marker appears in any egressed file (gunzip first);
      4. the published data shards carry EXACTLY the locally approved rows
         (multiset equality, canonical JSON) — no extra, missing, or
         mutated rows.
    """
    violations: list[str] = []

    # (1) dataset-remote diff attribution
    for rel in sorted(ledger.dataset_diff()):
        if not _is_attributable_to_publish(rel):
            violations.append(
                f"unattributable egress on dataset remote: "
                f"{ledger.dataset_remote / rel}"
            )

    # (2) bucket zero-bytes
    final_bucket = ledger.final_bucket()
    if final_bucket:
        for rel, (_sha, size) in sorted(final_bucket.items()):
            violations.append(
                f"bucket remote was never pushed but contains "
                f"{ledger.bucket_remote / rel} ({size} bytes)"
            )

    # (3) raw markers must not appear on any egress surface
    hits = _scan_egressed_files_for_markers(
        [ledger.dataset_remote, ledger.bucket_remote], markers
    )
    for marker, fpath in hits:
        violations.append(
            f"raw marker {marker[:50]!r} leaked into egressed file {fpath}"
        )

    # (4) row-level equality: published rows == approved rows, exactly
    approved = sorted(_approved_local_rows(box))
    published = sorted(_published_remote_rows(box))
    if approved != published:
        extra = [r for r in published if r not in approved]
        missing = [r for r in approved if r not in published]
        violations.append(
            "published rows are not exactly the approved rows: "
            f"{len(extra)} extra/mutated, {len(missing)} missing; "
            f"extra={[r[:120] for r in extra[:3]]} "
            f"missing={[r[:120] for r in missing[:3]]}"
        )

    return violations


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------
def test_deny_endpoint_seam_is_live():
    """Control: a direct request to the deny endpoint must fail. If this
    endpoint silently accepted connections, the network-deny claim in the
    arc test would be vacuous."""
    import urllib.error
    import urllib.request

    with pytest.raises((urllib.error.URLError, OSError)):
        urllib.request.urlopen(DENY_ENDPOINT, timeout=3)  # noqa: S310


def test_negative_space_no_raw_egress(driver):
    """The full capture+publish arc egresses ONLY the explicit publish's
    public surface; nothing raw leaves, and the configured-but-unpushed
    bucket remote stays at zero bytes — all under a live network-deny seam."""
    cp = resolve_checkpoint(driver, "c-captured-real-session")
    box = cp.box
    try:
        fake_bucket = _configure_bucket_fake_remote(box)
        ledger = EgressLedger(
            dataset_remote=box.fake_remote, bucket_remote=fake_bucket
        )
        trace_id = _drive_arc(driver, box, ledger)

        # Marker selection happens AFTER the arc so the row corpus exists.
        markers = _select_raw_markers(box, trace_id)

        violations = _negative_space_violations(box, ledger, markers)
        assert not violations, (
            "negative-space egress check FAILED:\n  "
            + "\n  ".join(violations)
        )

        # Positive sanity: the publish DID land its public surface (the
        # check is not vacuously green on an empty remote).
        diff = ledger.dataset_diff()
        assert any(
            r.startswith(f"{PUBLISH_REPO}/data/") and r.endswith(".jsonl")
            for r in diff
        ), f"publish produced no data shard; diff={sorted(diff)}"
        assert f"{PUBLISH_REPO}/dataset_infos.json" in diff, sorted(diff)

        # Row-level equality, stated explicitly for the evidence trail:
        # the shard carries exactly the one approved row, nothing else.
        approved = sorted(_approved_local_rows(box))
        published = sorted(_published_remote_rows(box))
        assert published == approved, (approved, published)
        assert len(published) == 1, published

        # Bucket zero-bytes, stated explicitly for the evidence trail.
        assert ledger.final_bucket() == {}, ledger.final_bucket()
    finally:
        if box.root.exists():
            driver.teardown(box)


def test_inject_raw_egress_turns_check_red(driver):
    """Mutation-kill: injecting a raw ``trace.json`` copy into the egress
    surfaces mid-arc MUST make the negative-space check go RED. A kill that
    cannot fail is a hard reject (guarantees.toml header rule), so we prove
    the verifier actually fires before trusting its green."""
    cp = resolve_checkpoint(driver, "c-captured-real-session")
    box = cp.box
    try:
        fake_bucket = _configure_bucket_fake_remote(box)
        ledger = EgressLedger(
            dataset_remote=box.fake_remote, bucket_remote=fake_bucket
        )

        def _inject(b, repo):
            return inject_raw_egress(b, repo, fake_bucket)

        trace_id = _drive_arc(driver, box, ledger, inject_fault=_inject)
        markers = _select_raw_markers(box, trace_id)

        violations = _negative_space_violations(box, ledger, markers)
        assert violations, (
            "VACUOUS VERIFIER: injected a raw trace.json onto the egress "
            "surfaces mid-arc and the negative-space check still passed"
        )
        # The kill must name the offending path(s) and trip BOTH the
        # unattributable-egress rail and the bucket zero-bytes rail.
        joined = "\n".join(violations)
        assert "unattributable egress" in joined, violations
        assert "bucket remote was never pushed" in joined, violations

        # Rail-4 kill: forge an extra row inside an ALLOWED data shard
        # (the exact gap from adversary finding 1 — path attribution alone
        # would wave it through) and prove the row-equality rail goes red.
        shards = sorted((box.fake_remote / PUBLISH_REPO / "data").glob("*.jsonl"))
        assert shards, "publish produced no data shard to forge into"
        with shards[0].open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"summary": "forged row injected post-publish"}) + "\n")
        violations = _negative_space_violations(box, ledger, markers)
        assert any(
            "published rows are not exactly the approved rows" in v
            for v in violations
        ), violations
    finally:
        if box.root.exists():
            driver.teardown(box)
