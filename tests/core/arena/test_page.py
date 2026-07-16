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
from tests.core.arena.test_browser_drive import PublicBrowserSession
from tests.core.arena.test_engine import RecordingBoxRuntime


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


def test_page_names_and_omits_every_cross_surface_ref_that_escapes_the_run(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    draft = store.begin()
    inside_manifest = draft.path / "recordings/browser/screenshots/manifest.json"
    inside_manifest.parent.mkdir(parents=True)
    inside_manifest.write_text(
        json.dumps({"screenshots": ["../escape-screenshot-member.png"]}),
        encoding="utf-8",
    )
    recordings = {
        "rewatchable": True,
        "timeline": {"complete": True, "reason": None},
        "timeline_ref": "../escape-timeline.jsonl",
        "channels": [
            {
                "kind": "terminal",
                "complete": True,
                "path": "../escape-terminal.cast",
                "reason": None,
                "casts": [
                    {
                        "ordinal": 1,
                        "label": "escaped terminal cast",
                        "cast_ref": "../escape-terminal.cast",
                        "duration_ms": 1,
                    }
                ],
            },
            {
                "kind": "browser_video",
                "complete": True,
                "path": "../escape-browser.webm",
                "reason": None,
            },
            {
                "kind": "playwright_trace",
                "complete": True,
                "path": "../escape-trace.zip",
                "reason": None,
            },
            {
                "kind": "browser_screenshots",
                "complete": True,
                "path": "../escape-screenshot-manifest.json",
                "reason": None,
            },
            {
                "kind": "browser_screenshots",
                "complete": True,
                "path": "recordings/browser/screenshots/manifest.json",
                "reason": None,
            },
        ],
    }
    finalized = draft.finalize(_result(draft.run_id, recordings=recordings))
    outside = {
        "escape-timeline.jsonl": (
            '{"sequence":1,"offset_ms":0,"surface":"browser",'
            '"event":"focus_changed","action_ref":"actions/0001",'
            '"causal_refs":[]}\n'
        ),
        "escape-terminal.cast": "terminal outside the run\n",
        "escape-browser.webm": "video outside the run\n",
        "escape-trace.zip": "trace outside the run\n",
        "escape-screenshot-manifest.json": json.dumps(
            {"screenshots": ["../escape-member-via-outside-manifest.png"]}
        ),
        "escape-screenshot-member.png": "screenshot outside the run\n",
        "escape-member-via-outside-manifest.png": "screenshot outside the run\n",
    }
    for name, content in outside.items():
        (store.root / name).write_text(content, encoding="utf-8")

    assert store.verify(finalized) is True
    rendered = render_evidence_page(finalized).read_text(encoding="utf-8")

    assert '<button class="timeline-row" data-focus-boundary' not in rendered
    assert "data-cast=" not in rendered
    assert "<video controls" not in rendered
    assert "Open Playwright trace" not in rendered
    assert '<img loading="lazy"' not in rendered
    for reference in (
        "../escape-timeline.jsonl",
        "../escape-terminal.cast",
        "../escape-browser.webm",
        "../escape-trace.zip",
        "../escape-screenshot-manifest.json",
        "../escape-screenshot-member.png",
    ):
        assert reference in rendered


def test_store_rejects_an_exhaust_symlink_before_page_render(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("outside the run\n", encoding="utf-8")
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    draft = store.begin()
    (draft.path / "artifacts" / "outside.txt").symlink_to(outside)
    recordings = {"rewatchable": False, "channels": []}
    with pytest.raises(RunIntegrityError, match="symlink"):
        draft.finalize(_result(draft.run_id, recordings=recordings))


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


def test_page_renders_each_recording_kind_against_the_stored_focus_timeline(
    tmp_path: Path,
) -> None:
    bench = Bench(
        source=_scenario(tmp_path),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=RecordingBoxRuntime(),
        repository_path=tmp_path,
        browser_factory=PublicBrowserSession,
    )

    def cross_surface(run):
        before = run.terminal.exec("printf", "before")
        browser = run.browser.inspect("main")
        after = run.terminal.exec("printf", "after")
        return {"evidence_refs": [before.result_ref, browser.result_ref, after.result_ref]}

    with bench.run(app_state="install-only") as run:
        run.verify(cross_surface)

    result_before = (run.final_path / "result.json").read_bytes()
    timeline_before = (run.final_path / "recordings/timeline.jsonl").read_bytes()
    playlist = json.loads(
        (run.final_path / "recordings/playlist.json").read_text(encoding="utf-8")
    )
    page = render_evidence_page(run.final_path)
    rendered = page.read_text(encoding="utf-8")

    assert "Cross-surface timeline" in rendered
    assert rendered.index("actions/0001") < rendered.index("actions/0002")
    assert rendered.index("actions/0002") < rendered.index("actions/0003")
    assert 'data-action-ref="actions/0001"' in rendered
    assert 'data-surface="browser"' in rendered
    assert 'data-event="focus_changed"' in rendered
    assert 'data-sequence="1"' in rendered
    assert "Causal: actions/0001" in rendered

    assert '<video controls' in rendered
    assert "recordings/browser/video/session.webm" in rendered
    assert "Open Playwright trace" in rendered
    assert "recordings/browser/trace/trace.zip" in rendered
    assert '<img loading="lazy"' in rendered
    assert "recordings/browser/screenshots/final.png" in rendered
    assert rendered.count("data-cast=") == 2
    assert "data-media-kind=\"browser_video\"" in rendered
    assert "data-focus-boundary" in rendered

    browser_start = next(
        json.loads(line)
        for line in timeline_before.decode("utf-8").splitlines()
        if (row := json.loads(line))["event"] == "action_started"
        and row["surface"] == "browser"
    )
    assert (
        f'data-media-start-offset-ms="{browser_start["offset_ms"]}"'
        in rendered
    )
    assert "media.currentTime = seekOffsetMs / 1000" in rendered
    assert "media.play()" in rendered
    assert "media.dataset.seekOffsetMs = String(seekOffsetMs)" in rendered
    assert "media.click()" in rendered

    assert set(playlist) == {"markers"}
    assert (run.final_path / "result.json").read_bytes() == result_before
    assert (run.final_path / "recordings/timeline.jsonl").read_bytes() == timeline_before
