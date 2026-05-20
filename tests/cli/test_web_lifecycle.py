from __future__ import annotations

# ruff: noqa: E402

import time

import pytest

pytest.skip(
    reason=(
        "legacy web review client is decommissioned until the next dataset "
        "review UI iteration lands"
    ),
    allow_module_level=True,
)

pytest.importorskip("flask")

from opentraces.clients.web.server import create_app


def test_web_lifecycle_tracks_browser_presence(tmp_path):
    app = create_app(staging_dir=str(tmp_path), state_path=str(tmp_path / "state.json"))
    client = app.test_client()
    snapshot = app.extensions["opentraces_web_lifecycle"]["snapshot"]

    initial = snapshot(stale_after=0.01)
    assert initial["seen_any_client"] is False
    assert initial["active_clients"] == 0

    response = client.post("/api/_lifecycle/ping", json={"client_id": "browser-1"})
    assert response.status_code == 200

    active = snapshot(stale_after=0.5)
    assert active["seen_any_client"] is True
    assert active["active_clients"] == 1

    response = client.post("/api/_lifecycle/disconnect?client_id=browser-1")
    assert response.status_code == 204

    inactive = snapshot(stale_after=0.5)
    assert inactive["seen_any_client"] is True
    assert inactive["active_clients"] == 0


def test_web_lifecycle_prunes_stale_clients(tmp_path):
    app = create_app(staging_dir=str(tmp_path), state_path=str(tmp_path / "state.json"))
    client = app.test_client()
    snapshot = app.extensions["opentraces_web_lifecycle"]["snapshot"]

    response = client.post("/api/_lifecycle/ping", json={"client_id": "browser-2"})
    assert response.status_code == 200

    time.sleep(0.03)
    state = snapshot(stale_after=0.01)
    assert state["seen_any_client"] is True
    assert state["active_clients"] == 0


def test_web_quit_requests_runtime_stop(tmp_path):
    app = create_app(staging_dir=str(tmp_path), state_path=str(tmp_path / "state.json"))
    client = app.test_client()
    reasons: list[str] = []
    app.extensions["opentraces_web_runtime"]["request_stop"] = reasons.append

    response = client.post("/api/_lifecycle/quit", json={"client_id": "browser-3"})

    assert response.status_code == 200
    assert reasons == ["browser quit"]
