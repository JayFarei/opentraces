"""A verifier helper that must be represented in bench source provenance."""


def assert_healthy_payload(payload: dict[str, object]) -> None:
    assert payload["healthy"] is True
