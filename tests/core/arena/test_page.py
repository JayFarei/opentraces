from __future__ import annotations

import html as html_module
import json
from pathlib import Path

import pytest

from opentraces.core.arena.page import render_evidence_page

from opentraces.core.arena.box import Box, BoxCommandResult
from opentraces.core.arena.contract import build_result
from opentraces.core.arena.engine import Bench
from opentraces.core.arena.engine import ScenarioSource
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
    )


def _result(run_id: str, *, recordings: dict) -> dict:
    return build_result(
        run_id=run_id,
        claim="Stored evidence remains inside its finalized run.",
        nodeid="tests/core/arena/test_page.py::test_page",
        source_ref="source/scenario.py",
        execution_mode="direct",
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
