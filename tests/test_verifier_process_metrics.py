"""Tests for the process-level verifier layer (br/69): PR-space metrics + deterministic
citation groundedness. These are the metric correction (Exp 6) and the zero-circularity RLVR
leg the per-finding experiment rests on.
"""

from __future__ import annotations

from opentraces.consumers.verifier_factory.pr_metrics import (
    auprc, discrimination_report, mcc, prevalence,
)
from opentraces.consumers.verifier_factory.groundedness import check_finding


# --- PR-space metrics --------------------------------------------------------

def test_auprc_and_mcc_edges():
    assert auprc([1, 1, 0, 0], [0.9, 0.8, 0.1, 0.2]) == 1.0           # perfect
    assert auprc([1, 1, 1], [0.9, 0.8, 0.7]) is None                  # one class -> undefined
    assert mcc([1, 1, 0, 0], [1, 1, 0, 0]) == 1.0
    assert mcc([1, 1, 0, 0], [0, 0, 1, 1]) == -1.0
    assert mcc([1, 1, 1], [1, 1, 1]) is None                          # zero margin -> undefined


def test_discrimination_report_blocks_on_one_class():
    r = discrimination_report([1] * 10, [0.9] * 10)
    assert r.defined is False and "BLOCKED" in r.note and r.auprc is None


def test_discrimination_report_separable_is_defined_with_interval():
    r = discrimination_report([1] * 8 + [0] * 3,
                              [0.9, 0.85, 0.8, 0.82, 0.88, 0.91, 0.86, 0.84, 0.2, 0.1, 0.15],
                              n_boot=300)
    assert r.defined is True
    assert r.auprc == 1.0 and r.mcc == 1.0
    assert r.prevalence == round(8 / 11, 6)
    lo, hi = r.auprc_ci
    assert lo is not None and hi is not None and lo <= 1.0 <= hi + 1e-9


# --- deterministic citation groundedness -------------------------------------

# the ground-truth universe of real file basenames (e.g. the repo filesystem)
_PRESENT = {"server.py", "text_redaction.py", "datasets.py", "pyproject.toml"}


def test_fabricated_citation_is_caught_deterministically():
    # a finding citing a file NOT in the repo -> fabricated (a real, zero-circularity negative)
    v = check_finding({"finding_id": "f1", "claim_quote": "bug in totally_made_up_module.py:42",
                       "cited_paths": ["src/opentraces/totally_made_up_module.py"]}, _PRESENT)
    assert v.label == "fabricated_citation" and v.citation_grounded is False
    assert v.unread_citations


def test_present_citation_is_grounded_even_if_claim_is_a_misread():
    # cites a real file -> citation_present; a SEMANTIC misread is NOT caught here (correctly),
    # it is routed to ratification. The deterministic leg only catches fabricated CITATIONS.
    v = check_finding({"finding_id": "f2",
                       "claim_quote": "intent is appended raw and should be escaped",
                       "cited_paths": ["core/text_redaction.py"]}, _PRESENT)
    assert v.label == "citation_present" and v.citation_grounded is True


def test_bare_symbol_is_not_a_citation_existence_claim():
    # a bare symbol (no file extension) is not a citation-existence claim -> not flagged
    v = check_finding({"finding_id": "f3", "claim_quote": "_pick_headline_intent mishandles intent",
                       "cited_paths": ["_pick_headline_intent"]}, _PRESENT)
    assert v.label == "no_citation"


def test_no_citation_is_not_a_fabrication():
    v = check_finding({"finding_id": "f4", "claim_quote": "looks good overall", "cited_paths": []}, _PRESENT)
    assert v.label == "no_citation" and v.citation_grounded is True
