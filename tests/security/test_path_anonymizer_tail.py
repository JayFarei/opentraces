"""Unit tests for the tail-consuming per-string path anonymizer (#143 move 1).

``anonymize_paths(text, consume_tail=True)`` rewrites the ENTIRE
``/Users|home/<name>/<tail-until-delimiter>`` run to a single hashed token, on
EMBEDDED substrings — not just the username segment. The default (consume_tail
False) keeps the existing username-only behavior so the TraceRecord ``apply``
path (and existing trace digests / goldens) are unchanged.
"""

from __future__ import annotations

import pytest

from opentraces.security import SECURITY_VERSION
from opentraces.security.anonymizer import anonymize_paths
from opentraces.security.tools.path_anonymizer_tool import PathAnonymizerTransformer


def test_security_version_bumped_to_0_8_0():
    """New companion detection logic + regex change bump SECURITY_VERSION."""
    assert SECURITY_VERSION == "0.8.0"


@pytest.mark.parametrize(
    "text,tail",
    [
        ("/Users/SENTINEL5/secret/.env", "/secret/.env"),
        ("/Users/SENTINEL6/work/acme", "/work/acme"),
        ('path = "/Users/SENTINEL8/.ssh/id_rsa"', "/.ssh/id_rsa"),
        ("It is written to /Users/victim/secret/.env on the box.", "/secret/.env"),
        ("/home/bob/proj/keys/private.pem", "/proj/keys/private.pem"),
    ],
)
def test_consume_tail_drops_the_path_tail(text, tail):
    out = anonymize_paths(text, username=None, consume_tail=True)
    assert tail not in out
    # The hashed marker survives; the tail structure does not.
    assert "[ot-user-" in out


@pytest.mark.parametrize(
    "text,tail,pre_hash_prefix",
    [
        ("/Users/jane.doe/secret/.env", "/secret/.env", "/Users/jane.doe/"),
        ("/home/j/work/acme", "/work/acme", "/home/j/"),
        ("cwd is /Users/a.b-c_d/proj/keys/private.pem now", "/proj/keys/private.pem", "/Users/a.b-c_d/"),
        ('path = "/home/x/.ssh/id_rsa"', "/.ssh/id_rsa", "/home/x/"),
    ],
)
def test_consume_tail_handles_dotted_and_short_usernames(text, tail, pre_hash_prefix):
    """H6 (#143): auto-detection (``extract_usernames_from_paths``) misses dotted
    (``jane.doe``) and short (``j``) usernames, so their home-path tail survived
    on the companion path even with ``consume_tail=True``. The rewrite must be
    STRUCTURAL — any ``/Users|home/<name>/<tail>`` collapses to the single hashed
    token regardless of whether ``<name>`` was auto-detected."""
    out = anonymize_paths(text, username="operator", consume_tail=True)
    assert tail not in out
    assert "[ot-user-" in out
    # The raw home-path prefix (username segment) is gone.
    assert pre_hash_prefix not in out


def test_consume_tail_dotted_username_is_idempotent():
    once = anonymize_paths("/Users/jane.doe/secret/.env", username="operator", consume_tail=True)
    twice = anonymize_paths(once, username="operator", consume_tail=True)
    assert twice == once
    assert "/secret/.env" not in once
    assert "[ot-user-" in once


def test_dotted_username_tracerecord_path_unchanged():
    """H6 stays companion-only: the TraceRecord ``apply`` path
    (``consume_tail=False``) does NOT structurally consume an undetected dotted
    username's path — it survives verbatim, so trace digests/goldens are
    unchanged. The widening is for the companion/consume_tail path only."""
    out = anonymize_paths("/Users/jane.doe/secret/.env", username="operator")
    assert out == "/Users/jane.doe/secret/.env"


def test_default_keeps_the_tail_unchanged_behavior():
    """The TraceRecord path (default consume_tail=False) is UNCHANGED — the
    username is hashed but the tail is preserved (no digest churn)."""
    out = anonymize_paths("/Users/victim/secret/.env", username=None)
    assert "/secret/.env" in out           # tail preserved
    assert "[ot-user-" in out              # username still hashed
    assert "victim" not in out


def test_token_is_hashed_form_not_user_flatten():
    """The drift-retirement proof: the replacement is the stable hashed
    ``[ot-user-<hash>]`` token, NOT the old ``_scrub_identity`` ``<user>`` flatten."""
    out = anonymize_paths("/Users/victim/secret/.env", username=None, consume_tail=True)
    assert "<user>" not in out
    assert "/Users/[ot-user-" in out


def test_consume_tail_is_idempotent():
    once = anonymize_paths("/Users/victim/secret/.env", username=None, consume_tail=True)
    twice = anonymize_paths(once, username=None, consume_tail=True)
    assert twice == once


def test_embedded_in_code_stops_at_quote():
    out = anonymize_paths('cfg = {"key": "/Users/victim/.ssh/id_rsa"}', username=None, consume_tail=True)
    assert "/.ssh/id_rsa" not in out
    assert out.endswith('"}')  # closing quote/brace survive; only the path tail is consumed


def test_tool_transform_text_entrypoint():
    """The per-string entry point returns TEXT (transform-in-place), consuming
    the tail by default for companion callers."""
    tool = PathAnonymizerTransformer()
    out = tool.transform_text("/Users/victim/secret/.env")
    assert "/secret/.env" not in out
    assert "[ot-user-" in out
