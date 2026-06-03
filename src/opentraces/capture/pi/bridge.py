"""Python bridge for the Pi extension.

The Pi TypeScript extension stays deliberately thin: it observes Pi runtime
callbacks and forwards normalized events to this module.  The bridge validates
those envelopes, enforces local/default-off raw provider body retention, enriches
Trail boundary events with shared OpenTraces state, writes append-only sidecars,
and optionally triggers bounded ingest.  Every public entrypoint is fail-open by
default so a bridge failure never blocks Pi.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from opentraces.capture.claude_code.hooks._trails import (
    observe_tool_boundary_for_hook,
    trail_state,
)

SIDECAR_SCHEMA_VERSION = "opentraces.pi.sidecar.v1"
SIDECAR_TYPE = "opentraces_pi_event"
SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")

ALLOWED_EVENTS: frozenset[str] = frozenset(
    {
        "session_start",
        "session_shutdown",
        "agent_start",
        "agent_end",
        "active_leaf",
        "tool_pre",
        "tool_post",
        "provider_request",
        "provider_response",
        "context",
        "model_change",
        "thinking_change",
        "tree",
        "compaction",
        "user_bash",
        "setup_status",
        "pi_capture_boundary_timeout",
    }
)

RAW_BODY_KEYS: frozenset[str] = frozenset(
    {
        "raw_provider_payload",
        "raw_provider_request",
        "raw_provider_response",
        "raw_body",
        "body",
    }
)


@dataclass(frozen=True)
class BridgeResult:
    """Structured bridge outcome; serializable via :meth:`to_json`."""

    ok: bool
    status: str
    sidecar_path: str | None = None
    event_id: str | None = None
    errors: list[str] = field(default_factory=list)
    ingest_spawned: bool = False
    raw_provider_body_retained: bool = False
    recovered_sessions: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "sidecar_path": self.sidecar_path,
            "event_id": self.event_id,
            "errors": list(self.errors),
            "ingest_spawned": self.ingest_spawned,
            "raw_provider_body_retained": self.raw_provider_body_retained,
            "recovered_sessions": self.recovered_sessions,
        }


class PiBridgeError(ValueError):
    """Validation/persistence error. CLI callers can surface this as JSON."""


# ---------------------------------------------------------------------------
# Paths / IDs
# ---------------------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_name(value: Any) -> str:
    text = str(value or "unknown")
    text = SAFE_NAME.sub("_", text).strip("._")
    return text or "unknown"


def project_pi_dir(cwd: str | Path) -> Path:
    return Path(cwd).expanduser().resolve() / ".opentraces" / "pi"


def sidecar_path_for(cwd: str | Path, session_id: str) -> Path:
    return project_pi_dir(cwd) / "events" / f"{safe_name(session_id)}.jsonl"


def _blob_dir(cwd: str | Path) -> Path:
    return project_pi_dir(cwd) / "blobs"


def _sha256_json(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _event_id(envelope: dict[str, Any]) -> str:
    material = {
        "session_id": envelope.get("session_id"),
        "sequence": envelope.get("sequence"),
        "event": envelope.get("event"),
        # Timestamp is deliberately excluded: extension retries/replays of the
        # same logical event may arrive without a client timestamp, and the
        # bridge fills one in at receive time. Sequence/entry/tool/data provide
        # the stable dedupe identity.
        "entry_id": envelope.get("entry_id"),
        "tool_call_id": envelope.get("tool_call_id"),
        "data": envelope.get("data"),
    }
    return f"pi-{_sha256_json(material)[:24]}"


# ---------------------------------------------------------------------------
# Validation / normalization
# ---------------------------------------------------------------------------


def normalize_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize one Pi sidecar envelope.

    Accepts either the frozen sidecar shape or the thinner extension payload
    shape (``event`` + ``session_id`` + ``cwd`` + ``data``).  Returns a new
    dict safe to append to sidecar JSONL.
    """

    if not isinstance(payload, dict):
        raise PiBridgeError("event payload must be a JSON object")

    event = payload.get("event")
    if not isinstance(event, str) or event not in ALLOWED_EVENTS:
        raise PiBridgeError(f"unsupported Pi sidecar event: {event!r}")

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise PiBridgeError("session_id is required")

    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise PiBridgeError("cwd is required")

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise PiBridgeError("data must be an object")

    sequence = payload.get("sequence")
    if sequence is not None:
        try:
            sequence = int(sequence)
        except (TypeError, ValueError) as exc:
            raise PiBridgeError("sequence must be an integer when present") from exc

    envelope: dict[str, Any] = {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "type": SIDECAR_TYPE,
        "event": event,
        "event_id": str(payload.get("event_id") or ""),
        "sequence": sequence,
        "session_id": session_id,
        "session_file": payload.get("session_file"),
        "cwd": cwd,
        "entry_id": payload.get("entry_id") or data.get("entry_id"),
        "leaf_id": payload.get("leaf_id") or data.get("leaf_id"),
        "turn_index": payload.get("turn_index") or data.get("turn_index"),
        "tool_call_id": (
            payload.get("tool_call_id")
            or data.get("tool_call_id")
            or data.get("tool_use_id")
        ),
        "timestamp": payload.get("timestamp") or utc_now(),
        "final": bool(payload.get("final", event == "session_shutdown")),
        "data": dict(data),
    }
    if not envelope["event_id"]:
        envelope["event_id"] = _event_id(envelope)
    return envelope


def _raw_body_opt_in(envelope: dict[str, Any]) -> bool:
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    if data.get("raw_provider_bodies_opt_in") is True:
        return True
    if envelope.get("raw_provider_bodies_opt_in") is True:
        return True
    return os.environ.get("OPENTRACES_PI_RETAIN_RAW_PROVIDER_BODIES") in {"1", "true", "yes"}


def _write_json_blob(cwd: str | Path, payload: Any) -> dict[str, Any]:
    digest = _sha256_json(payload)
    blob_path = _blob_dir(cwd) / digest[:2] / f"{digest}.json"
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return {
        "sha256": digest,
        "bytes": blob_path.stat().st_size,
        "path": str(blob_path),
        "local_only": True,
        "security_gate": "opentraces_security_pipeline_required_before_remote_sync",
    }


def _enforce_raw_body_defaults(envelope: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Drop raw provider body material unless explicitly opted in."""

    data = dict(envelope.get("data") or {})
    retained = False
    body_refs: dict[str, Any] = {}
    for key in list(data):
        if key not in RAW_BODY_KEYS:
            continue
        value = data.pop(key)
        body_refs[key] = {
            "sha256": _sha256_json(value),
            "retained": False,
            "reason": "raw_provider_bodies_default_off",
        }
        if _raw_body_opt_in(envelope):
            body_refs[key] = {
                **_write_json_blob(envelope["cwd"], value),
                "retained": True,
            }
            retained = True
    if body_refs:
        data["raw_provider_body_refs"] = body_refs
        data["raw_provider_bodies_retained"] = retained
    envelope = {**envelope, "data": data}
    return envelope, retained


def _with_trail_state(envelope: dict[str, Any]) -> dict[str, Any]:
    event = envelope.get("event")
    if event not in {"tool_pre", "tool_post", "session_shutdown", "agent_end"}:
        return envelope
    data = dict(envelope.get("data") or {})
    tool_name = data.get("tool_name") or data.get("tool")
    tool_input = data.get("input") or data.get("tool_input") or {}
    if isinstance(tool_name, str) and not data.get("trail"):
        data["trail"] = trail_state(envelope.get("cwd"), tool_name, tool_input)
    if event in {"tool_pre", "tool_post"} and not data.get("trail_observer"):
        observer = observe_tool_boundary_for_hook(
            envelope.get("cwd"),
            tool_name if isinstance(tool_name, str) else None,
            envelope.get("session_file"),
            tool_input,
        )
        if observer is not None:
            data["trail_observer"] = observer
    envelope = {**envelope, "data": data}
    return envelope


def _append_sidecar(envelope: dict[str, Any]) -> tuple[Path, bool]:
    path = sidecar_path_for(envelope["cwd"], envelope["session_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    event_id = envelope.get("event_id")
    if event_id and path.exists():
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("event_id") == event_id:
                    return path, False
        except OSError:
            pass
    line = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return path, True


# ---------------------------------------------------------------------------
# Consent / ingest / recovery
# ---------------------------------------------------------------------------


def capture_enabled_for_project(cwd: str | Path) -> bool:
    """Return True only after explicit ``opentraces init --agent pi`` consent."""

    marker = Path(cwd).expanduser().resolve() / ".opentraces.json"
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return False
    agents = data.get("agents") if isinstance(data, dict) else None
    if isinstance(agents, str):
        agents = [agents]
    if not isinstance(agents, list):
        return False
    return "pi" in {str(agent).strip().lower() for agent in agents}


def _recovery_marker(cwd: str | Path, session_id: str) -> Path:
    return project_pi_dir(cwd) / "recovery" / f"{safe_name(session_id)}.json"


def _recovery_fingerprint(envelope: dict[str, Any]) -> tuple[Path, dict[str, Any]] | None:
    session_id = str(envelope.get("session_id") or "")
    session_file = envelope.get("session_file")
    cwd = envelope.get("cwd")
    if not session_id or not isinstance(session_file, str) or not isinstance(cwd, str):
        return None
    path = Path(session_file)
    try:
        stat = path.stat()
    except OSError:
        return None
    marker = _recovery_marker(cwd, session_id)
    return marker, {"session_file": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _already_recovered(envelope: dict[str, Any]) -> bool:
    item = _recovery_fingerprint(envelope)
    if item is None:
        return True
    marker, fingerprint = item
    try:
        return marker.exists() and json.loads(marker.read_text(encoding="utf-8")) == fingerprint
    except Exception:
        return False


def _mark_recovered(envelope: dict[str, Any]) -> None:
    item = _recovery_fingerprint(envelope)
    if item is None:
        return
    marker, fingerprint = item
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(fingerprint, sort_keys=True) + "\n", encoding="utf-8")


def recover_unfinalized_sessions(cwd: str | Path, *, current_session_id: str | None = None) -> int:
    """Best-effort startup recovery for sidecars that never reached shutdown."""

    if not capture_enabled_for_project(cwd):
        return 0
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in iter_sidecar_events(cwd):
        sid = row.get("session_id")
        if isinstance(sid, str) and sid:
            grouped.setdefault(sid, []).append(row)
    recovered = 0
    for session_id, rows in grouped.items():
        if current_session_id and session_id == current_session_id:
            continue
        if any(row.get("final") or row.get("event") in {"agent_end", "session_shutdown"} for row in rows):
            continue
        latest = max(rows, key=lambda row: str(row.get("timestamp") or ""))
        if not _already_recovered(latest) and spawn_ingest(latest):
            _mark_recovered(latest)
            recovered += 1
    return recovered


def spawn_ingest(envelope: dict[str, Any]) -> bool:
    """Fire-and-forget ingest for a finalized Pi session."""

    session_file = envelope.get("session_file")
    cwd = envelope.get("cwd")
    if not isinstance(session_file, str) or not session_file:
        return False
    if not isinstance(cwd, str) or not cwd:
        return False
    try:
        argv = [
            sys.executable,
            "-m",
            "opentraces",
            "_ingest-session",
            session_file,
            "--agent",
            "pi",
            "--project",
            cwd,
        ]
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            cwd=cwd,
            env={**os.environ, "OPENTRACES_PI_EXTENSION_INTERNAL": "1"},
        )
        return True
    except Exception:
        return False


def record_event(payload: dict[str, Any], *, fail_open: bool = True) -> BridgeResult:
    """Validate/write one event and return a structured result.

    With ``fail_open=True`` (the extension default), validation/write failures
    are reported as ``ok=False`` but never raised.
    """

    try:
        envelope = normalize_event(payload)
        if not capture_enabled_for_project(envelope.get("cwd")) and envelope.get("event") != "setup_status":
            return BridgeResult(
                ok=True,
                status="capture_disabled",
                event_id=str(envelope.get("event_id") or ""),
            )
        envelope = _with_trail_state(envelope)
        envelope, raw_retained = _enforce_raw_body_defaults(envelope)
        recovered = recover_unfinalized_sessions(
            envelope.get("cwd"),
            current_session_id=str(envelope.get("session_id") or "") if envelope.get("event") == "session_start" else None,
        ) if envelope.get("event") == "session_start" else 0
        path, appended = _append_sidecar(envelope)
        ingest_spawned = False
        if appended and envelope.get("event") in {"agent_end", "session_shutdown"}:
            ingest_spawned = spawn_ingest(envelope)
        return BridgeResult(
            ok=True,
            status="ok" if appended else "duplicate",
            sidecar_path=str(path),
            event_id=str(envelope.get("event_id") or ""),
            ingest_spawned=ingest_spawned,
            raw_provider_body_retained=raw_retained,
            recovered_sessions=recovered,
        )
    except Exception as exc:  # noqa: BLE001 - fail-open bridge contract
        if not fail_open:
            raise
        return BridgeResult(ok=False, status="error", errors=[f"{type(exc).__name__}: {exc}"])


def iter_sidecar_events(cwd: str | Path, session_id: str | None = None) -> Iterable[dict[str, Any]]:
    base = project_pi_dir(cwd) / "events"
    if not base.exists():
        return []
    paths = [sidecar_path_for(cwd, session_id)] if session_id else sorted(base.glob("*.jsonl"))
    out: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("schema_version") == SIDECAR_SCHEMA_VERSION:
                out.append(row)
    return out


def bridge_status(cwd: str | Path | None = None) -> dict[str, Any]:
    """Return a compact setup/capture status used by `/ot-capture-status`."""

    import shutil

    cwd_path = Path(cwd or os.getcwd()).expanduser().resolve()
    cli_path = shutil.which("opentraces") or shutil.which("ot")
    sidecar_dir = project_pi_dir(cwd_path) / "events"
    event_count = 0
    latest_event_at: str | None = None
    if sidecar_dir.exists():
        for path in sidecar_dir.glob("*.jsonl"):
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    event_count += 1
                    try:
                        ts = json.loads(line).get("timestamp")
                    except json.JSONDecodeError:
                        ts = None
                    if isinstance(ts, str) and (latest_event_at is None or ts > latest_event_at):
                        latest_event_at = ts
            except OSError:
                continue
    marker = cwd_path / ".opentraces.json"
    capture_enabled = capture_enabled_for_project(cwd_path)
    return {
        "schema_version": "opentraces.pi.bridge_status.v1",
        "cwd": str(cwd_path),
        "cli": {
            "found": cli_path is not None,
            "path": cli_path,
            "module_importable": True,
            "bridge_command": "opentraces _pi-bridge",
            "install_hints": [
                "pipx install opentraces",
                "uv tool install opentraces",
                "pip install opentraces",
            ] if cli_path is None else [],
        },
        "project": {
            "initialized": marker.exists(),
            "capture_enabled": capture_enabled,
            "marker_path": str(marker),
            "init_command": "opentraces init --agent pi",
        },
        "capture": {
            "sidecar_dir": str(sidecar_dir),
            "enabled": capture_enabled,
            "sidecar_event_count": event_count,
            "latest_event_at": latest_event_at,
            "raw_provider_bodies_default": "off",
            "raw_provider_bodies_opt_in": os.environ.get("OPENTRACES_PI_RETAIN_RAW_PROVIDER_BODIES") in {"1", "true", "yes"},
            "fail_open": True,
        },
        "checklist": [
            {"name": "opentraces_cli", "state": "ok" if cli_path else "missing"},
            {"name": "project_init", "state": "ok" if capture_enabled else "missing", "command": "opentraces init --agent pi"},
            {"name": "git_hook", "state": "needs_terminal", "command": "opentraces setup git"},
            {"name": "hf_auth", "state": "needs_terminal", "optional": True, "command": "opentraces setup auth"},
            {"name": "bucket_remote", "state": "needs_terminal", "optional": True, "command": "opentraces setup bucket"},
        ],
    }


# ---------------------------------------------------------------------------
# CLI entrypoint (``python -m opentraces.capture.pi.bridge``)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="OpenTraces Pi bridge")
    parser.add_argument("--status", action="store_true", help="print bridge status instead of recording stdin")
    parser.add_argument("--cwd", default=None, help="project cwd for --status")
    parser.add_argument("--strict", action="store_true", help="raise validation errors instead of fail-open JSON")
    args = parser.parse_args(argv)

    if args.status:
        print(json.dumps(bridge_status(args.cwd), sort_keys=True))
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001
        if args.strict:
            raise
        print(json.dumps(BridgeResult(ok=False, status="error", errors=[str(exc)]).to_json(), sort_keys=True))
        return 0
    result = record_event(payload, fail_open=not args.strict)
    print(json.dumps(result.to_json(), sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
