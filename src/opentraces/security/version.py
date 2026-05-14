"""Security pipeline version.

Bumped independently of SCHEMA_VERSION and CLI __version__.
Tracks changes to detection logic (regex patterns, entropy thresholds,
classifier heuristics, anonymization rules) and structural pipeline
refactors that change the on-record metadata shape.

0.3.0 → 0.4.0
    Tool-registry refactor. The numbered-tier vocabulary (off/low/medium/high)
    is retired; the pipeline runs a flat ordered list of named tools (see
    ``security.tools._registry``). On-record metadata adds
    ``metadata.security.tools_applied`` and ``metadata.security.tools.<name>``.

0.4.0 → 0.5.0
    Transition layer removed. The legacy ``metadata.security.trufflehog*``
    and ``metadata.security.privacy`` keys are no longer written — readers
    use ``metadata.security.tools.<name>`` and ``tools_applied``. The
    ``scanner`` back-compat shims (``apply_redactions``, ``scan_trace_record``,
    ``two_pass_scan``, ``apply_trufflehog_redactions``) and the
    ``scanner_trufflehog`` module are gone.
"""

SECURITY_VERSION = "0.5.0"
