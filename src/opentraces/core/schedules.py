"""Local dataset schedule registry for Plan 57."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from opentraces_schema import DatasetSchedule

from .datasets import list_datasets, load_dataset, save_manifest

_INTERVAL_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$")
_INTERVAL_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_SCHEDULE_LOG_LINE_LIMIT = 5000

# The permanently-dead ``claude-code-headless`` executor was removed in the M1
# engine collapse (#185). A schedule serialized before the collapse still
# deserializes (the schema keeps the value readable); on read we coerce it onto
# the automated ``script`` executor so the regenerated trigger command stays
# valid instead of naming a removed executor.
_LEGACY_HEADLESS_EXECUTOR = "claude-code-headless"
_DEFAULT_EXECUTOR = "script"


def _coerce_executor(executor: str | None) -> str:
    if not executor or executor == _LEGACY_HEADLESS_EXECUTOR:
        return _DEFAULT_EXECUTOR
    return str(executor)


@dataclass(frozen=True)
class ScheduleState:
    dataset: str
    path: Path
    enabled: bool
    every: str
    executor: str
    trigger: dict[str, Any]
    last_run_status: str | None = None


def parse_interval_seconds(every: str) -> int:
    match = _INTERVAL_RE.match(every or "")
    if not match:
        raise ValueError(
            "schedule --every must look like '30s', '15m', '2h', or '1d'"
        )
    quantity = int(match.group(1))
    if quantity <= 0:
        raise ValueError("schedule --every must be greater than zero")
    return quantity * _INTERVAL_UNIT_SECONDS[match.group(2)]


def add_schedule(
    name: str,
    *,
    every: str,
    executor: str,
    approve_new: bool = False,
    publish: bool = False,
    publish_check_only: bool = False,
) -> ScheduleState:
    parse_interval_seconds(every)
    if publish and publish_check_only:
        raise ValueError("use either --publish or --publish-check-only, not both")
    if approve_new and not (publish or publish_check_only):
        raise ValueError("--approve-new requires --publish or --publish-check-only")
    dataset = load_dataset(name)
    state = _state(
        dataset.name,
        dataset.path,
        enabled=True,
        every=every,
        executor=executor,
        approve_new=approve_new,
        publish=publish,
        publish_check_only=publish_check_only,
        last_run_status=None,
    )
    _write_state(dataset.path, state)
    _write_trigger(
        dataset.path,
        name,
        executor,
        approve_new=approve_new,
        publish=publish,
        publish_check_only=publish_check_only,
    )
    _append_log(dataset.path, f"schedule added every={every} executor={executor}")
    dataset.manifest.schedule = DatasetSchedule(enabled=True, every=every, executor=executor)
    save_manifest(dataset.path, dataset.manifest)
    return state


def list_schedules() -> list[ScheduleState]:
    schedules: list[ScheduleState] = []
    for dataset in list_datasets():
        state_path = dataset.path / ".opentraces" / "schedule.yaml"
        if state_path.exists():
            schedules.append(read_schedule(dataset.name))
    return schedules


def read_schedule(name: str) -> ScheduleState:
    dataset = load_dataset(name)
    state_path = dataset.path / ".opentraces" / "schedule.yaml"
    if not state_path.exists():
        raise FileNotFoundError(f"schedule not found: {name}")
    raw = yaml.safe_load(state_path.read_text(encoding="utf-8")) or {}
    return ScheduleState(
        dataset=name,
        path=dataset.path,
        enabled=bool(raw.get("enabled")),
        every=str(raw.get("every") or ""),
        executor=_coerce_executor(raw.get("executor")),
        trigger=dict(raw.get("trigger") or {}),
        last_run_status=raw.get("last_run_status"),
    )


def pause_schedule(name: str) -> ScheduleState:
    dataset = load_dataset(name)
    state = read_schedule(name)
    updated = _state(
        name,
        dataset.path,
        enabled=False,
        every=state.every,
        executor=state.executor,
        approve_new=bool(state.trigger.get("approve_new")),
        publish=state.trigger.get("publish") == "upload",
        publish_check_only=state.trigger.get("publish") == "check_only",
        last_run_status=state.last_run_status,
    )
    _write_state(dataset.path, updated)
    trigger_path = dataset.path / ".opentraces" / "schedule.trigger"
    if trigger_path.exists():
        trigger_path.unlink()
    _append_log(dataset.path, "schedule paused")
    dataset.manifest.schedule = DatasetSchedule(
        enabled=False,
        every=state.every,
        executor=state.executor,
    )
    save_manifest(dataset.path, dataset.manifest)
    return updated


def resume_schedule(name: str) -> ScheduleState:
    dataset = load_dataset(name)
    state = read_schedule(name)
    updated = _state(
        name,
        dataset.path,
        enabled=True,
        every=state.every,
        executor=state.executor,
        approve_new=bool(state.trigger.get("approve_new")),
        publish=state.trigger.get("publish") == "upload",
        publish_check_only=state.trigger.get("publish") == "check_only",
        last_run_status=state.last_run_status,
    )
    _write_state(dataset.path, updated)
    _write_trigger(
        dataset.path,
        name,
        state.executor,
        approve_new=bool(updated.trigger.get("approve_new")),
        publish=updated.trigger.get("publish") == "upload",
        publish_check_only=updated.trigger.get("publish") == "check_only",
    )
    _append_log(dataset.path, "schedule resumed")
    dataset.manifest.schedule = DatasetSchedule(
        enabled=True,
        every=state.every,
        executor=state.executor,
    )
    save_manifest(dataset.path, dataset.manifest)
    return updated


def remove_schedule(name: str) -> ScheduleState:
    dataset = load_dataset(name)
    state = read_schedule(name)
    for relative in ("schedule.yaml", "schedule.trigger"):
        path = dataset.path / ".opentraces" / relative
        if path.exists():
            path.unlink()
    _append_log(dataset.path, "schedule removed")
    dataset.manifest.schedule = None
    save_manifest(dataset.path, dataset.manifest)
    return state


def read_schedule_logs(name: str, *, tail: bool = False) -> list[str]:
    dataset = load_dataset(name)
    log_path = dataset.path / ".opentraces" / "schedule.log"
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()
    return lines[-50:] if tail else lines


def _state(
    name: str,
    root: Path,
    *,
    enabled: bool,
    every: str,
    executor: str,
    approve_new: bool = False,
    publish: bool = False,
    publish_check_only: bool = False,
    last_run_status: str | None = None,
) -> ScheduleState:
    publish_mode = "upload" if publish else "check_only" if publish_check_only else "none"
    return ScheduleState(
        dataset=name,
        path=root,
        enabled=enabled,
        every=every,
        executor=executor,
        trigger={
            "backend": "local-file",
            "path": str(root / ".opentraces" / "schedule.trigger"),
            "approve_new": approve_new,
            "publish": publish_mode,
        },
        last_run_status=last_run_status,
    )


def _write_state(root: Path, state: ScheduleState) -> None:
    payload = {
        "dataset": state.dataset,
        "enabled": state.enabled,
        "every": state.every,
        "executor": state.executor,
        "trigger": state.trigger,
        "last_run_status": state.last_run_status,
    }
    (root / ".opentraces" / "schedule.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )


def _write_trigger(
    root: Path,
    name: str,
    executor: str,
    *,
    approve_new: bool = False,
    publish: bool = False,
    publish_check_only: bool = False,
) -> None:
    trigger_path = root / ".opentraces" / "schedule.trigger"
    parts = ["opentraces", "dataset", "run", name, "--scheduled", "--executor", executor]
    if approve_new:
        parts.append("--approve-new")
    if publish:
        parts.append("--publish")
    elif publish_check_only:
        parts.append("--publish-check-only")
    trigger_path.write_text(
        " ".join(parts) + "\n",
        encoding="utf-8",
    )


def _append_log(root: Path, message: str) -> None:
    log_path = root / ".opentraces" / "schedule.log"
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(message + "\n")
    _rotate_log(log_path)


def _rotate_log(log_path: Path) -> None:
    if not log_path.exists():
        return
    lines = log_path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= _SCHEDULE_LOG_LINE_LIMIT:
        return
    trimmed = lines[-_SCHEDULE_LOG_LINE_LIMIT:]
    log_path.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
