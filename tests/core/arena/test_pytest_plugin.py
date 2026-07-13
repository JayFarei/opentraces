from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from opentraces.core.arena.pytest_plugin import _scenario_source
from opentraces.core.arena.run_store import RunStore


def test_scenario_outside_misconfigured_repository_persists_a_private_path(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    source_path = tmp_path / "Users" / "jayfarei" / "checkout" / "test_claim.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(
        'def test_claim(bench):\n    """An external scenario stays private."""\n',
        encoding="utf-8",
    )

    def test_claim(bench):
        """An external scenario stays private."""

    request = SimpleNamespace(
        node=SimpleNamespace(
            function=test_claim,
            path=source_path,
            nodeid="outside/test_claim.py::test_claim",
        )
    )
    source = _scenario_source(request, repository)
    draft = RunStore(tmp_path / "runs" / "v1").begin()
    draft.record_source(
        source_path=source.source_path,
        nodeid=source.nodeid,
        claim=source.claim,
        scenario_path=source.scenario_path,
        repository=source.repository,
        commit=source.commit,
        dirty_diff_digest=source.dirty_diff_digest,
    )
    persisted = json.loads((draft.path / "source" / "source.json").read_text())

    assert re.fullmatch(r"external/[0-9a-f]{12}\.py", persisted["scenario_path"])
    assert not Path(persisted["scenario_path"]).is_absolute()
    serialized = json.dumps(persisted).lower()
    for private in ("/users/", "/home/", "jayfarei", tmp_path.as_posix().lower()):
        assert private not in serialized
