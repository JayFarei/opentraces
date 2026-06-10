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
