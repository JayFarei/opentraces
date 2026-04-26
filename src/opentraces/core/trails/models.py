"""Local Trace Trails models.

These models describe the local event log under
``refs/opentraces/local/events/v1``. They are intentionally not part of
``opentraces-schema`` yet because Phase 1 TrailEvents are a local replay
contract, not a published dataset row.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from opentraces_schema import SCHEMA_VERSION

from ...security.version import SECURITY_VERSION

ATTRIBUTION_VERSION = "0.1.0"
EVENT_ID_PREFIX = "trailevent-sha256:"
HASH_PREFIX = "sha256:"
GIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")
GIT_OBJECT_KEYS = {
    "blob_id",
    "tree_id",
    "commit_id",
    "git_head",
    "git_head_id",
    "before_blob_id",
    "after_blob_id",
    "parent_id",
    "base_tree_id",
}
BARE_GIT_SHA_KEYS = {
    "blob_sha",
    "tree_sha",
    "commit_sha",
    "before_blob_sha",
    "after_blob_sha",
    "parent_sha",
}


def canonical_json(data: Any) -> str:
    """Stable JSON representation used for local content addresses."""
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def sha256_hex(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def sha256_text(text: str) -> str:
    return f"{HASH_PREFIX}{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


class GitObjectID(BaseModel):
    """Git object identity with explicit hash algorithm."""

    algo: str = "sha1"
    hex: str

    @field_validator("algo")
    @classmethod
    def _valid_algo(cls, value: str) -> str:
        if value not in {"sha1", "sha256"}:
            raise ValueError("git object algo must be sha1 or sha256")
        return value

    @field_validator("hex")
    @classmethod
    def _valid_hex(cls, value: str) -> str:
        if not GIT_SHA_RE.match(value):
            raise ValueError("git object hex must be a 40 or 64 character hex string")
        return value.lower()


class TrailEventDraft(BaseModel):
    """Unsequenced event input for an append batch."""

    event_type: str
    payload: dict[str, Any]
    trace_id: str | None = None
    generation_index: int | None = 0
    step_index: int | None = None
    event_time: str | None = None
    capture_method: list[str]
    SCHEMA_VERSION: str | None = None
    SECURITY_VERSION: str | None = None
    ATTRIBUTION_VERSION: str | None = None

    @field_validator("capture_method")
    @classmethod
    def _capture_method_non_empty(cls, value: list[str]) -> list[str]:
        if not value or any(not item for item in value):
            raise ValueError("capture_method must be a required non-empty array")
        return value

    @model_validator(mode="after")
    def _payload_object_ids_are_typed(self) -> "TrailEventDraft":
        _reject_bare_git_object_ids(self.payload)
        _reject_unknown_capture_limitations(self.payload)
        return self


class TrailEvent(BaseModel):
    """Canonical Trace Trails event envelope."""

    event_id: str
    event_sequence: int = Field(ge=1)
    event_time: str
    previous_event_id: str | None = None
    trace_id: str | None = None
    generation_index: int | None = 0
    step_index: int | None = None
    batch_id: str
    writer: str
    capture_method: list[str]
    event_type: str
    payload: dict[str, Any]
    content_hash: str
    SCHEMA_VERSION: str = SCHEMA_VERSION
    SECURITY_VERSION: str = SECURITY_VERSION
    ATTRIBUTION_VERSION: str = ATTRIBUTION_VERSION

    @field_validator("capture_method")
    @classmethod
    def _capture_method_non_empty(cls, value: list[str]) -> list[str]:
        if not value or any(not item for item in value):
            raise ValueError("capture_method must be a required non-empty array")
        return value

    @model_validator(mode="after")
    def _payload_object_ids_are_typed(self) -> "TrailEvent":
        _reject_bare_git_object_ids(self.payload)
        _reject_unknown_capture_limitations(self.payload)
        return self

    def canonical_event_material(self) -> dict[str, Any]:
        """Fields that define the replay event identity.

        Transport fields such as ``batch_id``, ``writer``, and append-time
        commit metadata stay outside the content address. The sequence and
        previous-event pointer remain inside so replay order is tamper-evident.
        """
        return {
            "event_sequence": self.event_sequence,
            "previous_event_id": self.previous_event_id,
            "trace_id": self.trace_id,
            "generation_index": self.generation_index,
            "step_index": self.step_index,
            "capture_method": self.capture_method,
            "event_type": self.event_type,
            "payload": self.payload,
            "content_hash": self.content_hash,
            "SCHEMA_VERSION": self.SCHEMA_VERSION,
            "SECURITY_VERSION": self.SECURITY_VERSION,
            "ATTRIBUTION_VERSION": self.ATTRIBUTION_VERSION,
        }


def _reject_bare_git_object_ids(value: Any, key: str | None = None) -> None:
    if isinstance(value, dict):
        if key in GIT_OBJECT_KEYS:
            GitObjectID.model_validate(value)
        for child_key, child_value in value.items():
            _reject_bare_git_object_ids(child_value, str(child_key))
        return
    if isinstance(value, list):
        for item in value:
            _reject_bare_git_object_ids(item, key)
        return
    if (
        isinstance(value, str)
        and GIT_SHA_RE.match(value)
        and (key in BARE_GIT_SHA_KEYS or (key or "").endswith("_sha"))
    ):
        raise ValueError(f"{key} must use {{algo, hex}} Git object identity")


def _reject_unknown_capture_limitations(value: Any, key: str | None = None) -> None:
    if isinstance(value, dict):
        if key == "capture_limitations":
            from .capture_limitations import assert_known_capture_limitations

            if not isinstance(value, list):
                raise ValueError("capture_limitations must be an array")
            assert_known_capture_limitations(value)
        for child_key, child_value in value.items():
            _reject_unknown_capture_limitations(child_value, str(child_key))
        return
    if isinstance(value, list):
        if key == "capture_limitations":
            from .capture_limitations import assert_known_capture_limitations

            assert_known_capture_limitations(value)
            return
        for item in value:
            _reject_unknown_capture_limitations(item, key)


def payload_content_hash(payload: dict[str, Any]) -> str:
    return f"{HASH_PREFIX}{sha256_hex(payload)}"


def event_id_for(event_material: dict[str, Any]) -> str:
    return f"{EVENT_ID_PREFIX}{sha256_hex(event_material)}"


def finalize_event(data: dict[str, Any]) -> TrailEvent:
    data = dict(data)
    data["content_hash"] = payload_content_hash(data["payload"])
    provisional = TrailEvent.model_validate({**data, "event_id": EVENT_ID_PREFIX + "0" * 64})
    data["event_id"] = event_id_for(provisional.canonical_event_material())
    return TrailEvent.model_validate(data)


def expected_event_id(event: TrailEvent) -> str:
    return event_id_for(event.canonical_event_material())
