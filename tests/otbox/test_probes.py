"""Runtime probe contract (otbox 2.0 phase 4)."""

from __future__ import annotations

from .journey import _checkpoint_satisfies
from .probes import PROBES, TRUST_CRITICAL_SURVIVAL, run_probes


class _FakeDriver:
    def __init__(self, responses):
        self._responses = responses

    def cli_argv(self, box):
        return ["ot"]

    def exec(self, box, argv):
        class R:
            pass

        r = R()
        key = " ".join(argv)
        rc, out = self._responses(key)
        r.returncode = rc
        r.stdout = out
        r.stderr = ""
        return r


def test_probe_registry_keys_are_precondition_vocab():
    from .catalogue_lint import PRECONDITION_VOCAB

    assert set(PROBES) <= PRECONDITION_VOCAB


def test_survival_probe_only_gates_trust_critical_states():
    """alive_on_path / unknown legitimately show zero search rows
    pre-maturation; gating them would conflate the maturation lifecycle
    with a lying world. reverted/lost stay strict."""
    assert "reverted" in TRUST_CRITICAL_SURVIVAL
    assert "lost" in TRUST_CRITICAL_SURVIVAL
    assert "alive_on_path" not in TRUST_CRITICAL_SURVIVAL
    assert "unknown" not in TRUST_CRITICAL_SURVIVAL

    driver = _FakeDriver(lambda key: (0, '{"result_count": 0}'))
    out = run_probes(driver, object(), {"requires_survival_states": ["alive_on_path"]})
    assert out == [("requires_survival_states", True,
                    "no trust-critical survival states requested")]

    out = run_probes(driver, object(), {"requires_survival_states": ["reverted"]})
    (key, ok, message), = out
    assert not ok and "zero matching trails" in message


def test_crashing_probe_is_a_failed_probe():
    def boom(key):
        raise RuntimeError("driver exploded")

    driver = _FakeDriver(boom)
    out = run_probes(driver, object(), {"requires_survival_states": ["reverted"]})
    (key, ok, message), = out
    assert not ok and "probe error" in message


def test_static_resolver_still_applies_for_unprobed_keys():
    """Probes complement the static provides check, they don't replace it."""
    ok, _ = _checkpoint_satisfies({"skills": ["x"]}, {"requires_skills": ["x"]})
    assert ok
    ok, reason = _checkpoint_satisfies({"skills": []}, {"requires_skills": ["x"]})
    assert not ok and "missing skills" in reason


# -- issue #42: new resolver branches ---------------------------------------------

def test_resolver_boolean_world_flags():
    """Each issue-#42 boolean flag gates on the hosting checkpoint
    advertising the same flag True."""
    for flag in (
        "bucket_spine_v2_layout",
        "pushed_to_fake_remote",
        "git_repo_present",
        "post_commit_hook_installed",
        "events_mirror_v1_populated",
    ):
        ok, _ = _checkpoint_satisfies({flag: True}, {flag: True})
        assert ok, f"{flag} should satisfy when provided True"
        ok, reason = _checkpoint_satisfies({}, {flag: True})
        assert not ok and "is not True" in reason, f"{flag} should fail when absent"


def test_resolver_branch_types_requirement():
    ok, _ = _checkpoint_satisfies(
        {"branch_types": ["main", "subagent", "rewind"]},
        {"requires_branch_types": ["subagent", "rewind"]},
    )
    assert ok
    ok, reason = _checkpoint_satisfies(
        {"branch_types": ["main"]},
        {"requires_branch_types": ["subagent"]},
    )
    assert not ok and "missing branch_types" in reason


def test_resolver_integer_minimums():
    # compactions
    assert _checkpoint_satisfies(
        {"compactions": 2}, {"requires_compactions_min": 1}
    )[0]
    assert not _checkpoint_satisfies(
        {"compactions": 0}, {"requires_compactions_min": 1}
    )[0]
    # messages-layer bytes
    assert _checkpoint_satisfies(
        {"messages_layer_bytes_max": 70000},
        {"requires_messages_layer_bytes_min": 65536},
    )[0]
    assert not _checkpoint_satisfies(
        {"messages_layer_bytes_max": 1024},
        {"requires_messages_layer_bytes_min": 65536},
    )[0]
    # edit commits
    assert _checkpoint_satisfies(
        {"edit_commits": 3}, {"requires_edit_commits_min": 3}
    )[0]
    assert not _checkpoint_satisfies(
        {"edit_commits": 1}, {"requires_edit_commits_min": 3}
    )[0]


def test_new_probes_no_op_when_not_requested():
    """A falsy precondition value short-circuits each filesystem probe to
    True without touching the box (so a journey that does not need the
    flag is never gated by it)."""
    driver = _FakeDriver(lambda key: (1, ""))
    for key in (
        "git_repo_present",
        "post_commit_hook_installed",
        "bucket_spine_v2_layout",
        "events_mirror_v1_populated",
    ):
        out = run_probes(driver, object(), {key: False})
        (_k, ok, _msg), = out
        assert ok, f"{key} should no-op to True when requested falsy"
