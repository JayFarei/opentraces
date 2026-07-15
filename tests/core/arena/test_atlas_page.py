from pathlib import Path

from opentraces.core.arena.atlas_page import render_atlas_page


def _atlas() -> dict:
    return {
        "schema_version": "opentraces.arena.atlas.v0",
        "product_commit": "abc123",
        "capabilities_digest": "sha256:capabilities",
        "inactive_hole_states": ["no-red-proof", "unrepresentative-world"],
        "rows": [
            {
                "id": "guarantee.proven",
                "claim": "A stored claim remains visible.",
                "nodeid": "tests/arena/test_claim.py::test_claim",
                "state": "proven",
                "latest_run_id": "run_01",
                "verdict": "pass",
                "evidence_ref": "runs/v1/run_01/result.json",
                "black_box_review": "confirmed",
            },
            {
                "id": "guarantee.unbound",
                "claim": "A claim without a run stays a hole.",
                "nodeid": "tests/arena/test_missing.py::test_missing",
                "state": "unbound",
                "latest_run_id": None,
                "verdict": None,
                "evidence_ref": None,
                "black_box_review": "unreviewed",
            },
        ],
    }


def test_atlas_page_renders_proven_and_unbound_rows_without_promoting_holes(
    tmp_path: Path,
) -> None:
    output = render_atlas_page(_atlas(), tmp_path / "atlas.html")

    html = output.read_text(encoding="utf-8")
    assert "What do we actually know is true?" in html
    assert "A stored claim remains visible." in html
    assert "PROVEN" in html
    assert "runs/v1/run_01/result.json" in html
    assert "A claim without a run stays a hole." in html
    assert "UNBOUND" in html
    assert "No stored run is bound to this claim." in html
    assert "UNREVIEWED" in html


def test_atlas_page_states_the_inactive_world_reduction(tmp_path: Path) -> None:
    output = render_atlas_page(_atlas(), tmp_path / "atlas.html")

    html = output.read_text(encoding="utf-8")
    assert "Defined but inactive in bench.v0" in html
    assert "no-red-proof" in html
    assert "unrepresentative-world" in html
    assert "not evaluated or silently treated as proven" in html


def test_atlas_page_is_byte_stable_for_the_same_projection(tmp_path: Path) -> None:
    first = render_atlas_page(_atlas(), tmp_path / "first.html").read_bytes()
    second = render_atlas_page(_atlas(), tmp_path / "second.html").read_bytes()

    assert first == second
