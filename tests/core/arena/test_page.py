from __future__ import annotations

import html as html_module
import json
from pathlib import Path

from opentraces.core.arena.page import render_evidence_page

from opentraces.core.arena.box import Box, BoxCommandResult
from opentraces.core.arena.engine import Bench
from opentraces.core.arena.engine import ScenarioSource
from opentraces.core.arena.run_store import RunStore


class FakeBoxRuntime:
    def lease(self):
        return Box("fake", "fake", "local-container", "container", "host", "user", "22", "key")

    def materialize(self, box, app_state, *, repository):
        return {"name": app_state, "digest": "sha256:state", "provides": ["cli"]}

    def exec(self, box, argv, *, cwd=None, env=None, timeout=60, timing_path):
        return BoxCommandResult(list(argv), 0, "healthy\n", "", {})

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
    for relative in [*manifest["files"], ".integrity.json", "result.json"]:
        assert f">{html_module.escape(relative)}</a>" in html
    for private_path in ("/Users/", "/home/", repository_path.as_posix(), "jayfarei"):
        assert private_path.lower() not in result_before.decode("utf-8").lower()
        assert private_path.lower() not in html.lower()
