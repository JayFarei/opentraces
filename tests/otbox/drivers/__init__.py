"""otbox substrate drivers.

A driver provisions an isolated environment, executes commands inside it,
and tears it down. The driver interface is the seam that lets Tier 0
(local / docker) and Tier 1 (remote lease) share one set of journey
definitions.
"""

from __future__ import annotations

from .base import Driver, ExecResult
from .local import LocalDriver

_REGISTRY: dict[str, type] = {"local": LocalDriver}

try:  # docker driver is optional — only registered if the module imports
    from .docker import DockerDriver

    _REGISTRY["docker"] = DockerDriver
except Exception:  # pragma: no cover - docker driver is best-effort
    pass

try:
    from .remote import RemoteDriver

    _REGISTRY["remote"] = RemoteDriver
except Exception:  # pragma: no cover - tier 1 is opt-in
    pass


def get_driver(name: str) -> Driver:
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise ValueError(
            f"unknown otbox driver {name!r}; available: {sorted(_REGISTRY)}"
        ) from None


def available_drivers() -> list[str]:
    return sorted(_REGISTRY)


__all__ = ["Driver", "ExecResult", "LocalDriver", "get_driver", "available_drivers"]
