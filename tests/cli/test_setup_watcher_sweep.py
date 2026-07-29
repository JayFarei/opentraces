"""Human rendering contract for the bounded watcher sweep."""

from click.testing import CliRunner

from opentraces.cli.setup_watcher import setup_watcher_group
from opentraces.watcher import daemon as wd


def test_human_sweep_output_reports_completed_peak_rss_breaches(monkeypatch):
    monkeypatch.setattr(
        wd,
        "run_sweep",
        lambda **_kwargs: {
            "projects": 1,
            "ok": 0,
            "rss_killed": 0,
            "peak_rss_exceeded": 1,
            "timeout_killed": 0,
            "errors": 0,
            "deferred": 0,
        },
    )

    result = CliRunner().invoke(setup_watcher_group, ["sweep"])

    assert result.exit_code == 0
    assert result.output == (
        "sweep: 1 projects, 0 ok, 0 rss-killed, "
        "1 peak-rss-exceeded, 0 timeout-killed, 0 errors\n"
    )
