from __future__ import annotations

from tests.otbox import matrix


class _DummyDriver:
    pass


def test_tier1_matrix_skips_before_checkpoint_resolution_without_opt_in(monkeypatch):
    monkeypatch.delenv(matrix.TIER1_FLAG, raising=False)
    monkeypatch.setattr(
        matrix,
        "available_journeys",
        lambda: [
            {
                "name": "deferred-tier1",
                "description": "",
                "lane": "core",
                "tier": 1,
                "seed": None,
                "from_checkpoints": ["c-missing-live-only"],
                "persona": "agent",
                "requires": ["cli"],
                "preconditions": {},
                "tier_label": "gold",
                "trajectories": [],
            }
        ],
    )

    def _should_not_resolve(*_args, **_kwargs):
        raise AssertionError("Tier 1 matrix resolved a checkpoint without opt-in")

    monkeypatch.setattr(matrix, "resolve_checkpoint", _should_not_resolve)
    monkeypatch.setattr(matrix, "_check_journey_trajectories", lambda _journeys: [])

    report = matrix.run_matrix(_DummyDriver(), tier=1)

    assert report.pass_count == 0
    assert report.fail_count == 0
    assert report.error_count == 0
    assert report.skip_count == 1
    assert report.journeys_run == 1
    assert report.rows[0].journey == "deferred-tier1"
    assert report.rows[0].base_checkpoint == "c-missing-live-only"
    assert report.rows[0].verdict == "SKIP"
    assert report.rows[0].reason == matrix.TIER1_OPT_IN_REASON
