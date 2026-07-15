"""Pytest selection and isolated concurrent execution for the bench fleet."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .run_store import RunStore


class FleetError(RuntimeError):
    """The fleet controller could not preserve its execution contract."""


class SelectionError(FleetError):
    """Pytest could not produce an executable scenario selection."""


class UnsupportedPlacement(FleetError):
    """A requested placement has no implemented, evidenced runtime."""


class RecipeInputChanged(FleetError):
    """An immutable recipe input changed while attempts were running."""


@dataclass(frozen=True)
class FleetPlacement:
    """One implemented placement, separate from unproven coverage claims."""

    name: str
    provider: str


LOCAL_CONTAINER = FleetPlacement(name="local-container", provider="local-container")


@dataclass(frozen=True)
class CoverageHole:
    """A named proof absent from the current fleet."""

    code: str
    message: str


REMOTE_RENTED_GLIBC_HOLE = CoverageHole(
    code="remote_rented_glibc_lease_unproven",
    message="no exact run has proved the pinned emulator on a remote or rented glibc lease",
)
X86_64_EMULATOR_HOLE = CoverageHole(
    code="x86_64_hf_emulator_unproven",
    message="the pinned Hugging Face emulator has no exact x86_64 real-box proof",
)
LOCAL_CONTAINER_HOLES = (REMOTE_RENTED_GLIBC_HOLE, X86_64_EMULATOR_HOLE)


@dataclass(frozen=True)
class RecipeArtifact:
    """Immutable cached bytes for one host-side materialization input."""

    name: str
    content: bytes
    size: int
    sha256: str


@dataclass(frozen=True)
class RecipeInputs:
    """Content pins that may be shared while every box stays writable-private."""

    artifacts: tuple[RecipeArtifact, ...]
    digest: str

    @classmethod
    def capture(cls, paths: Iterable[Path]) -> "RecipeInputs":
        resolved = tuple(
            path.resolve(strict=True)
            for path in sorted((Path(path) for path in paths), key=lambda item: str(item))
        )
        names = [path.name for path in resolved]
        if len(set(names)) != len(names):
            raise ValueError("recipe input names must be unique")
        artifacts = tuple(
            RecipeArtifact(
                name=path.name,
                content=(content := path.read_bytes()),
                size=len(content),
                sha256=f"sha256:{hashlib.sha256(content).hexdigest()}",
            )
            for path in resolved
        )
        material = [
            {"ordinal": ordinal, "name": item.name, "size": item.size, "sha256": item.sha256}
            for ordinal, item in enumerate(artifacts)
        ]
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return cls(artifacts=artifacts, digest=f"sha256:{hashlib.sha256(encoded).hexdigest()}")

    @classmethod
    def empty(cls) -> "RecipeInputs":
        return cls.capture(())

    def verify(self) -> bool:
        for artifact in self.artifacts:
            if (
                len(artifact.content) != artifact.size
                or f"sha256:{hashlib.sha256(artifact.content).hexdigest()}" != artifact.sha256
            ):
                raise RecipeInputChanged(f"immutable recipe input changed: {artifact.name}")
        return True

    def materialize(self, destination: Path) -> tuple[Path, ...]:
        """Create one attempt-private writable copy of the cached inputs."""

        root = Path(destination)
        root.mkdir(parents=True, exist_ok=False)
        materialized: list[Path] = []
        for artifact in self.artifacts:
            path = root / artifact.name
            path.write_bytes(artifact.content)
            materialized.append(path)
        return tuple(materialized)


@dataclass(frozen=True)
class FleetAttempt:
    """The integrity-checked stored result of one selected node id."""

    nodeid: str
    run_id: str
    run_path: Path
    verdict: str | None
    execution_status: str
    provider: str | None

    @classmethod
    def from_run(cls, run_path: Path, *, store: RunStore) -> "FleetAttempt":
        try:
            resolved = Path(run_path).resolve(strict=True)
            store_root = store.root.resolve(strict=True)
        except OSError as exc:
            raise FleetError("fleet attempt is not a finalized stored run") from exc
        if resolved.parent != store_root:
            raise FleetError("fleet attempt is outside the finalized RunStore")
        store.verify(resolved)
        result = json.loads((resolved / "result.json").read_text(encoding="utf-8"))
        run_id = result.get("run_id")
        if run_id != resolved.name:
            raise FleetError("stored fleet result run_id does not match its directory")
        scenario = result.get("scenario")
        nodeid = scenario.get("nodeid") if isinstance(scenario, dict) else None
        if not isinstance(nodeid, str) or not nodeid:
            raise FleetError("stored fleet result has no scenario nodeid")
        pins = result.get("pins")
        environment = pins.get("environment") if isinstance(pins, dict) else None
        provider = environment.get("provider") if isinstance(environment, dict) else None
        if provider is not None and not isinstance(provider, str):
            raise FleetError("stored fleet result has an invalid placement provider")
        return cls(
            nodeid=nodeid,
            run_id=run_id,
            run_path=resolved,
            verdict=result.get("verdict"),
            execution_status=str(result.get("execution_status") or ""),
            provider=provider,
        )


@dataclass(frozen=True)
class FleetResult:
    """Ordered outcomes plus the exact shared-input and coverage facts."""

    placement: FleetPlacement
    recipe: RecipeInputs
    attempts: tuple[FleetAttempt, ...]
    coverage_holes: tuple[CoverageHole, ...]


def _resolve_placement(value: FleetPlacement | str) -> FleetPlacement:
    if value == LOCAL_CONTAINER or value == LOCAL_CONTAINER.name:
        return LOCAL_CONTAINER
    raise UnsupportedPlacement(
        f"placement {value!r} is not implemented; {REMOTE_RENTED_GLIBC_HOLE.code}"
    )


def execute_fleet(
    nodeids: Sequence[str],
    *,
    store: RunStore,
    concurrency: int,
    placement: FleetPlacement | str,
    prepare_recipe: Callable[[], RecipeInputs],
    run_attempt: Callable[[str, RecipeInputs], Path | str],
) -> FleetResult:
    """Run selected node ids concurrently with one lifecycle per attempt.

    Only immutable, content-pinned recipe inputs cross attempt boundaries. The
    callback must create and finalize a distinct ``Bench`` lifecycle for every
    node id; this controller verifies the resulting store identities and the
    observed placement before returning.
    """

    selected = tuple(nodeids)
    if not selected:
        raise SelectionError("fleet selection is empty")
    if len(set(selected)) != len(selected):
        raise SelectionError("fleet selection contains duplicate node ids")
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or concurrency < 1:
        raise ValueError("concurrency must be a positive integer")
    resolved_placement = _resolve_placement(placement)
    recipe = prepare_recipe()
    if not isinstance(recipe, RecipeInputs):
        raise TypeError("prepare_recipe must return RecipeInputs")
    recipe.verify()

    with ThreadPoolExecutor(max_workers=min(concurrency, len(selected))) as executor:
        futures = [executor.submit(run_attempt, nodeid, recipe) for nodeid in selected]
        returned = tuple(future.result() for future in futures)

    recipe.verify()
    attempt_paths: list[Path] = []
    for value in returned:
        if isinstance(value, str):
            if Path(value).name != value or not value.startswith("run_"):
                raise FleetError("run_attempt returned an invalid finalized run id")
            attempt_paths.append(store.root / value)
        elif isinstance(value, Path):
            attempt_paths.append(value)
        else:
            raise TypeError("run_attempt must return a finalized run path or run id")
    attempts = tuple(FleetAttempt.from_run(path, store=store) for path in attempt_paths)
    if tuple(attempt.nodeid for attempt in attempts) != selected:
        raise FleetError("fleet result node ids do not match the selected attempts")
    if len({attempt.run_id for attempt in attempts}) != len(attempts):
        raise FleetError("fleet attempts reused a run id")
    if len({attempt.run_path for attempt in attempts}) != len(attempts):
        raise FleetError("fleet attempts reused a writable run directory")
    if any(attempt.provider != resolved_placement.provider for attempt in attempts):
        raise FleetError("fleet result placement does not match the requested provider")
    return FleetResult(
        placement=resolved_placement,
        recipe=recipe,
        attempts=attempts,
        coverage_holes=LOCAL_CONTAINER_HOLES,
    )


def pytest_collection_finish(session: object) -> None:
    """Write selected pytest node ids for the collection-only controller."""

    report_value = os.environ.get("OT_BENCH_FLEET_COLLECTION_REPORT")
    if not report_value:
        return
    items = getattr(session, "items", ())
    payload = {"nodeids": [str(item.nodeid) for item in items]}
    Path(report_value).write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def collect_selected_nodeids(
    *,
    repository: Path,
    targets: Sequence[str],
    marker: str | None = None,
) -> tuple[str, ...]:
    """Use pytest itself to resolve node, path, directory, and marker selection."""

    if not targets:
        raise SelectionError("at least one pytest target is required")
    descriptor, report_name = tempfile.mkstemp(prefix="opentraces-bench-fleet-", suffix=".json")
    os.close(descriptor)
    report_path = Path(report_name)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "opentraces.core.arena.fleet",
        "--collect-only",
        "-q",
        *targets,
    ]
    if marker:
        command.extend(["-m", marker])
    env = dict(os.environ)
    env["OT_BENCH_FLEET_COLLECTION_REPORT"] = str(report_path)
    try:
        completed = subprocess.run(
            command,
            cwd=Path(repository),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise SelectionError("pytest collection produced no node-id report") from exc
    finally:
        report_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise SelectionError(f"pytest collection failed ({completed.returncode}): {detail}")
    raw_nodeids = payload.get("nodeids") if isinstance(payload, dict) else None
    if not isinstance(raw_nodeids, list) or any(not isinstance(item, str) for item in raw_nodeids):
        raise SelectionError("pytest collection emitted an invalid node-id report")
    nodeids = tuple(dict.fromkeys(raw_nodeids))
    if not nodeids:
        raise SelectionError("pytest selection matched no scenarios")
    return nodeids
