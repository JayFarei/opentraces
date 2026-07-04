"""C2 (#143) — companion redaction MUST fail closed on malformed / non-object lines.

The load-bearing #143/#197 redaction path (``redact_companions``) previously
appended a non-JSON line VERBATIM (its ``except ValueError`` branch) and a JSON
scalar/list line via canonical JSON VERBATIM, so a planted secret in a malformed
line or a JSON string/array line SURVIVED into the sealed mini-bucket while
``floor_satisfied`` could still read ``True``. These tests pin the fix: every
line's content is routed through the substrate sanitizer before it is emitted,
so nothing leaves the redactor un-scanned, and ``floor_satisfied`` reflects REAL
coverage.
"""

from __future__ import annotations

import gzip

from opentraces.core.capsule.companions import redact_companions

# ``sk-live-<20+>`` is caught by the widened generic ``sk-[A-Za-z0-9-]{20,}``
# regex; the ``live`` env is NOT in the dummy-token allowlist (unlike
# ``sk-test``/``sk-example``), so these are genuine planted secrets.
TOK_NONJSON = "sk-live-REDLINEAAAAAAAAAAAAAAAAAAAAAAAA"
TOK_JSONSTR = "sk-live-REDLINEBBBBBBBBBBBBBBBBBBBBBBBB"
TOK_JSONLIST = "sk-live-REDLINECCCCCCCCCCCCCCCCCCCCCCCC"
HOME_PATH = "/Users/redline/secret/creds.env"
PATH_TAIL = "/secret/creds.env"  # the security-meaningful tail-probe


def _raw_gz() -> bytes:
    lines = [
        # (a) a NON-JSON line carrying a planted token in free text.
        f"# raw deploy log: leaked {TOK_NONJSON} while pushing",
        # (b) a JSON STRING line that IS a planted token.
        f'"{TOK_JSONSTR}"',
        # (c) a JSON LIST line: a planted token AND a home-path tail.
        f'["deploy step", "{TOK_JSONLIST}", "{HOME_PATH}"]',
    ]
    body = "\n".join(lines) + "\n"
    return gzip.compress(body.encode("utf-8"), mtime=0)


def test_malformed_and_scalar_lines_are_redacted_not_passed_through():
    gz, manifest = redact_companions(_raw_gz())
    out = gzip.decompress(gz).decode("utf-8")

    # EVERY planted secret is ABSENT from the sealed bytes (the leak is closed).
    assert TOK_NONJSON not in out, "non-JSON line token leaked verbatim"
    assert TOK_JSONSTR not in out, "JSON string line token leaked verbatim"
    assert TOK_JSONLIST not in out, "JSON list line token leaked verbatim"
    assert "sk-live-REDLINE" not in out, "a planted sk- token residual survived"
    # The home-path TAIL (the security-meaningful structure) is neutralized.
    assert PATH_TAIL not in out, "home-path tail leaked from a JSON list line"

    # ``floor_satisfied`` is honest: True only because every line actually ran
    # through the sanitizer (all three processed, none passed through raw).
    assert manifest["floor_satisfied"] is True
    assert manifest["lines"] == 3
    assert "path_anonymizer" in manifest["tools_applied"]
    assert manifest["redactions_applied"] >= 3  # the three planted tokens at least


def test_redacted_nonobject_lines_are_idempotent():
    once, _ = redact_companions(_raw_gz())
    twice, _ = redact_companions(once)
    assert gzip.decompress(twice) == gzip.decompress(once)
