from __future__ import annotations

import html as html_module
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from opentraces.core.arena.page import render_evidence_page

from opentraces.core.arena.box import Box, BoxCommandResult
from opentraces.core.arena.contract import build_result
from opentraces.core.arena.engine import Bench
from opentraces.core.arena.engine import ScenarioSource
from opentraces.core.arena.pytest_plugin import _scenario_source
from opentraces.core.arena.run_store import RunIntegrityError, RunStore


class FakeBoxRuntime:
    def lease(self):
        return Box("fake", "fake", "local-container", "container", "host", "user", "22", "key")

    def materialize(self, box, app_state, *, repository):
        return {"name": app_state, "digest": "sha256:state", "provides": ["cli"]}

    def exec(self, box, argv, *, cwd=None, env=None, timeout=60, timing_path):
        return BoxCommandResult(list(argv), 0, "healthy\n", "", {})

    def exec_product(self, box, argv, *, cwd=None, env=None, timeout=60, timing_path):
        return self.exec(
            box,
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            timing_path=timing_path,
        )

    def release(self, box):
        return None


def _scenario(tmp_path: Path) -> ScenarioSource:
    source = tmp_path / "test_install.py"
    source.write_text('def test_install(bench):\n    """Install is healthy on a fresh box."""\n')
    return ScenarioSource(
        "test_install.py::test_install",
        "Install is healthy on a fresh box.",
        source,
        "test_install.py",
        "JayFarei/opentraces",
        "abc123",
        None,
        "clean",
        None,
    )


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _product_clone(tmp_path: Path) -> Path:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    _git(origin, "config", "user.name", "Bench Test")
    _git(origin, "config", "user.email", "bench@example.invalid")
    product = origin / "src" / "opentraces" / "payload.bin"
    product.parent.mkdir(parents=True)
    product.write_bytes(b"clean\x00product\n")
    scenario = origin / "tests" / "test_product_pin.py"
    scenario.parent.mkdir()
    scenario.write_text(
        'def test_product_pin(bench):\n    """The product pin records its worktree state."""\n',
        encoding="utf-8",
    )
    (origin / "pyproject.toml").write_text("[project]\nname = 'pin-fixture'\n", encoding="utf-8")
    _git(origin, "add", ".")
    _git(origin, "commit", "-qm", "fixture")

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--no-local", str(origin), str(clone)],
        check=True,
        capture_output=True,
        text=True,
    )
    return clone


@pytest.mark.parametrize(
    ("dirty", "expected_worktree"),
    [(False, "clean"), (True, "dirty")],
)
def test_product_worktree_state_round_trips_through_result_page_and_store(
    tmp_path: Path, dirty: bool, expected_worktree: str
) -> None:
    repository = _product_clone(tmp_path)
    if dirty:
        (repository / "src" / "opentraces" / "payload.bin").write_bytes(
            b"dirty\x00product\n"
        )

    def test_product_pin(bench):
        """The product pin records its worktree state."""

    source_path = repository / "tests" / "test_product_pin.py"
    request = SimpleNamespace(
        node=SimpleNamespace(
            function=test_product_pin,
            path=source_path,
            nodeid="tests/test_product_pin.py::test_product_pin",
        )
    )
    source = _scenario_source(request, repository)
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    bench = Bench(
        source=source,
        store=store,
        box_runtime=FakeBoxRuntime(),
        repository_path=repository,
    )

    with bench.run(app_state="base-only") as run:
        pass

    result = json.loads((run.final_path / "result.json").read_text(encoding="utf-8"))
    product_pin = result["pins"]["product"]
    page = render_evidence_page(run.final_path).read_text(encoding="utf-8")

    assert product_pin["commit"] == _git(repository, "rev-parse", "HEAD")
    assert product_pin["worktree"] == expected_worktree
    if dirty:
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", product_pin["dirty_diff_digest"])
    else:
        assert product_pin["dirty_diff_digest"] is None
    assert "PRODUCT PIN" in page
    assert product_pin["commit"] in page
    assert f"worktree {expected_worktree}" in page
    assert store.verify(run.final_path) is True


def _result(run_id: str, *, recordings: dict, execution_mode: str = "direct") -> dict:
    return build_result(
        run_id=run_id,
        claim="Stored evidence remains inside its finalized run.",
        nodeid="tests/core/arena/test_page.py::test_page",
        source_ref="source/scenario.py",
        execution_mode=execution_mode,
        started_at="2026-07-14T12:00:00Z",
        duration_ms=1,
        execution_status="complete",
        verdict="pass",
        reason=None,
        verifiers=[],
        evidence={"complete": True, "requirements": []},
        recordings=recordings,
        artifacts=[],
        capture=None,
        pins={},
    )


def test_page_is_a_read_only_projection_with_claim_verifier_and_raw_links(tmp_path: Path) -> None:
    repository_path = Path(__file__).resolve().parents[3]
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=FakeBoxRuntime(),
        repository_path=repository_path,
    )

    def health(run):
        observation = run.terminal.exec("opentraces", "doctor", "--json")
        assert observation.returncode == 0
        return {"evidence_refs": [observation.result_ref]}

    with bench.run(app_state="install-only") as run:
        run.verify(health)

    result_before = (run.final_path / "result.json").read_bytes()
    page = render_evidence_page(run.final_path)
    html = page.read_text(encoding="utf-8")
    result = json.loads(result_before)
    verifier_sources = json.loads(
        (run.final_path / "source" / "verifiers.json").read_text(encoding="utf-8")
    )

    assert "Install is healthy on a fresh box." in html
    assert "PASS" in html
    assert "test_page" in html
    assert "sha256:" in html
    assert "actions/0001/stdout" in html
    assert "actions/0001/stderr" in html
    assert "https://" not in html
    assert (run.final_path / "result.json").read_bytes() == result_before
    assert not page.is_relative_to(run.final_path)
    assert result["verifiers"][0]["source_ref"]["path"] == "tests/core/arena/test_page.py"
    assert verifier_sources["sources"][0]["path"] == "tests/core/arena/test_page.py"
    assert "tests/core/arena/test_page.py" in html
    manifest = json.loads((run.final_path / ".integrity.json").read_text(encoding="utf-8"))
    assert "actions/0001/result.json" in manifest["files"]
    assert "actions/0001/result.json" in html
    for relative in [*manifest["files"], ".integrity.json", "result.json"]:
        assert f">{html_module.escape(relative)}</a>" in html
    for private_path in ("/Users/", "/home/", repository_path.as_posix(), "jayfarei"):
        assert private_path.lower() not in result_before.decode("utf-8").lower()
        assert private_path.lower() not in html.lower()


def test_page_refuses_to_render_a_run_with_tampered_stdout(tmp_path: Path) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=FakeBoxRuntime(),
        repository_path=tmp_path,
    )

    def health(run):
        observation = run.terminal.exec("opentraces", "doctor", "--json")
        assert observation.returncode == 0

    with bench.run(app_state="install-only") as run:
        run.verify(health)

    stdout = run.final_path / "actions" / "0001" / "stdout"
    stdout.chmod(0o600)
    stdout.write_text("tampered\n", encoding="utf-8")
    output = tmp_path / "tampered-page.html"

    with pytest.raises(RunIntegrityError, match="actions/0001/stdout"):
        render_evidence_page(run.final_path, output)

    assert not output.exists()


def test_page_names_and_omits_a_recording_ref_that_escapes_the_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    draft = store.begin()
    recordings = {
        "rewatchable": True,
        "channels": [
            {
                "kind": "terminal",
                "complete": True,
                "path": "../escape.cast",
                "reason": None,
                "casts": [
                    {
                        "ordinal": 1,
                        "label": "escaped cast",
                        "cast_ref": "../escape.cast",
                        "duration_ms": 1,
                    }
                ],
            }
        ],
    }
    finalized = draft.finalize(_result(draft.run_id, recordings=recordings))
    (store.root / "escape.cast").write_text("outside the run\n", encoding="utf-8")

    assert store.verify(finalized) is True
    html = render_evidence_page(finalized).read_text(encoding="utf-8")

    assert "MISSING RECORDING" in html
    assert "../escape.cast" in html
    assert "data-cast=" not in html


def test_page_names_and_omits_an_exhaust_symlink_that_escapes_the_run(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("outside the run\n", encoding="utf-8")
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    draft = store.begin()
    (draft.path / "artifacts" / "outside.txt").symlink_to(outside)
    recordings = {"rewatchable": False, "channels": []}
    finalized = draft.finalize(_result(draft.run_id, recordings=recordings))

    assert store.verify(finalized) is True
    html = render_evidence_page(finalized).read_text(encoding="utf-8")

    assert "MISSING EXHAUST" in html
    assert "artifacts/outside.txt" in html
    assert ">artifacts/outside.txt</a>" not in html


def test_page_renders_execution_mode_as_a_fact(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    draft = store.begin()
    finalized = draft.finalize(
        _result(
            draft.run_id,
            recordings={"rewatchable": False, "channels": []},
            execution_mode="agent_replay",
        )
    )

    html = render_evidence_page(finalized).read_text(encoding="utf-8")

    assert '<div class="eyebrow">MODE</div>agent_replay' in html
