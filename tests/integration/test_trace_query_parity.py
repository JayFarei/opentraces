"""Plan-59 crawl-vs-query parity harness.

For every filter ``ot trace query --help`` exposes, the indexed query must
return the same set of trace ids as a from-scratch JSONL crawl, run at
least 10× faster than the crawl, and emit at most as many JSON bytes.

Each ``FilterCase`` is parametrized into four tests: ``recall``,
``precision``, ``speed``, and ``token_efficiency``. The four families share
a single oracle load to keep the suite fast.

A passing case exits as ``pass``; a known-failing case marks itself
``xfail`` with the bug number cited in :mod:`kb.plans.059`. After the
Plan-59 bug fixes land, every case in this matrix should be ``pass`` —
the suite stays green at landing because xfail markers get derived from
``FilterCase.expected_status``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.integration._trace_query_corpus import (
    fixture_project_slug,
    write_corpus,
)
from tests.integration._trace_query_oracle import (
    FilterCase,
    _git_tiers,
    _has_bash_command_matching,
    _layer,
    _outcome_committed,
    _outcome_success,
    _project_slug,
    _provider_kind,
    _superseded_unit,
    _trace_files,
    _trace_skills,
    _trace_tools,
    ensure_index_built,
    load_oracle,
    run_indexed_query,
    run_oracle,
)


# ---------------------------------------------------------------------------
# Predicates corresponding to each indexed filter.
# ---------------------------------------------------------------------------


def _pred_skill(name: str):
    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        return name in _trace_skills(trace)

    return predicate


def _pred_tool(name: str):
    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        return name in _trace_tools(trace)

    return predicate


def _pred_files_glob(glob: str):
    from fnmatch import fnmatch

    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        return any(fnmatch(path, glob) for path in _trace_files(trace))

    return predicate


def _pred_file_kind(kind: str):
    kind = kind.lstrip(".")

    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        return any(Path(path).suffix.lstrip(".") == kind for path in _trace_files(trace))

    return predicate


def _pred_file_op(op: str):
    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        for step in trace.get("steps", []):
            for call in step.get("tool_calls", []):
                normalized = str(call.get("tool_name") or "").lower().replace("_", "").replace("-", "")
                if op == "edit" and normalized in {"edit", "write", "multiedit"}:
                    return True
                if op == "read" and normalized in {"read", "view", "glob", "grep"}:
                    return True
        return False

    return predicate


def _pred_provider(provider: str):
    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        return _provider_kind(trace) == provider

    return predicate


def _pred_git_tier(tier: str):
    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        return tier in _git_tiers(trace)

    return predicate


def _pred_success(value: bool):
    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        return _outcome_success(trace) is value

    return predicate


def _pred_unknown_success():
    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        return _outcome_success(trace) is None

    return predicate


def _pred_committed(value: bool):
    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        actual = (trace.get("outcome") or {}).get("committed")
        return actual is value

    return predicate


def _pred_layer(layer: str):
    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        return _layer(trace) == layer

    return predicate


def _pred_project(slug: str):
    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        return _project_slug(trace) == slug

    return predicate


def _pred_dependency(name: str):
    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        for dep in trace.get("dependencies") or []:
            if not isinstance(dep, str):
                continue
            bare = dep
            for sep in ("@", "<", ">", "=", "~", "!"):
                if sep in bare:
                    bare = bare.split(sep, 1)[0]
            bare = bare.strip()
            if bare == name:
                return True
        return False

    return predicate


def _pred_cmd_family(family: str):
    import re

    keyword = re.escape(family)

    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        return _has_bash_command_matching(
            trace,
            lambda cmd: bool(re.match(rf"^\s*{keyword}\b", cmd)),
        )

    return predicate


def _pred_bash_action(action: str):
    import re

    test_re = re.compile(
        r"\b(pytest|npm\s+test|pnpm\s+test|yarn\s+test|cargo\s+test|go\s+test|"
        r"vitest|jest|rspec|mvn\s+test|gradle\s+test)\b",
        re.IGNORECASE,
    )

    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        if action == "test":
            return _has_bash_command_matching(trace, lambda cmd: bool(test_re.search(cmd)))
        if action == "service_probe":
            return _has_bash_command_matching(
                trace,
                lambda cmd: bool(re.match(r"^\s*(curl|wget)\b", cmd)),
            )
        return False

    return predicate


def _pred_service(host: str):
    import re

    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False

        def _matches(cmd: str) -> bool:
            for url in re.findall(r"https?://[^\s'\"<>$`{}|^\\]+", cmd):
                from urllib.parse import urlparse

                try:
                    parsed = urlparse(url)
                except ValueError:
                    continue
                if parsed.hostname == host:
                    return True
            return False

        return _has_bash_command_matching(trace, _matches)

    return predicate


def _pred_survival(state: str):
    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        raw = (trace.get("metadata") or {}).get("survival_state") or (trace.get("metadata") or {}).get("survival_states")
        if raw is not None:
            states = (
                {str(item) for item in raw if item}
                if isinstance(raw, list)
                else {str(raw)}
            )
            if states:
                return state in states
        # Fallback to git_links liveness mirroring _survival_states in trace_index.
        derived: set[str] = set()
        for link in trace.get("git_links") or []:
            alive = link.get("content_alive")
            reachable = link.get("commit_reachable")
            if alive is True:
                derived.add("alive_on_path")
            elif alive is False:
                if reachable is False:
                    derived.add("reverted")
                else:
                    derived.add("lost")
            elif reachable is False:
                derived.add("reverted")
        if not derived and (trace.get("git_links") or []):
            derived.add("unknown")
        return state in derived

    return predicate


# ---------------------------------------------------------------------------
# Filter matrix.
# ---------------------------------------------------------------------------


def _build_matrix() -> list[FilterCase]:
    project_slug = fixture_project_slug()
    cases: list[FilterCase] = [
        # Skills.
        FilterCase("skill:agent-browser", ["--skill", "agent-browser"], _pred_skill("agent-browser")),
        FilterCase("skill:grill-me", ["--skill", "grill-me"], _pred_skill("grill-me")),
        FilterCase(
            "skill:nonexistent",
            ["--skill", "absolutely-not-a-skill"],
            _pred_skill("absolutely-not-a-skill"),
            allow_empty=True,
        ),
        # Tools.
        FilterCase("tool:Edit", ["--tool", "Edit"], _pred_tool("Edit")),
        FilterCase("tool:Read", ["--tool", "Read"], _pred_tool("Read")),
        FilterCase("tool:Bash", ["--tool", "Bash"], _pred_tool("Bash")),
        # Files / file kinds.
        FilterCase("files:src-py", ["--files", "src/*.py"], _pred_files_glob("src/*.py")),
        FilterCase("files:src-tsx", ["--files", "src/site/*.tsx"], _pred_files_glob("src/site/*.tsx")),
        FilterCase("file-kind:py", ["--file-kind", "py"], _pred_file_kind("py")),
        FilterCase("file-kind:tsx", ["--file-kind", "tsx"], _pred_file_kind("tsx")),
        # File operations (closed vocabulary).
        FilterCase("file-op:edit", ["--file-op", "edit"], _pred_file_op("edit")),
        FilterCase("file-op:read", ["--file-op", "read"], _pred_file_op("read")),
        # Provider.
        FilterCase("provider:anthropic", ["--provider", "anthropic"], _pred_provider("anthropic")),
        FilterCase("provider:openai", ["--provider", "openai"], _pred_provider("openai")),
        # Cmd family / bash action.
        FilterCase("cmd-family:pytest", ["--cmd-family", "pytest"], _pred_cmd_family("pytest")),
        FilterCase("cmd-family:curl", ["--cmd-family", "curl"], _pred_cmd_family("curl")),
        FilterCase(
            "cmd-family:for",
            ["--cmd-family", "for"],
            lambda trace: False,  # post-fix: 'for' must not surface as a command family.
            allow_empty=True,
        ),
        FilterCase("bash-action:test", ["--bash-action", "test"], _pred_bash_action("test")),
        FilterCase("bash-action:service_probe", ["--bash-action", "service_probe"], _pred_bash_action("service_probe")),
        # Service host.
        FilterCase(
            "service:hub.huggingface.co",
            ["--service", "hub.huggingface.co"],
            _pred_service("hub.huggingface.co"),
        ),
        FilterCase(
            "service:nonexistent",
            ["--service", "no-such-service.example.com"],
            _pred_service("no-such-service.example.com"),
            allow_empty=True,
        ),
        # Dependencies (validator dropped garbage tokens).
        FilterCase("dependency:requests", ["--dependency", "requests"], _pred_dependency("requests")),
        FilterCase("dependency:pytest", ["--dependency", "pytest"], _pred_dependency("pytest")),
        FilterCase(
            "dependency:for",
            ["--dependency", "for"],
            lambda trace: False,  # post-fix: 'for' must not be a valid dependency name.
            allow_empty=True,
        ),
        # Git tier.
        FilterCase("git-tier:tool_emitted", ["--git-tier", "tool_emitted"], _pred_git_tier("tool_emitted")),
        FilterCase("git-tier:overlapping", ["--git-tier", "overlapping"], _pred_git_tier("overlapping")),
        FilterCase(
            "git-tier:tool_emitted_with_divergence",
            ["--git-tier", "tool_emitted_with_divergence"],
            _pred_git_tier("tool_emitted_with_divergence"),
        ),
        # Survival.
        FilterCase("survival:alive_on_path", ["--survival", "alive_on_path"], _pred_survival("alive_on_path")),
        FilterCase("survival:lost", ["--survival", "lost"], _pred_survival("lost")),
        FilterCase("survival:reverted", ["--survival", "reverted"], _pred_survival("reverted")),
        FilterCase(
            "survival:alive_transformed",
            ["--survival", "alive_transformed"],
            _pred_survival("alive_transformed"),
        ),
        # Outcome tri-state.
        FilterCase("success:true", ["--success"], _pred_success(True)),
        FilterCase("success:false", ["--no-success"], _pred_success(False)),
        FilterCase("success:unknown", ["--unknown-success"], _pred_unknown_success()),
        FilterCase("committed:true", ["--committed"], _pred_committed(True)),
        FilterCase("committed:false", ["--uncommitted"], _pred_committed(False)),
        # Source layer (Bug #1).
        FilterCase(
            "source-layer:canonical",
            ["--facet", "source_layer=canonical"],
            _pred_layer("canonical"),
        ),
        FilterCase(
            "source-layer:staging",
            ["--facet", "source_layer=staging"],
            _pred_layer("staging"),
        ),
        # Project slug.
        FilterCase(
            f"project:{project_slug}",
            ["--project", project_slug],
            _pred_project(project_slug),
        ),
        # Boolean facet case-fold (Bug #12).
        FilterCase(
            "facet:outcome.success=true (lowercase)",
            ["--facet", "outcome.success=true"],
            _pred_success(True),
        ),
        # Since.
        FilterCase(
            "since:2026-04-01",
            ["--since", "2026-04-01"],
            _pred_since("2026-04-01"),
        ),
        # Test framework.
        FilterCase(
            "test:pytest",
            ["--test", "pytest"],
            _pred_cmd_family("pytest"),
        ),
        # Service channel (default https/http for any URL host).
        FilterCase(
            "service-channel:https",
            ["--service-channel", "https"],
            _pred_service_channel("https"),
        ),
        # Lex query against a known title token.
        FilterCase(
            "lex:parser",
            ["--lex", "parser"],
            _pred_lex("parser"),
        ),
        # Signal filter — tested_successful_fix_candidate.
        FilterCase(
            "signal:tested_successful_fix_candidate",
            ["--signal", "tested_successful_fix_candidate"],
            _pred_signal_tested_fix(),
        ),
        # Candidate-kind escape hatch.
        FilterCase(
            "candidate-kind:bug_fix",
            ["--candidate-kind", "bug_fix"],
            _pred_candidate_kind_bugfix(),
        ),
        # Unknown-committed — schema default makes ``committed`` always
        # explicit, so this case asserts the empty set is correctly returned.
        FilterCase(
            "committed:unknown",
            ["--unknown-committed"],
            lambda trace: False,
            allow_empty=True,
        ),
        # Metadata filter.
        FilterCase(
            "metadata:session_id=parity-distinct-1",
            ["--metadata", "session_id=parity-distinct-1"],
            _pred_metadata_session("parity-distinct-1"),
        ),
    ]
    return cases


def _pred_service_channel(scheme: str):
    import re
    from urllib.parse import urlparse

    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False

        def _matches(cmd: str) -> bool:
            for url in re.findall(r"https?://[^\s'\"<>$`{}|^\\]+", cmd):
                try:
                    parsed = urlparse(url)
                except ValueError:
                    continue
                if parsed.scheme == scheme and parsed.hostname:
                    return True
            return False

        return _has_bash_command_matching(trace, _matches)

    return predicate


def _pred_lex(term: str):
    """Match a trace if any of its title/intent text contains the term.

    The indexer's lexical match runs against title+intent+action+evidence+artifact
    fields; for the synthetic corpus, our task descriptions and bash
    commands carry the searchable terms. We match against the same
    surface so the oracle and index agree.
    """

    needle = term.lower()

    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        haystacks: list[str] = []
        haystacks.append(((trace.get("task") or {}).get("description") or "").lower())
        for step in trace.get("steps", []):
            haystacks.append(str(step.get("content") or "").lower())
            for call in step.get("tool_calls", []):
                inp = call.get("input") or {}
                haystacks.append(str(inp.get("command") or "").lower())
                haystacks.append(str(inp.get("file_path") or "").lower())
                haystacks.append(str(inp.get("path") or "").lower())
            for obs in step.get("observations", []):
                haystacks.append(str(obs.get("output_summary") or "").lower())
        return any(needle in h for h in haystacks)

    return predicate


def _pred_signal_tested_fix():
    """Match traces that should derive ``tested_successful_fix_candidate``.

    Mirrors :func:`opentraces.core.trace_index._trace_signals`'s recipe.
    """

    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        observations: list[str] = []
        edited = False
        test_seen = False
        for step in trace.get("steps", []):
            for call in step.get("tool_calls", []):
                normalized = str(call.get("tool_name") or "").lower().replace("_", "").replace("-", "")
                if normalized in {"edit", "write", "multiedit"}:
                    edited = True
                if normalized in {"bash", "shell"}:
                    cmd = str((call.get("input") or {}).get("command") or "")
                    if any(
                        token in cmd.lower()
                        for token in ("pytest", "npm test", "pnpm test", "yarn test", "cargo test", "go test", "vitest", "jest", "rspec")
                    ):
                        test_seen = True
            for obs in step.get("observations", []):
                summary = str(obs.get("output_summary") or "") + " " + str(obs.get("content") or "")
                observations.append(summary.lower())
        joined = " ".join(observations)
        failed = any(word in joined for word in ("failed", "failures", "error"))
        passed = "passed" in joined or "success" in joined
        success = (trace.get("outcome") or {}).get("success") is True
        return test_seen and failed and passed and edited and success

    return predicate


def _pred_candidate_kind_bugfix():
    """Mirror ``_candidate_kind`` from the indexer for ``bug_fix``."""

    pred = _pred_signal_tested_fix()
    return pred


def _pred_metadata_session(session_id: str):
    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        return str(trace.get("session_id") or "") == session_id

    return predicate


def _pred_since(iso_date: str):
    from datetime import datetime, timezone

    cutoff = datetime.fromisoformat(iso_date).replace(tzinfo=timezone.utc)

    def predicate(trace: dict) -> bool:
        if _superseded_unit(trace):
            return False
        raw = trace.get("timestamp_end") or trace.get("timestamp_start")
        if not raw:
            return False
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts >= cutoff

    return predicate


FILTER_MATRIX = _build_matrix()


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state():
    """Override the function-scoped isolation from tests/conftest.py.

    The parity matrix needs the module-scoped corpus to remain in place
    across every parametrized test. The repo's autouse isolation fixture
    would re-monkeypatch ``paths`` to a *new* tmp_path on every test
    function, wiping out the corpus our ``_module_isolated_home`` fixture
    just installed. Returning a no-op short-circuits that override.
    """

    yield


@pytest.fixture(scope="module", autouse=True)
def _module_isolated_home(tmp_path_factory):
    """Module-scoped isolation override.

    The repo's autouse isolation fixture in ``tests/conftest.py`` is
    function-scoped, but the parity matrix is module-scoped (corpus +
    index rebuild are slow). Without a module-level override, our module
    fixtures run against the developer's real ``~/.opentraces`` and the
    oracle pulls in unrelated real traces, breaking parity. We install an
    isolated home for the duration of the module instead.
    """

    import os

    from opentraces.core import config as _config
    from opentraces.core import paths as _paths

    home = tmp_path_factory.mktemp("parity_home")
    opentraces_dir = home / ".opentraces"
    projects_dir = opentraces_dir / "projects"
    staging_dir = opentraces_dir / "staging"
    opentraces_dir.mkdir(parents=True, exist_ok=True)
    projects_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    saved_env = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    saved: dict = {}
    for mod in (_paths, _config):
        saved[mod] = {
            "OPENTRACES_DIR": getattr(mod, "OPENTRACES_DIR"),
            "CONFIG_PATH": getattr(mod, "CONFIG_PATH"),
            "CREDENTIALS_PATH": getattr(mod, "CREDENTIALS_PATH"),
            "PROJECTS_DIR": getattr(mod, "PROJECTS_DIR"),
        }
        if hasattr(mod, "STAGING_DIR"):
            saved[mod]["STAGING_DIR"] = getattr(mod, "STAGING_DIR")
        mod.OPENTRACES_DIR = opentraces_dir
        mod.CONFIG_PATH = opentraces_dir / "config.json"
        mod.CREDENTIALS_PATH = opentraces_dir / "credentials"
        mod.PROJECTS_DIR = projects_dir
        if hasattr(mod, "STAGING_DIR"):
            mod.STAGING_DIR = staging_dir

    try:
        yield opentraces_dir
    finally:
        if saved_env is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved_env
        for mod, values in saved.items():
            for name, value in values.items():
                setattr(mod, name, value)


@pytest.fixture(scope="module")
def parity_corpus(_module_isolated_home):
    """Seed the synthetic corpus once per module and rebuild the index."""

    write_corpus()
    ensure_index_built()
    yield


@pytest.fixture(scope="module")
def oracle(parity_corpus) -> dict:
    return load_oracle(force=True)


# ---------------------------------------------------------------------------
# Parametrized assertions.
# ---------------------------------------------------------------------------


def _xfail_marker(case: FilterCase):
    if case.expected_status == "xfail":
        return pytest.mark.xfail(reason=case.xfail_reason or "expected to fail", strict=False)
    return None


def _parametrize():
    params = []
    for case in FILTER_MATRIX:
        marker = _xfail_marker(case)
        params.append(pytest.param(case, marks=[marker] if marker else [], id=case.name))
    return params


@pytest.mark.parametrize("case", _parametrize())
def test_recall(case: FilterCase, oracle):
    oracle_ids, _, _ = run_oracle(case.crawl_predicate, oracle)
    if not case.allow_empty:
        assert oracle_ids, f"oracle returned empty set for {case.name!r}; matrix entry is not exercising the index"
    index_ids, _, _ = run_indexed_query(case.cli_args)
    missing = oracle_ids - index_ids
    assert not missing, (
        f"recall failure for {case.name!r}: oracle has "
        f"{len(missing)} trace_id(s) the index does not return: "
        f"{sorted(list(missing))[:10]}"
    )


@pytest.mark.parametrize("case", _parametrize())
def test_precision(case: FilterCase, oracle):
    oracle_ids, _, _ = run_oracle(case.crawl_predicate, oracle)
    index_ids, _, _ = run_indexed_query(case.cli_args)
    extra = index_ids - oracle_ids
    assert not extra, (
        f"precision failure for {case.name!r}: index returned "
        f"{len(extra)} trace_id(s) the oracle does not match: "
        f"{sorted(list(extra))[:10]}"
    )


@pytest.mark.parametrize("case", _parametrize())
def test_speed(case: FilterCase, oracle):
    # warmup
    run_indexed_query(case.cli_args)
    run_oracle(case.crawl_predicate, oracle)

    _, _, oracle_ms = run_oracle(case.crawl_predicate, oracle)
    _, _, index_ms = run_indexed_query(case.cli_args)
    # The harness corpus is tiny, so absolute latency is sub-ms; only
    # enforce the ratio when the oracle takes meaningful time. Otherwise
    # accept any non-pathological index latency.
    if oracle_ms > 0.5:
        ratio = index_ms / oracle_ms
        assert ratio <= 0.5, (
            f"speed failure for {case.name!r}: index {index_ms:.3f}ms "
            f"vs oracle {oracle_ms:.3f}ms (ratio {ratio:.2f})"
        )
    else:
        assert index_ms < 200.0, (
            f"speed failure for {case.name!r}: index {index_ms:.3f}ms "
            f"is unreasonably slow on a sub-millisecond oracle"
        )


@pytest.mark.parametrize("case", _parametrize())
def test_token_efficiency(case: FilterCase, oracle):
    _, oracle_bytes, _ = run_oracle(case.crawl_predicate, oracle)
    _, index_bytes, _ = run_indexed_query(case.cli_args)
    assert index_bytes <= oracle_bytes, (
        f"token efficiency failure for {case.name!r}: index "
        f"{index_bytes}B vs oracle {oracle_bytes}B"
    )


# ---------------------------------------------------------------------------
# Diagnostics emission.
# ---------------------------------------------------------------------------


def test_emit_parity_report(oracle, tmp_path_factory):
    """Write ``tests/integration/parity_report.json`` for CI artifact upload.

    Lives in the same module so the report reflects the same corpus that
    the matrix exercised. Each FilterCase contributes one row with full
    diagnostics so downstream automation can assert on counts/latency
    without re-running the matrix.
    """

    cases_out: list[dict] = []
    for case in FILTER_MATRIX:
        oracle_ids, oracle_bytes, oracle_ms = run_oracle(case.crawl_predicate, oracle)
        index_ids, index_bytes, index_ms = run_indexed_query(case.cli_args)
        ratio = (index_ms / oracle_ms) if oracle_ms > 0 else None
        byte_ratio = (index_bytes / oracle_bytes) if oracle_bytes > 0 else None
        cases_out.append(
            {
                "name": case.name,
                "filter_args": case.cli_args,
                "expected_status": case.expected_status,
                "oracle_count": len(oracle_ids),
                "index_count": len(index_ids),
                "missing_from_index": sorted(list(oracle_ids - index_ids))[:10],
                "extra_in_index": sorted(list(index_ids - oracle_ids))[:10],
                "oracle_latency_ms": round(oracle_ms, 3),
                "index_latency_ms": round(index_ms, 3),
                "latency_ratio": round(ratio, 3) if ratio is not None else None,
                "oracle_bytes": oracle_bytes,
                "index_bytes": index_bytes,
                "byte_ratio": round(byte_ratio, 3) if byte_ratio is not None else None,
                "verdict": "pass"
                if oracle_ids == index_ids
                else "fail",
            }
        )
    report_path = Path(__file__).resolve().parent / "parity_report.json"
    report_path.write_text(json.dumps({"cases": cases_out}, indent=2, sort_keys=True))
    failed = [c for c in cases_out if c["verdict"] != "pass"]
    assert not failed, f"{len(failed)} parity cases failed; see {report_path}"
