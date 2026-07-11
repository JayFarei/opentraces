"""Reconstruct per-step OTel session state from raw API body files.

Claude Code's ``OTEL_LOG_RAW_API_BODIES=file:<dir>`` drops one
``<client-uuid>.request.json`` per llm-request and one
``req_<request-id>.response.json`` per response into the raw-bodies dir. The
two filenames do NOT share a stem (the request is named by a client UUID, the
response by the Anthropic request id), which is why the filename-stem pairing
in ``raw_body_watcher`` never matched (issue #158, B1).

This module pairs them by *content/metadata* instead, which also lets us flush
the ~15 GB of already-captured sessions retroactively, with no live receiver:

* Requests are grouped into a session by ``metadata.user_id.session_id``.
* Steps are ordered by request-file mtime (the conversation grows
  monotonically; verified empirically on a 545-step session).
* Each request is paired to the response it *produced* via the
  ``diagnostics.previous_message_id`` chain: request ``r[k+1]`` records the id
  of the response produced by ``r[k]``, so ``r[k]``'s response is the file
  whose ``id`` equals ``r[k+1].previous_message_id``. Response files carry the
  per-step ``usage`` tokens; pairing is best-effort (a step with no resolvable
  response still yields full request-side context, just no usage).

Each reconstructed step carries everything the per-step layer/node builder
needs: the full request body (system / tools / messages the model saw) and,
when paired, the response body (usage / output).
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("opentraces.otlp.reconstruct")

# metadata.user_id is a JSON *string* embedded in the request body; the
# session id appears as the escaped substring  session_id\":\"<uuid>  . Tolerate
# optional whitespace around the colon (Claude Code emits compact, but other
# serializers space it) so the match is robust to formatting.
_SESSION_RE = re.compile(rb'session_id\\"\s*:\s*\\"([0-9a-fA-F-]{8,})')

# ``diagnostics.previous_message_id`` is a real (unescaped) JSON string field on
# the request body. We pull just this tiny value out of the raw bytes during the
# scan pass so the scan never has to parse (and hold) the whole wire-cumulative
# body — parsing is deferred to yield time, one body at a time. ``null`` (no
# prior message) simply does not match and yields ``None``.
_PREV_MSG_RE = re.compile(rb'"previous_message_id"\s*:\s*"([^"]+)"')


@dataclass
class ReconstructedStep:
    """One llm-request's worth of reconstructed context."""

    step_index: int
    transcript_uuid: str  # unique per step (request-file client uuid)
    request_id: str | None  # canonical Anthropic req_<id> when a response paired
    request_body: dict[str, Any]
    response_body: dict[str, Any] | None = None
    previous_message_id: str | None = None
    finish_reasons: list[str] = field(default_factory=list)

    def to_snapshot_step(self) -> dict[str, Any]:
        """Serializable per-step dict for a reconstruction snapshot."""
        return {
            "step_index": self.step_index,
            "transcript_uuid": self.transcript_uuid,
            "request_id": self.request_id,
            "request_body": self.request_body,
            "response_body": self.response_body,
            "previous_message_id": self.previous_message_id,
            "finish_reasons": self.finish_reasons,
        }


def _session_of(raw: bytes) -> str | None:
    m = _SESSION_RE.search(raw)
    return m.group(1).decode() if m else None


def _prev_message_id_of(raw: bytes) -> str | None:
    m = _PREV_MSG_RE.search(raw)
    return m.group(1).decode() if m else None


def _scan_request_files_for_session(
    raw_dir: Path, session_id: str
) -> list[tuple[float, Path, str | None]]:
    """Return lightweight ``(mtime, path, previous_message_id)`` per session request.

    Streaming pass (issue #252): only the tiny ``previous_message_id`` (needed for
    the response-index chain) is pulled from each file's bytes via regex — the
    full wire-cumulative body is NEVER parsed or held here. Parsing is deferred to
    :meth:`_ReconstructedSteps.__getitem__`, so peak memory over the scan is
    ``O(one file's bytes)``, not ``O(sum of all bodies)``.
    """
    out: list[tuple[float, Path, str | None]] = []
    with os.scandir(raw_dir) as it:
        for entry in it:
            name = entry.name
            if not name.endswith(".request.json"):
                continue
            try:
                raw = Path(entry.path).read_bytes()
            except OSError:
                continue
            if _session_of(raw) != session_id:
                continue
            prev_msg = _prev_message_id_of(raw)
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                mtime = 0.0
            out.append((mtime, Path(entry.path), prev_msg))
            del raw
    out.sort(key=lambda t: (t[0], t[1].name))
    return out


def _request_id_from_response_path(path: str | Path) -> str | None:
    name = Path(path).name
    if name.startswith("req_") and name.endswith(".response.json"):
        return name[: -len(".response.json")]
    return None


def _build_response_index(
    raw_dir: Path, needed_ids: set[str]
) -> dict[str, dict[str, Any]]:
    """Index response files for the needed message ids.

    Returns ``{message_id: {"body": <response body>, "request_id": req_<id>}}``.
    Response files are tiny (~1-2 KB). We scan newest-first and stop once every
    needed id is found, so a recent session resolves without walking the whole
    (tens-of-thousands) response corpus.
    """
    if not needed_ids:
        return {}
    remaining = set(needed_ids)
    found: dict[str, dict[str, Any]] = {}
    entries: list[tuple[float, str]] = []
    with os.scandir(raw_dir) as it:
        for entry in it:
            if not entry.name.endswith(".response.json"):
                continue
            try:
                entries.append((entry.stat().st_mtime, entry.path))
            except OSError:
                continue
    entries.sort(reverse=True)  # newest first
    for _mtime, path in entries:
        if not remaining:
            break
        try:
            body = json.loads(Path(path).read_bytes())
        except (OSError, json.JSONDecodeError):
            continue
        rid = body.get("id")
        if rid in remaining:
            found[rid] = {
                "body": body,
                "request_id": _request_id_from_response_path(path),
            }
            remaining.discard(rid)
    return found


class _ReconstructedSteps(Sequence):
    """Lazy, indexable sequence of :class:`ReconstructedStep` (issue #252).

    Holds only lightweight per-step metadata (mtime/path/previous_message_id) plus
    the tiny paired response bodies. The heavy wire-cumulative *request* body is
    read+parsed on demand in :meth:`__getitem__` and never retained, so iterating
    the whole session peaks at ``O(one body)`` instead of ``O(sum of all bodies)``.

    It is a real ``Sequence`` (supports ``len()`` and indexing) so existing
    callers — the CLI's ``[s.to_snapshot_step() for s in recon]`` and the
    ``steps[k].request_body`` unit tests — keep working unchanged. To actually
    *stream*, iterate it into a generator (``(s.to_snapshot_step() for s in recon)``)
    and hand that to the builder, which drops each body after use.
    """

    def __init__(
        self,
        *,
        scanned: list[tuple[float, Path, str | None]],
        produced_ids: list[str | None],
        resp_index: dict[str, dict[str, Any]],
    ) -> None:
        self._scanned = scanned
        self._produced_ids = produced_ids
        self._resp_index = resp_index

    def __len__(self) -> int:
        return len(self._scanned)

    def __getitem__(self, index: int) -> ReconstructedStep:
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self._scanned)))]
        if index < 0:
            index += len(self._scanned)
        _mtime, path, prev_msg = self._scanned[index]
        try:
            body = json.loads(Path(path).read_bytes())
        except (OSError, json.JSONDecodeError):
            body = {}
        produced_id = self._produced_ids[index]
        matched = self._resp_index.get(produced_id) if produced_id else None
        response_body = matched["body"] if matched else None
        request_id = matched["request_id"] if matched else None
        finish_reasons: list[str] = []
        if response_body and response_body.get("stop_reason"):
            finish_reasons = [response_body["stop_reason"]]
        return ReconstructedStep(
            step_index=index,
            transcript_uuid=path.name[: -len(".request.json")],
            request_id=request_id,
            request_body=body if isinstance(body, dict) else {},
            response_body=response_body,
            previous_message_id=prev_msg,
            finish_reasons=finish_reasons,
        )


def reconstruct_steps_from_raw_bodies(
    session_id: str, raw_dir: Path
) -> Sequence[ReconstructedStep]:
    """Reconstruct ordered per-step context for one session from raw bodies.

    Pure content/metadata pairing — no live receiver, no OTel envelopes. Returns a
    lazy :class:`_ReconstructedSteps` (a real ``Sequence``, falsy/empty when the
    session has no request files in ``raw_dir``) whose request bodies are parsed on
    demand, so a whole session is never materialized at once (issue #252).
    """
    raw_dir = Path(raw_dir)
    if not raw_dir.exists():
        return _ReconstructedSteps(scanned=[], produced_ids=[], resp_index={})
    scanned = _scan_request_files_for_session(raw_dir, session_id)
    if not scanned:
        return _ReconstructedSteps(scanned=[], produced_ids=[], resp_index={})

    # produced_response_id(step k) == previous_message_id(step k+1)
    produced_ids: list[str | None] = [
        scanned[k + 1][2] if k + 1 < len(scanned) else None
        for k in range(len(scanned))
    ]
    needed_ids: set[str] = {pid for pid in produced_ids if pid}
    resp_index = _build_response_index(raw_dir, needed_ids)
    return _ReconstructedSteps(
        scanned=scanned, produced_ids=produced_ids, resp_index=resp_index
    )
