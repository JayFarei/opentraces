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

0.5.0 → 0.6.0
    Plan 090 (usage-episode capsules) adds two registry tools:
    ``business_logic`` (a Detector promoting the classifier's internal-hostname /
    internal-url / db-connection-string / aws-account-id heuristics to redactable
    spans — joins the capsule REDACTION_FLOOR) and ``capsule_scope`` (a Transformer
    applying field-path EXCLUSION, the "this never leaves" guarantee for
    prompt-bearing fields). The classifier verdict ``flags`` payload no longer
    carries ``matched_text``/``reason`` — only ``{pattern, severity}``.

0.6.0 → 0.7.0
    Issue #84 (non-overridable dataset reader floor). ``sanitize_dataset_row``
    now ALWAYS unions in :data:`opentraces.security.dataset_rows.DATASET_ROW_FLOOR`
    (``regex``, ``entropy``, ``business_logic``, ``path_anonymizer``) as the last
    resolution step — a workflow author's security contract may only ADD tools,
    and the default ``tier="off"`` no longer ships rows verbatim. The per-row
    ``DatasetRowSecurity`` sidecar grows ``requested_tools`` / ``effective_tools``
    / ``floor`` / ``floor_satisfied`` (author-declared vs floor-resolved), a
    metadata-shape change. ``anonymize_paths`` auto-detection now skips
    already-hashed (8-hex) username segments so the always-on path floor is
    idempotent.
"""

SECURITY_VERSION = "0.7.0"
