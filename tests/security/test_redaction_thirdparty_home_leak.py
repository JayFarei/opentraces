"""Third-party home-path leak regression (seal-family, redaction-leak lane).

The retired ``_PATH_USER_RE`` was case-INSENSITIVE and needed NO trailing tail:
it collapsed ANY ``/Users|home/<name>`` segment, so a *non-operator* collaborator
username was scrubbed even when its path was tail-less ("cloned into
/Users/teammate") or non-canonically cased ("/users/collaborator/.env").

The #143 replacement (``extract_usernames_from_paths`` +
``_HOME_PATH_CONSUME_RE``) is case-SENSITIVE (``Users|home``) and REQUIRES a
trailing ``/tail`` — only the operator's own identity tokens get the aggressive
substitution. So a third-party home path LEAKS verbatim into a public capsule
when it is (a) tail-less or (b) a lower-cased root. These tests drive the REAL
capsule redaction path (``redact_envelope``) and assert no third-party username
survives verbatim, while the H6 dotted/short and the well-formed tailed cases
still redact.
"""

from __future__ import annotations

import json

from opentraces.core.capsule.redaction import redact_envelope


def test_third_party_home_paths_never_leak_verbatim():
    payload = {
        # (a) tail-less, canonical case — auto-detect needs a trailing '/'.
        "tailless": "cloned into /Users/teammate",
        # (b) lower-cased root — case-sensitive 'Users' misses it.
        "lowercase": "config at /users/collaborator/.env",
        # control: well-formed tailed path (must still redact via H6/auto-detect).
        "tailed": "/Users/thirdparty/project/x.py",
    }
    redacted, _manifest = redact_envelope(payload)
    blob = json.dumps(redacted)

    assert "teammate" not in blob, "tail-less third-party home path leaked"
    assert "collaborator" not in blob, "lower-cased third-party home path leaked"
    assert "thirdparty" not in blob, "tailed third-party home path leaked"


def test_h6_dotted_and_short_names_still_redacted():
    """The H6 fix (dotted/short username collapse) must not regress."""
    payload = {
        "dotted": "path = /Users/jane.doe/secret/.env",
        "short": "/home/j/work/acme/keys/private.pem",
    }
    redacted, _manifest = redact_envelope(payload)
    blob = json.dumps(redacted)

    assert "jane.doe" not in blob
    assert "/secret/.env" not in blob
    assert "/home/j/" not in blob
