"""`c-empty` — a bare provisioned box, nothing installed."""

from __future__ import annotations

from ..drivers.base import Driver
from ..env import Box
from . import Checkpoint, register


def _empty_builder(driver: Driver, box: Box) -> None:
    """No-op: a freshly-provisioned box *is* the empty checkpoint.

    The provisioner already created the isolated HOME / project / fake-
    remote directory layout. Recording the state here is just for the
    audit trail.
    """
    box.notes["c_empty_audit"] = {
        "home": str(box.home),
        "project": str(box.project),
        "fake_remote": str(box.fake_remote),
    }


register(
    Checkpoint(
        name="c-empty",
        builder=_empty_builder,
        # Caching a provisioned-only state would just copy empty dirs —
        # provisioning is fast and reliable, so skip the cache.
        cache=False,
        description="A bare provisioned box with isolated HOME and an empty project tree. Nothing else.",
    )
)
