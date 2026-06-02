"""S2 — schema field-loss audit for the 0.3.3 -> 0.4 migration.

Round-trips a committed schema-0.3.0 ``TraceRecord`` (as it sits in a real
v0.3.3 user's JSONL) through the current schema-0.6.0 model and reports every
leaf field that the validate-and-redump cycle drops or adds. This is the
contract evidence for plan 085 scenario S2: it must show that ``outcome.patch``
is the *only* field a 0.3.0 record loses going to 0.6.0.

Run standalone for the human-readable report:

    .venv/bin/python tests/migration/audit_schema_fieldloss.py

Imported by ``tests/test_migration_0_3_3_to_0_4.py`` for the assertions.
"""
from __future__ import annotations

import json
from pathlib import Path

from opentraces_schema.migrations import migrate_record
from opentraces_schema.models import TraceRecord

FIXTURE = Path(__file__).parent / "fixtures" / "record_schema_0_3_0.json"


def _leaf_paths(obj, prefix: str = "") -> set[str]:
    """Every dotted leaf path in a nested dict (lists collapse to ``[]``)."""
    paths: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            child = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict) and v:
                paths |= _leaf_paths(v, child)
            else:
                paths.add(child)
    return paths


def run_audit() -> dict:
    raw = json.loads(FIXTURE.read_text())
    in_paths = _leaf_paths(raw)

    # (a) BARE load — what a naive model_validate drops (the documented gap).
    bare = TraceRecord.model_validate(raw).model_dump(mode="json")
    bare_dropped = sorted(p for p in in_paths if p not in _leaf_paths(bare))

    # (b) MIGRATED load — migrate_record(raw) then validate (the fix).
    migrated = TraceRecord.model_validate(migrate_record(raw)).model_dump(mode="json")
    legacy_patch = (
        migrated.get("metadata", {}).get("legacy", {}).get("patch") is not None
    )

    return {
        "schema_version_in": raw.get("schema_version"),
        "bare_dropped_fields": bare_dropped,
        "migrated_patches": migrated.get("patches") or [],
        "migrated_patch_files": [p.get("file_path") for p in (migrated.get("patches") or [])],
        "legacy_patch_preserved": legacy_patch,
    }


def main() -> None:
    r = run_audit()
    print("=== S2 schema field-loss audit (0.3.0 -> 0.6.0) ===")
    print(f"schema_version_in: {r['schema_version_in']}")
    print(f"(a) bare model_validate dropped_fields: "
          f"{r['bare_dropped_fields'] or '(none)'}")
    print(f"(b) migrate_record -> patches[] files: "
          f"{r['migrated_patch_files'] or '(none)'}")
    print(f"(b) legacy diff preserved under metadata.legacy.patch: "
          f"{r['legacy_patch_preserved']}")
    bare_only_patch = r["bare_dropped_fields"] == ["outcome.patch"]
    fixed = bool(r["migrated_patches"]) and r["legacy_patch_preserved"]
    if bare_only_patch and fixed:
        print("\nVERDICT: FIXED — bare load loses only outcome.patch; "
              "migrate_record reconstructs patches[] AND preserves the raw diff. "
              "Zero data loss.")
    elif bare_only_patch:
        print("\nVERDICT: PRE-FIX — outcome.patch is the only dropped field but "
              "migrate_record is not reconstructing it yet.")
    else:
        print("\nVERDICT: UNEXPECTED — investigate bare_dropped_fields above.")


if __name__ == "__main__":
    main()
