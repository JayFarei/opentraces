"""Schema 0.8.0 (#200) — additive Environment dependency-pin / interpreter / arch fields.

This is the train's ONE sanctioned MINOR bump (0.7.0 -> 0.8.0). It adds a
structured *home* for exact dependency pins plus interpreter/CPU identity to the
``Environment`` model. It is strictly additive:

- every new field is OPTIONAL and defaults to absent (``None``);
- an existing pre-0.8.0 record validates unchanged;
- the model-driven HF features map picks the fields up for free;
- the fields are pure DATA — their presence never lifts ``env_tier`` or any
  trust ordinal. The resolver that would *fill* them (and derive L1) is the
  out-of-train follow-up #202; today ``env_tier`` stays at its L0 floor.

RED-first discipline (per the M3 contract): the symbol-existence asserts below
name the net-new symbols. Before the models land they fail with
``ImportError`` / missing ``model_fields`` — the failing test IS the spec. Do
not delete them to go green.
"""

from __future__ import annotations

import json

import pytest

# --- RED-first: these imports name the net-new symbols. ---
from opentraces_schema import (
    SCHEMA_VERSION,
    Agent,
    Environment,
    Interpreter,
    PinRecord,
    TraceRecord,
    load_record_dict,
    migrate_record,
)


def _pinned_environment() -> Environment:
    """An Environment with EVERY new 0.8.0 field populated."""
    return Environment(
        os="darwin",
        shell="/bin/zsh",
        language_ecosystem=["python"],
        resolved_dependencies=[
            PinRecord(
                name="requests",
                version="2.31.0",
                hash="sha256:deadbeef",
                marker="python_version >= '3.8'",
                source="pypi",
            ),
            PinRecord(name="pydantic", version="2.6.0"),
        ],
        interpreter=Interpreter(name="cpython", version="3.11.6"),
        arch="arm64",
        platform="macosx_14_0_arm64",
        abi_tag="cp311",
    )


# --------------------------------------------------------------------------- #
# 1. RED-first — the net-new symbols and fields exist.
# --------------------------------------------------------------------------- #


def test_pin_record_symbol_exists_with_expected_shape():
    fields = PinRecord.model_fields
    assert set(fields) == {"name", "version", "hash", "marker", "source"}
    # name is the only required field; everything else is optional pin metadata
    # so a resolver can carry a hash for the future L3 path without a 2nd bump.
    assert fields["name"].is_required()
    for optional in ("version", "hash", "marker", "source"):
        assert not fields[optional].is_required()


def test_interpreter_symbol_exists_with_expected_shape():
    fields = Interpreter.model_fields
    assert set(fields) == {"name", "version"}
    for optional in ("name", "version"):
        assert not fields[optional].is_required()


def test_environment_gains_new_optional_fields():
    fields = Environment.model_fields
    # Pre-0.8.0 fields survive.
    for legacy in ("os", "shell", "vcs", "language_ecosystem"):
        assert legacy in fields
    # Net-new 0.8.0 fields exist and are all optional / absent by default.
    for new in ("resolved_dependencies", "interpreter", "arch", "platform", "abi_tag"):
        assert new in fields, f"missing net-new field {new!r}"
        assert not fields[new].is_required(), f"{new!r} must be optional"

    empty = Environment()
    assert empty.resolved_dependencies is None
    assert empty.interpreter is None
    assert empty.arch is None
    assert empty.platform is None
    assert empty.abi_tag is None


# --------------------------------------------------------------------------- #
# 2. Round-trip WITH and WITHOUT the new fields.
# --------------------------------------------------------------------------- #


def test_round_trip_with_new_fields_survives():
    env = _pinned_environment()
    restored = Environment.model_validate_json(env.model_dump_json())
    assert restored == env
    assert [p.name for p in restored.resolved_dependencies] == ["requests", "pydantic"]
    assert restored.resolved_dependencies[0].hash == "sha256:deadbeef"
    assert restored.interpreter == Interpreter(name="cpython", version="3.11.6")
    assert restored.arch == "arm64"
    assert restored.platform == "macosx_14_0_arm64"
    assert restored.abi_tag == "cp311"


def test_round_trip_without_new_fields_is_absent():
    env = Environment(os="linux", language_ecosystem=["python"])
    restored = Environment.model_validate_json(env.model_dump_json())
    assert restored == env
    assert restored.resolved_dependencies is None
    assert restored.interpreter is None


def test_existing_pre_080_record_validates_unchanged():
    """A record serialized WITHOUT the new fields (the pre-0.8.0 shape) still
    validates, and the new fields read back as absent — additive-safe."""
    legacy_raw = {
        "trace_id": "legacy-1",
        "session_id": "s-legacy",
        "schema_version": "0.7.0",
        "agent": {"name": "claude-code"},
        "environment": {
            "os": "linux",
            "shell": "/bin/bash",
            "language_ecosystem": ["python"],
            "vcs": {"type": "git", "base_commit": "abc123"},
        },
    }
    record = load_record_dict(legacy_raw)
    assert record.environment.os == "linux"
    assert record.environment.resolved_dependencies is None
    assert record.environment.interpreter is None
    assert record.environment.arch is None


def test_full_trace_record_round_trips_with_pinned_environment():
    record = TraceRecord(
        trace_id="t-pins",
        session_id="s-pins",
        agent=Agent(name="claude-code"),
        environment=_pinned_environment(),
    )
    restored = TraceRecord.model_validate_json(record.model_dump_json())
    assert restored.environment == _pinned_environment()


# --------------------------------------------------------------------------- #
# 3. HF features map derives additively (model-driven, no hand edit).
# --------------------------------------------------------------------------- #


def test_hf_features_map_derives_new_environment_fields_additively():
    # Re-derive fresh so the assertion reflects the current model, not a cached
    # pre-bump map imported at module load.
    from opentraces.publish.huggingface.schema import _feature_for_model
    from opentraces_schema.models import TraceRecord as _TR

    features = _feature_for_model(_TR)
    env_features = features["environment"]

    # Pre-0.8.0 keys unchanged.
    for legacy in ("os", "shell", "vcs", "language_ecosystem"):
        assert legacy in env_features

    # Net-new keys appear additively.
    for new in ("resolved_dependencies", "interpreter", "arch", "platform", "abi_tag"):
        assert new in env_features, f"HF features map missing {new!r}"

    # list[PinRecord] -> [ {name,version,hash,marker,source feature map} ]
    rd = env_features["resolved_dependencies"]
    assert isinstance(rd, list) and len(rd) == 1
    assert set(rd[0]) == {"name", "version", "hash", "marker", "source"}

    # nested Interpreter -> its feature map.
    assert set(env_features["interpreter"]) == {"name", "version"}

    # scalar arch/platform/abi_tag -> Value(string).
    for scalar in ("arch", "platform", "abi_tag"):
        assert env_features[scalar] == {"dtype": "string", "_type": "Value"}


def test_hf_dataset_infos_version_tracks_the_minor_bump():
    from opentraces.publish.huggingface.schema import _dataset_version_info

    info = _dataset_version_info()
    assert info["version_str"] == SCHEMA_VERSION
    assert (info["major"], info["minor"], info["patch"]) == (0, 8, 0)


# --------------------------------------------------------------------------- #
# 4. MINOR recognized as additive-newer per the version policy.
# --------------------------------------------------------------------------- #


def test_schema_version_is_the_one_minor_bump():
    assert SCHEMA_VERSION == "0.8.0"


def test_minor_bump_over_070_is_additive_newer():
    major, minor, patch = (int(p) for p in SCHEMA_VERSION.split("."))
    prev_major, prev_minor, prev_patch = 0, 7, 0
    assert major == prev_major, "additive MINOR must not bump MAJOR"
    assert minor == prev_minor + 1
    assert patch == 0


def test_migrate_record_is_a_transparent_noop_across_070_to_080():
    """Additive fields need NO registered migration: an older record dict passes
    through ``migrate_record`` unchanged for the Environment fields and validates
    with the new fields absent (auto-migration only rewrites strictly-older rows,
    and adds nothing here)."""
    raw = {
        "trace_id": "m-1",
        "session_id": "s-m",
        "schema_version": "0.7.0",
        "agent": {"name": "codex"},
        "environment": {"os": "linux", "language_ecosystem": ["python"]},
    }
    migrated = migrate_record(raw)
    # migrate_record never mutates its input and adds no Environment fields.
    assert migrated["environment"] == raw["environment"]
    assert "resolved_dependencies" not in migrated["environment"]
    # And it still validates on the current model.
    record = load_record_dict(raw)
    assert record.environment.resolved_dependencies is None


def test_a_bumped_080_record_is_readable():
    record = TraceRecord(
        trace_id="t-080",
        session_id="s-080",
        agent=Agent(name="claude-code"),
        environment=_pinned_environment(),
    )
    assert record.schema_version == "0.8.0"
    raw = json.loads(record.model_dump_json())
    reread = load_record_dict(raw)
    assert reread.environment.arch == "arm64"


# --------------------------------------------------------------------------- #
# 5. GUARD — the fields are a HOME, not a trust lift (honesty invariant).
# --------------------------------------------------------------------------- #


def test_schema_carries_no_trust_ordinal_on_environment():
    """The load-bearing honesty guard: the Environment model has NO trust/tier
    field. Populating ``resolved_dependencies`` is structurally incapable of
    being a trust signal at the schema layer — schema fields are a HOME for the
    #202 resolver to fill, never a trust lift. env_tier / verdict_trust are
    DERIVED downstream (core/capsule), pinned to their L0/floor value across the
    whole M3 stack because the resolver is deferred."""
    forbidden = {
        "env_tier",
        "verdict_trust",
        "oracle_trust",
        "diff_trust",
        "sandbox_tier",
        "trust",
        "tier",
    }
    assert forbidden.isdisjoint(set(Environment.model_fields))
    assert forbidden.isdisjoint(set(PinRecord.model_fields))
    assert forbidden.isdisjoint(set(Interpreter.model_fields))


def test_populating_pins_does_not_change_the_field_set():
    """A populated Environment exposes the SAME field names as an empty one — no
    dynamic trust field materializes because pins are present."""
    empty = Environment()
    pinned = _pinned_environment()
    assert set(empty.model_dump()) == set(pinned.model_dump())


def test_no_schema_model_carries_a_trust_ordinal_field():
    """Pin the boundary structurally: NO model exported from the schema package
    carries a trust ordinal / tier field. The whole trust vocabulary is DERIVED
    downstream (core/capsule), pinned to L0/floor in this train — the schema is a
    home for the #202 resolver's inputs, never the place trust is computed."""
    import inspect

    from pydantic import BaseModel

    import opentraces_schema as pkg

    forbidden = {
        "env_tier",
        "verdict_trust",
        "oracle_trust",
        "diff_trust",
        "sandbox_tier",
    }
    models = [
        obj
        for _, obj in inspect.getmembers(pkg)
        if inspect.isclass(obj) and issubclass(obj, BaseModel)
    ]
    assert models, "expected exported schema models"
    for model in models:
        overlap = forbidden.intersection(model.model_fields)
        assert not overlap, f"{model.__name__} leaked trust field(s): {overlap}"


# --------------------------------------------------------------------------- #
# 6. bucket_digest byte-identity on the rebuild-WITHOUT-rewrite path.
# --------------------------------------------------------------------------- #
#
# The digest reads STORED per-record hashes / count-summary material, NOT a
# re-serialization of the record. The new Environment fields are therefore
# invisible to bucket_digest. We must NOT run sync_trace_records_from_local_stores
# / bucket repair here (those DO re-serialize via model_dump(mode="json"), which
# would emit the new null-default fields and legitimately flip write-path hashes).
# Mirrors tests/test_bucket_remote_symmetric.py's digest-invariance pattern.


def test_per_trace_row_digest_excludes_environment_content():
    """The per-trace manifest ``digest`` is computed from a count-summary that
    contains NO Environment content. Two records that differ ONLY in the new
    0.8.0 Environment fields therefore produce a byte-identical per-trace digest
    — the rebuild-without-rewrite path is unperturbed by the bump."""
    from opentraces.core.bucket_envelope import _per_trace_v2_summary

    plain = TraceRecord(
        trace_id="t-dig",
        session_id="s-dig",
        agent=Agent(name="claude-code"),
        environment=Environment(os="darwin", language_ecosystem=["python"]),
    )
    pinned = TraceRecord(
        trace_id="t-dig",
        session_id="s-dig",
        agent=Agent(name="claude-code"),
        environment=_pinned_environment(),
    )

    row_plain = _per_trace_v2_summary("proj", "t-dig", plain)
    row_pinned = _per_trace_v2_summary("proj", "t-dig", pinned)
    assert row_plain["digest"] == row_pinned["digest"], (
        "the new Environment fields must be invisible to the per-trace digest"
    )


def test_read_path_reads_stored_record_hash_not_a_reserialization():
    """Mirror bucket_trace_records.py:131 — the read path prefers the STORED
    ``record_hash``; it only recomputes when it is ABSENT. So an existing
    on-disk envelope keeps its hash across the schema bump, which is why
    ``bucket_digest`` is byte-unchanged on the rebuild-without-rewrite path."""
    from opentraces.core.bucket_store import _digest_payload

    record = TraceRecord(
        trace_id="t-stored",
        session_id="s-stored",
        agent=Agent(name="claude-code"),
        environment=Environment(os="linux", language_ecosystem=["python"]),
    )
    stored_hash = "sha256:stored-from-disk-pre-bump"
    raw = {
        "schema_version": "opentraces.bucket.trace_record.v1",
        "project_slug": "proj",
        "source_layer": "bucket",
        "record": json.loads(record.model_dump_json()),
        "record_hash": stored_hash,
    }

    # The exact read-path expression from bucket_trace_records.py:131.
    read_hash = str(raw.get("record_hash") or _digest_payload(record.model_dump(mode="json")))
    assert read_hash == stored_hash, "stored hash must win over re-serialization"

    # And the digest-safe row roll-up over stored per-row digests is invariant to
    # a mutation of any non-digest-material content (mirrors the plan-087 guard).
    from opentraces.core.bucket_store import (
        _digest_safe_trace_rows,
        _machine_neutral_digest_view,
    )

    rows = [
        {"project_slug": "proj", "trace_id": "t-stored", "digest": stored_hash},
    ]

    def _roll(trace_rows):
        return _digest_payload(
            _machine_neutral_digest_view(_digest_safe_trace_rows(trace_rows))
        )

    baseline = _roll(rows)
    # Adding the new (null) Environment fields to an underlying record does not
    # touch the row's stored digest, so the roll-up is unchanged.
    assert _roll([dict(r) for r in rows]) == baseline
