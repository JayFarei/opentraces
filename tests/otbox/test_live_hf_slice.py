"""Opt-in LIVE HuggingFace otbox lane.

Runs the ``live_hf`` journeys end-to-end against REAL private HF dataset
repos (one ephemeral bucket repo + one ephemeral dataset repo per box,
under the token owner's namespace). This is the only otbox lane that
talks to huggingface.co; every other lane uses the filesystem fake remote.

Gated three ways so it never runs in default CI:
  1. ``OT_OTBOX_LIVE_HF=1`` (the lane switch)
  2. a resolvable HF token (env ``OPENTRACES_LIVE_HF_TOKEN`` / ``HF_TOKEN``
     or a cached ``hf auth login`` token)
  3. ``huggingface_hub`` importable (it is a core dep)

Repos are ephemeral and KEPT ON FAILURE: a failing journey leaves its
repos (URLs printed) for debugging; a passing journey deletes them. A
session-start orphan sweep clears stragglers older than 24h.

    OT_OTBOX_LIVE_HF=1 make otbox-live-hf
"""

from __future__ import annotations

import json
import os

import pytest

from tests.otbox import live_hf
from tests.otbox.checkpoints import resolve_checkpoint
from tests.otbox.drivers import get_driver
from tests.otbox.journey import JourneyResult, run_journey

# The captured-session checkpoint gives every journey a real trace with
# blobs + events to push (preconditions: min_captured_traces = 1).
CHECKPOINT = "c-captured-real-session"

LIVE_JOURNEYS = [
    "live-hf-bucket-roundtrip",
    "live-hf-bucket-daemon-sync",
    "live-hf-dataset-publish",
    "live-hf-read-remote",
]

# Journeys whose bucket repo must end up private + carry a manifest.
_BUCKET_JOURNEYS = {
    "live-hf-bucket-roundtrip",
    "live-hf-bucket-daemon-sync",
    "live-hf-read-remote",
}


# Stable, scrubber-proof token location. This dev environment runs an HF-token
# scrubber that renames ~/.cache/huggingface/{token,stored_tokens} to *.pat.bak
# shortly after every `hf auth login`, so the standard cache cannot be relied on.
# Drop a write token here once (chmod 600) and the live lane always finds it:
#     mkdir -p ~/.opentraces && hf auth token > ~/.opentraces/otbox-live-hf-token
_STABLE_TOKEN_FILE = os.path.expanduser("~/.opentraces/otbox-live-hf-token")


def _resolve_token() -> str | None:
    """Resolve the live-lane HF token from the most durable source available.

    Order: explicit env -> stable scrubber-proof file -> hf cache (`get_token`)
    -> the scrubber's `*.pat.bak` backup as a last resort.
    """
    tok = os.environ.get("OPENTRACES_LIVE_HF_TOKEN") or os.environ.get("HF_TOKEN")
    if tok:
        return tok
    try:
        if os.path.exists(_STABLE_TOKEN_FILE):
            with open(_STABLE_TOKEN_FILE, encoding="utf-8") as fh:
                tok = fh.read().strip()
            if tok:
                return tok
    except OSError:
        pass
    try:
        from huggingface_hub import get_token

        tok = get_token()
        if tok:
            return tok
    except Exception:  # noqa: BLE001
        pass
    # Last resort: the token scrubber's backup of the cached login token.
    try:
        bak = os.path.expanduser("~/.cache/huggingface/token.pat.bak")
        if os.path.exists(bak):
            with open(bak, encoding="utf-8") as fh:
                tok = fh.read().strip()
            if tok:
                return tok
    except OSError:
        pass
    return None


def _require_live() -> str:
    if os.environ.get("OT_OTBOX_LIVE_HF") != "1":
        pytest.skip("set OT_OTBOX_LIVE_HF=1 to run the live HF otbox lane")
    token = _resolve_token()
    if not token:
        pytest.skip(
            "no HF token — set OPENTRACES_LIVE_HF_TOKEN / HF_TOKEN or run `hf auth login`"
        )
    # The box has an isolated HOME, so a cached token file is invisible to the
    # CLI subprocess. Surface the token as an env var the capability gate and
    # isolated_env(live_hf=True) both read, so it gets injected into the box.
    os.environ["OPENTRACES_LIVE_HF_TOKEN"] = token
    return token


@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state():
    """otbox boxes isolate HOME via the driver; neutralise the repo-wide
    conftest autouse fixture that would otherwise redirect HOME."""
    yield


@pytest.fixture(scope="session", autouse=True)
def _sweep_orphans_once():
    """Best-effort cleanup of stale otbox-live-* repos from earlier
    kept-on-failure runs, before this session provisions new ones."""
    if os.environ.get("OT_OTBOX_LIVE_HF") == "1" and _resolve_token():
        os.environ.setdefault("OPENTRACES_LIVE_HF_TOKEN", _resolve_token())
        try:
            deleted = live_hf.sweep_orphans(ttl_hours=24)
            if deleted:
                print(f"[otbox live_hf] swept {len(deleted)} orphan repo(s): {deleted}")
        except Exception as exc:  # noqa: BLE001
            print(f"[otbox live_hf] orphan sweep skipped: {exc}")
    yield


@pytest.fixture
def driver():
    return get_driver("local")


def _fmt_failures(result: JourneyResult) -> str:
    lines = [f"{result.name}: {result.verdict} — {result.reason}"]
    for a in result.assertions:
        if not a.ok:
            lines.append(
                f"  {a.kind} step={a.spec.get('step')} "
                f"path={a.spec.get('path')!r}: {a.message}"
            )
    for s in result.steps:
        if not s.ok:
            tail = ""
            if s.result is not None:
                tail = (s.result.stderr or s.result.stdout or "")[-400:]
            lines.append(f"  step {s.step_id!r} failed: {s.message}\n    {tail}")
    return "\n".join(lines)


def _gz_lines(path: str) -> list[str]:
    import gzip

    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [ln for ln in fh.read().splitlines() if ln.strip()]


def _verify_whole_bucket_synced(api, repo_id: str) -> None:
    """Assert every substrate of the bucket actually crossed the wire, not
    just the manifest: the event-log mirror, the per-trace envelope
    companions, the trace.json spine, and the raw-body objects. Then prove
    a couple of synced objects are byte-intact (parse the events index,
    gz-decode a companion)."""
    info = api.repo_info(repo_id=repo_id, repo_type="dataset")
    assert info.private is True, f"{repo_id} must be private"
    files = set(api.list_repo_files(repo_id=repo_id, repo_type="dataset"))

    def has(pred) -> bool:
        return any(pred(f) for f in files)

    sample = sorted(files)[:40]
    # manifest (whole-bucket ready signal)
    assert "manifest.json" in files, f"manifest.json missing: {sample}"
    # spine: per-trace trace.json
    assert has(lambda f: f.startswith("traces/v1/") and f.endswith("/trace.json")), (
        f"no per-trace trace.json spine on {repo_id}: {sample}"
    )
    # envelopes: trail/context/sources companions
    assert has(
        lambda f: f.startswith("traces/v1/") and f.endswith(".jsonl.gz")
    ), f"no per-trace .jsonl.gz envelope on {repo_id}: {sample}"
    # event-log mirror: index + at least one batch
    assert "events/v1/index.json" in files, f"events index missing on {repo_id}: {sample}"
    assert has(
        lambda f: f.startswith("events/v1/batches/") and f.endswith(".jsonl.gz")
    ), f"no event-log batch on {repo_id}: {sample}"
    # raw-body objects (raw captured source bodies)
    assert has(lambda f: "/raw/" in f and ("/sources/" in f or "/blobs/" in f)), (
        f"no raw-body object on {repo_id}: {sample}"
    )

    # Content integrity of synced objects (not just presence):
    idx_path = api.hf_hub_download(
        repo_id=repo_id, repo_type="dataset", filename="events/v1/index.json"
    )
    with open(idx_path, encoding="utf-8") as fh:
        idx = json.load(fh)
    assert idx, f"events index parsed empty on {repo_id}"
    companion = next(
        f for f in sorted(files)
        if f.startswith("traces/v1/") and f.endswith(".jsonl.gz")
    )
    comp_path = api.hf_hub_download(
        repo_id=repo_id, repo_type="dataset", filename=companion
    )
    # must gz-decode cleanly (raises on corruption); jsonl lines must parse
    for ln in _gz_lines(comp_path):
        json.loads(ln)


def _post_verify(repos: live_hf.LiveRepos, journey: str) -> None:
    """Repo-side integrity the in-box CLI cannot assert: real privacy +
    that the entire bucket actually landed on huggingface.co (every
    substrate, byte-intact)."""
    api = live_hf._api(repos.token)
    if journey in _BUCKET_JOURNEYS:
        _verify_whole_bucket_synced(api, repos.bucket_repo)
    if journey == "live-hf-dataset-publish":
        info = api.repo_info(repo_id=repos.dataset_repo, repo_type="dataset")
        assert info.private is True, f"{repos.dataset_repo} must be private"
        files = set(api.list_repo_files(repo_id=repos.dataset_repo, repo_type="dataset"))
        assert "dataset_infos.json" in files, (
            f"dataset_infos.json missing on {repos.dataset_repo}: {sorted(files)[:20]}"
        )
        assert any(f.startswith("data/") and f.endswith(".jsonl") for f in files), (
            f"no data/*.jsonl shard on {repos.dataset_repo}: {sorted(files)[:20]}"
        )


@pytest.mark.parametrize("journey", LIVE_JOURNEYS)
def test_live_hf_journey(driver, journey):
    _require_live()
    cp = resolve_checkpoint(driver, CHECKPOINT)
    box = cp.box
    # Provision BEFORE run_journey so _context() can expand
    # {live_bucket_repo}/{live_dataset_repo} from the registry.
    repos = live_hf.provision_live_repos(box.box_id)
    passed = False
    try:
        result = run_journey(driver, box, journey)
        assert result.verdict == "PASS", _fmt_failures(result)
        _post_verify(repos, journey)
        passed = True
    finally:
        live_hf.cleanup_live_repos(repos, passed=passed)
        if box.root.exists():
            driver.teardown(box)
