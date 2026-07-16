from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from opentraces.core.arena.box import Box
from opentraces.core.arena.engine import Bench, ScenarioSource
from opentraces.core.arena.contract import build_result
from opentraces.core.arena.fleet import (
    FleetAttempt,
    FleetError,
    LOCAL_CONTAINER,
    RecipeInputs,
    UnsupportedPlacement,
    collect_selected_nodeids,
    execute_fleet,
    observed_max_lease_concurrency,
)
from opentraces.core.arena.run_store import RunIntegrityError, RunStore


def _source(nodeid: str) -> ScenarioSource:
    return ScenarioSource(
        nodeid=nodeid,
        claim=f"{nodeid} keeps its writable evidence isolated.",
        source_path=Path(__file__),
        scenario_path="tests/core/arena/test_fleet.py",
        repository="JayFarei/opentraces",
        commit="abc123",
        dirty_diff_digest=None,
        product_worktree="clean",
        product_dirty_diff_digest=None,
    )


def _marker_is_local(run) -> dict[str, list[str]]:
    marker = run.draft.path / "artifacts" / "attempt-marker.txt"
    assert marker.read_text(encoding="utf-8") == run.bench.source.nodeid
    return {"evidence_refs": [str(marker)]}


class ConcurrentRuntime:
    crabbox_version = "0.38.0"

    def __init__(
        self,
        nodeid: str,
        *,
        rendezvous: threading.Barrier,
        active: set[str],
        active_lock: threading.Lock,
        overlap_seen: threading.Event,
    ) -> None:
        self.nodeid = nodeid
        self.rendezvous = rendezvous
        self.active = active
        self.active_lock = active_lock
        self.overlap_seen = overlap_seen

    def configure_run_evidence(self, _run_root: Path) -> None:
        return None

    def lease(self) -> Box:
        with self.active_lock:
            self.active.add(self.nodeid)
            if len(self.active) == 2:
                self.overlap_seen.set()
        self.rendezvous.wait(timeout=5)
        return Box(
            id=f"box-{self.nodeid}",
            slug=f"box-{self.nodeid}",
            provider="local-container",
            sandbox_tier="container",
            ssh_host="127.0.0.1",
            ssh_user="runner",
            ssh_port="22",
            ssh_key="fixture-key",
            image="ubuntu:24.04",
        )

    def materialize(self, _box: Box, app_state: str, *, repository: Path) -> dict[str, object]:
        return {
            "name": app_state,
            "digest": "sha256:" + "1" * 64,
            "provides": ["python3"],
        }

    def release(self, _box: Box) -> None:
        with self.active_lock:
            self.active.remove(self.nodeid)


def test_two_attempts_overlap_without_crossing_run_or_recipe_state(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    recipe_file = tmp_path / "recipe" / "opentraces.whl"
    recipe_file.parent.mkdir()
    recipe_file.write_bytes(b"immutable wheel input")
    rendezvous = threading.Barrier(2)
    active: set[str] = set()
    active_lock = threading.Lock()
    overlap_seen = threading.Event()
    prepare_calls = 0

    def prepare() -> RecipeInputs:
        nonlocal prepare_calls
        prepare_calls += 1
        return RecipeInputs.capture([recipe_file])

    def attempt(nodeid: str, recipe: RecipeInputs) -> Path:
        assert recipe.verify() is True
        runtime = ConcurrentRuntime(
            nodeid,
            rendezvous=rendezvous,
            active=active,
            active_lock=active_lock,
            overlap_seen=overlap_seen,
        )
        bench = Bench(
            source=_source(nodeid),
            store=store,
            box_runtime=runtime,
            repository_path=Path.cwd(),
        )
        with bench.run(app_state="base-only") as run:
            assert run.draft is not None
            run.draft.write_text("artifacts/attempt-marker.txt", nodeid)
            run.verify(_marker_is_local)
        return run.final_path

    fleet = execute_fleet(
        ("scenario-a", "scenario-b"),
        store=store,
        concurrency=2,
        placement=LOCAL_CONTAINER,
        prepare_recipe=prepare,
        run_attempt=attempt,
    )

    assert overlap_seen.is_set(), "the control must exercise genuine overlap"
    assert prepare_calls == 1
    assert len({item.run_id for item in fleet.attempts}) == 2
    assert len({item.run_path for item in fleet.attempts}) == 2
    assert [item.nodeid for item in fleet.attempts] == ["scenario-a", "scenario-b"]
    assert all(item.provider == "local-container" for item in fleet.attempts)
    assert len({item.lease_lifecycle["id"] for item in fleet.attempts}) == 2
    assert fleet.observed_max_lease_concurrency == 2
    assert [hole.code for hole in fleet.coverage_holes] == [
        "remote_rented_glibc_lease_unproven",
        "x86_64_hf_emulator_unproven",
    ]
    markers = {
        (item.run_path / "artifacts" / "attempt-marker.txt").read_text(encoding="utf-8")
        for item in fleet.attempts
    }
    assert markers == {"scenario-a", "scenario-b"}
    assert recipe_file.read_bytes() == b"immutable wheel input"
    assert fleet.recipe.verify() is True


def test_observed_concurrency_requires_strictly_overlapping_lease_intervals() -> None:
    attempts = (
        SimpleNamespace(
            lease_lifecycle={
                "acquired": "2026-07-16T10:00:00Z",
                "released": "2026-07-16T10:00:01Z",
                "status": "released",
            }
        ),
        SimpleNamespace(
            lease_lifecycle={
                "acquired": "2026-07-16T10:00:01Z",
                "released": "2026-07-16T10:00:02Z",
                "status": "released",
            }
        ),
    )

    assert observed_max_lease_concurrency(attempts) == 1


def test_recipe_cache_exposes_immutable_content_not_shared_writable_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "opentraces.whl"
    source.write_bytes(b"clean")

    recipe = RecipeInputs.capture([source])
    source.write_bytes(b"poisoned after capture")

    artifact = recipe.artifacts[0]
    assert not hasattr(artifact, "path")
    assert artifact.content == b"clean"
    first = recipe.materialize(tmp_path / "attempt-a")
    second = recipe.materialize(tmp_path / "attempt-b")
    first[0].write_bytes(b"attempt-a mutation")
    assert second[0].read_bytes() == b"clean"
    assert recipe.verify() is True


def test_fleet_reloads_every_attempt_from_the_finalized_store(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")

    with pytest.raises(Exception, match="stored|finalized|exist|index"):
        execute_fleet(
            ("scenario-a",),
            store=store,
            concurrency=1,
            placement=LOCAL_CONTAINER,
            prepare_recipe=RecipeInputs.empty,
            run_attempt=lambda _nodeid, _recipe: tmp_path / "not-a-stored-run",
        )


def test_fleet_attempt_rejects_a_finalized_external_lease_symlink(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    draft = store.begin()
    lease_ref = "artifacts/lease-lifecycle.json"
    lifecycle = {
        "schema_version": "opentraces.bench.lease-lifecycle.v0",
        "id": "external-lease",
        "provider": "local-container",
        "acquired": "2026-07-16T10:00:00Z",
        "release_started": "2026-07-16T10:00:01Z",
        "released": "2026-07-16T10:00:02Z",
        "status": "released",
    }
    draft.write_json(lease_ref, lifecycle)
    result = build_result(
        run_id=draft.run_id,
        claim="A lease lifecycle stays inside its finalized run.",
        nodeid="tests/arena/test_symlink.py::test_symlink",
        source_ref="source/scenario.py",
        execution_mode="direct",
        started_at="2026-07-16T10:00:00Z",
        duration_ms=1,
        execution_status="complete",
        verdict="pass",
        reason=None,
        verifiers=[],
        evidence={"complete": True, "requirements": []},
        recordings={"rewatchable": False, "channels": []},
        artifacts=[
            {"path": lease_ref, "media_type": "application/json", "kind": "lease_lifecycle"}
        ],
        capture=None,
        pins={"environment": {"provider": "local-container"}},
    )
    finalized = draft.finalize(result)

    external = tmp_path / "external-lease.json"
    external.write_text(json.dumps(lifecycle) + "\n", encoding="utf-8")
    artifacts = finalized / "artifacts"
    lifecycle_path = finalized / lease_ref
    artifacts.chmod(0o755)
    lifecycle_path.chmod(0o600)
    lifecycle_path.unlink()
    lifecycle_path.symlink_to(external)
    integrity_path = finalized / ".integrity.json"
    integrity_path.chmod(0o600)
    integrity = json.loads(integrity_path.read_text())
    integrity["files"][lease_ref] = "sha256:" + hashlib.sha256(external.read_bytes()).hexdigest()
    integrity_path.write_text(json.dumps(integrity) + "\n", encoding="utf-8")
    index_path = store.index_root / f"{draft.run_id}.json"
    index_path.chmod(0o600)
    index = json.loads(index_path.read_text())
    index["integrity_digest"] = (
        "sha256:" + hashlib.sha256(integrity_path.read_bytes()).hexdigest()
    )
    index_path.write_text(json.dumps(index) + "\n", encoding="utf-8")

    with pytest.raises((FleetError, RunIntegrityError), match="symlink|outside|escape"):
        FleetAttempt.from_run(finalized, store=store)


def test_selection_is_pytest_node_path_and_marker_selection(tmp_path: Path) -> None:
    scenarios = tmp_path / "test_selected.py"
    scenarios.write_text(
        """import pytest

@pytest.mark.nightly_alpha
def test_alpha():
    pass

def test_beta():
    pass
""",
        encoding="utf-8",
    )

    selected = collect_selected_nodeids(
        repository=tmp_path,
        targets=(str(scenarios),),
        marker="nightly_alpha",
    )
    exact = collect_selected_nodeids(
        repository=tmp_path,
        targets=(f"{scenarios}::test_beta",),
    )

    assert selected == ("test_selected.py::test_alpha",)
    assert exact == ("test_selected.py::test_beta",)


def test_remote_placement_is_a_named_hole_not_local_proof() -> None:
    with pytest.raises(
        UnsupportedPlacement,
        match="remote_rented_glibc_lease_unproven",
    ):
        execute_fleet(
            ("scenario-a",),
            store=RunStore(Path("unused-store")),
            concurrency=1,
            placement="remote-rented",
            prepare_recipe=lambda: RecipeInputs.empty(),
            run_attempt=lambda _nodeid, _recipe: pytest.fail("must not execute"),
        )
