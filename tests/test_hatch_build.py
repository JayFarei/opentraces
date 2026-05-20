from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from hatch_build import OptionalViewerBuildHook


def _make_hook(tmp_path: Path, *, target_name: str) -> OptionalViewerBuildHook:
    build_config = SimpleNamespace(build_force_include={})
    return OptionalViewerBuildHook(
        root=str(tmp_path),
        config={},
        build_config=build_config,
        metadata=None,
        directory=str(tmp_path / "dist"),
        target_name=target_name,
    )


def test_optional_viewer_build_hook_skips_missing_bundle(tmp_path: Path) -> None:
    hook = _make_hook(tmp_path, target_name="wheel")
    build_data = {}

    hook.initialize("standard", build_data)

    assert hook.build_config.build_force_include == {}


def test_optional_viewer_build_hook_ignores_bundle_for_wheel(tmp_path: Path) -> None:
    viewer_dist = tmp_path / "web" / "viewer" / "dist"
    viewer_dist.mkdir(parents=True)
    (viewer_dist / "index.html").write_text("<!doctype html>")

    hook = _make_hook(tmp_path, target_name="wheel")
    build_data = {}

    hook.initialize("standard", build_data)

    assert hook.build_config.build_force_include == {}


def test_optional_viewer_build_hook_ignores_bundle_for_sdist(tmp_path: Path) -> None:
    viewer_dist = tmp_path / "web" / "viewer" / "dist"
    viewer_dist.mkdir(parents=True)
    (viewer_dist / "index.html").write_text("<!doctype html>")

    hook = _make_hook(tmp_path, target_name="sdist")
    build_data = {}

    hook.initialize("standard", build_data)

    assert hook.build_config.build_force_include == {}
