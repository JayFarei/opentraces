"""Unit tests for the shared address grammar in ``cli/_address.py`` (v7 F5).

Pure-grammar coverage: the ``parse_address`` selector table and the
``root_dispatch_token`` tier-1 vs tier-2 matrix with a stubbed probe —
the real bucket is never touched here (dispatch integration lives in
``test_root_address_dispatch.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opentraces.cli._address import (
    ParsedAddress,
    is_root_dispatchable,
    parse_address,
    root_dispatch_token,
)

UUID = "3f9a1c2e-5b6d-4e7f-8a9b-0c1d2e3f4a5b"
OTHER_UUID = "3f9a1c2e-1111-4e7f-8a9b-0c1d2e3f4a5b"
LEGACY = f"claude-code_{UUID}"


# ---------------------------------------------------------------------------
# parse_address selector table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        # bare trace part, no selector
        (UUID, ParsedAddress(UUID, None)),
        ("19f1a2f4", ParsedAddress("19f1a2f4", None)),
        # integer step selector
        (f"{UUID}:3", ParsedAddress(UUID, 3)),
        (f"{UUID}:0", ParsedAddress(UUID, 0)),
        # last selector (recognized locally pre-F1)
        (f"{UUID}:last", ParsedAddress(UUID, "last")),
        # span selector
        (f"{UUID}:2-5", ParsedAddress(UUID, (2, 5))),
        # invalid / reserved selectors are NOT addresses
        (f"{UUID}:origin", None),
        (f"{UUID}:abc", None),
        (f"{UUID}:2-x", None),
        (f"{UUID}:", None),
        ("foo:bar", None),
        # empty-ish input
        ("", None),
        ("   ", None),
        (":3", None),
    ],
)
def test_parse_address_selector_table(token, expected):
    assert parse_address(token) == expected


def test_parse_address_delegates_colon_grammar_to_parse_trail_ref():
    """The tuple oracle is parse_trail_ref — same trace/step split."""
    from opentraces.core.trails.lineage import parse_trail_ref

    token = f"{UUID}:7"
    trace_id, step_index, span, reserved = parse_trail_ref(token)
    parsed = parse_address(token)
    assert parsed is not None
    assert parsed.trace_part == trace_id
    assert parsed.selector == step_index
    assert span is None and reserved is None


# ---------------------------------------------------------------------------
# root_dispatch_token — tier-1 (pure syntax, no probe)
# ---------------------------------------------------------------------------


def _forbidden_probe(cwd: Path, prefix: str):  # pragma: no cover - guard
    raise AssertionError(
        f"tier-1 token must never hit the existence probe (got {prefix!r})"
    )


@pytest.mark.parametrize(
    "token",
    [
        UUID,                                   # full 36-char UUID
        f"{UUID}:3",                            # uuid + step
        f"{UUID}:last",                         # uuid + last
        f"{UUID}:2-5",                          # uuid + span
        LEGACY,                                 # legacy <agent>_<uuid>
        "ot://trace/xyz/patches/p1/trail",      # ot:// resource
        f"t:{UUID}",                            # typed trace id
        f"tu:{UUID}#u1",                        # trace unit id
        f"tmn:{UUID}#n1",                       # map node id
    ],
)
def test_tier1_tokens_dispatch_verbatim_without_probe(token):
    assert root_dispatch_token(token, probe=_forbidden_probe) == token
    assert is_root_dispatchable(token, probe=_forbidden_probe)


@pytest.mark.parametrize(
    "token",
    [
        "stauts",              # typo — not hex, not colon
        "status",              # real command name (gate is syntax-only here)
        f"{UUID}:origin",      # reserved selector — trail's, not ours
        f"{UUID}:abc",         # malformed selector
        "foo:bar",             # colon form, left part not uuid-ish
        "deadbee",             # hex but only 7 chars — below tier-2 bar
        "-19f1a2f4",           # leading dash never enters the gate
        "",
        "Zf9a1c2e-5b6d",       # non-hex lead char
    ],
)
def test_non_addresses_never_dispatch(token):
    calls: list[str] = []

    def recording_probe(cwd: Path, prefix: str):
        calls.append(prefix)
        return None

    assert root_dispatch_token(token, probe=recording_probe) is None
    assert not is_root_dispatchable(token, probe=recording_probe)


# ---------------------------------------------------------------------------
# root_dispatch_token — tier-2 (short hex prefix + stubbed probe)
# ---------------------------------------------------------------------------


def test_tier2_bare_prefix_resolves_to_full_id():
    """The RESOLVED full trace_id is forwarded, not the raw prefix.

    trace get's bare-id path is EXACT-id resolution (sqlite equality +
    direct glob), so forwarding the prefix verbatim would dead-end at
    rc=6 — the plan-review tier-2 coherence fix.
    """
    def probe(cwd: Path, prefix: str):
        assert prefix == "3f9a1c2e"
        return UUID

    assert root_dispatch_token("3f9a1c2e", probe=probe) == UUID


def test_tier2_colon_form_goes_through_probe_and_reattaches_selector():
    def probe(cwd: Path, prefix: str):
        assert prefix == "3f9a1c2e"
        return UUID

    assert root_dispatch_token("3f9a1c2e:3", probe=probe) == f"{UUID}:3"
    assert root_dispatch_token("3f9a1c2e:last", probe=probe) == f"{UUID}:last"
    assert root_dispatch_token("3f9a1c2e:2-5", probe=probe) == f"{UUID}:2-5"


def test_tier2_unresolved_prefix_is_not_an_address():
    assert root_dispatch_token("deadbeef", probe=lambda cwd, p: None) is None
    # hex-lettered english word long enough for tier 2, no matching trace
    assert root_dispatch_token("decadeadded", probe=lambda cwd, p: None) is None


def test_tier2_ambiguous_prefix_falls_through():
    from opentraces.core.trace_meta import AmbiguousPrefixError

    def ambiguous_probe(cwd: Path, prefix: str):
        raise AmbiguousPrefixError(prefix, [UUID, OTHER_UUID])

    assert root_dispatch_token("3f9a1c2e", probe=ambiguous_probe) is None


def test_tier2_probe_crash_is_fail_safe():
    def broken_probe(cwd: Path, prefix: str):
        raise OSError("meta store unreadable")

    assert root_dispatch_token("3f9a1c2e", probe=broken_probe) is None


def test_tier2_probe_receives_cwd():
    seen: list[Path] = []

    def probe(cwd: Path, prefix: str):
        seen.append(cwd)
        return UUID

    root_dispatch_token("3f9a1c2e", cwd=Path("/tmp/somewhere"), probe=probe)
    assert seen == [Path("/tmp/somewhere")]
