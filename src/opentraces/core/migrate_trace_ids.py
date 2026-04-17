"""One-shot migration: legacy ``<agent>_<session_id>`` trace ids -> UUIDv4.

Pre-0.3 captures stored ``trace_id = f"claude-code_{session_id}"`` which
conflated our canonical identifier with the upstream-agent session id. The
fix (see ``core.ingest._trace_id_for``) mints a fresh UUID per trace and
keeps ``session_id`` in its own field. Existing records on disk need to be
rewritten to match.

Migration rewrites three things per legacy trace:

1. **JSONL record** — replace ``trace_id`` in the first-line JSON, preserve
   everything else.
2. **Filename** — rename ``<legacy>.jsonl`` to ``<new_uuid>.jsonl``.
3. **state.json** — rekey the ``traces`` dict and update every
   ``GenerationRecord.trace_id`` under ``sessions``.

Idempotent: skips any record whose ``trace_id`` is already a bare UUID.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import get_project_dir, get_project_traces_dir, get_project_state_path
from .state import StateManager

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _is_legacy(trace_id: str) -> bool:
    """True when ``trace_id`` carries the deprecated agent prefix."""
    return "_" in trace_id and not _UUID_RE.match(trace_id)


@dataclass
class MigrationReport:
    traces_scanned: int = 0
    traces_migrated: int = 0
    state_entries_rekeyed: int = 0
    generations_updated: int = 0
    commit_groups_updated: int = 0
    attribution_files_updated: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def migrate_project(project_dir: Path, *, dry_run: bool = False) -> MigrationReport:
    """Migrate a single project's traces dir + state.json in place."""
    report = MigrationReport()
    traces_dir = get_project_traces_dir(project_dir)
    state_path = get_project_state_path(project_dir)

    if not traces_dir.exists():
        return report

    # Build the legacy -> new id map by scanning the JSONL records first.
    id_map: dict[str, str] = {}
    for jsonl in sorted(traces_dir.glob("*.jsonl")):
        report.traces_scanned += 1
        try:
            lines = jsonl.read_text().splitlines()
            if not lines:
                continue
            record = json.loads(lines[0])
        except Exception as exc:
            report.errors.append(f"{jsonl.name}: parse error: {exc}")
            continue

        legacy_tid = record.get("trace_id") or ""
        if not _is_legacy(legacy_tid):
            continue

        new_tid = str(uuid.uuid4())
        id_map[legacy_tid] = new_tid
        report.traces_migrated += 1

        if dry_run:
            continue

        # 1. Rewrite JSONL (first line only, which is the record).
        record["trace_id"] = new_tid
        lines[0] = json.dumps(record, ensure_ascii=False)
        jsonl.write_text("\n".join(lines) + ("\n" if lines else ""))

        # 2. Rename file to match the new trace_id.
        new_path = traces_dir / f"{new_tid}.jsonl"
        jsonl.rename(new_path)

    if not id_map or dry_run:
        return report

    # Build a session_id -> new_trace_id map so we can also fix the
    # attribution cache, which historically stored Claude's session_id
    # under the field name "trace_id".
    session_to_new: dict[str, str] = {}
    for legacy_tid, new_tid in id_map.items():
        # legacy format: "claude-code_<session_id>" or "<agent>_<session_id>"
        parts = legacy_tid.split("_", 1)
        if len(parts) == 2 and parts[1]:
            session_to_new[parts[1]] = new_tid

    # 3. Rekey state.json traces + update every GenerationRecord.trace_id.
    state = StateManager(state_path=state_path)
    raw = state._state  # noqa: SLF001 — migration is an internal operation
    traces = raw.get("traces", {})
    new_traces: dict[str, dict] = {}
    for old_key, entry in traces.items():
        new_key = id_map.get(old_key, old_key)
        if new_key != old_key:
            entry = dict(entry)
            entry["trace_id"] = new_key
            # Rewrite stale file_path so downstream tools don't chase a
            # filename that no longer exists on disk.
            fp = entry.get("file_path")
            if isinstance(fp, str) and old_key in fp:
                entry["file_path"] = fp.replace(old_key, new_key)
            report.state_entries_rekeyed += 1
        new_traces[new_key] = entry
    raw["traces"] = new_traces

    for sess in raw.get("sessions", {}).values():
        for gen in sess.get("generations", []) or []:
            old_tid = gen.get("trace_id")
            if old_tid in id_map:
                gen["trace_id"] = id_map[old_tid]
                report.generations_updated += 1
            old_sup = gen.get("supersedes")
            if old_sup in id_map:
                gen["supersedes"] = id_map[old_sup]

    # 4. Rewrite commit_groups[*].trace_ids lists.
    for cg in raw.get("commit_groups", {}).values():
        tids = cg.get("trace_ids") or []
        new_list = [id_map.get(t, t) for t in tids]
        if new_list != tids:
            cg["trace_ids"] = new_list
            report.commit_groups_updated += 1

    state.save()

    # 5. Attribution cache — historically keyed by session_id under the
    # field name "trace_id". Walk every file and rewrite to our canonical
    # trace_id wherever the session matches a record we migrated.
    attributions_dir = get_project_dir(project_dir) / "attributions"
    if attributions_dir.is_dir() and session_to_new:
        for attr_file in sorted(attributions_dir.glob("*.json")):
            try:
                data = json.loads(attr_file.read_text())
            except Exception as exc:
                report.errors.append(f"{attr_file.name}: attribution parse: {exc}")
                continue
            if _rewrite_attribution(data, session_to_new):
                attr_file.write_text(json.dumps(data, indent=2))
                report.attribution_files_updated += 1

    return report


def _rewrite_attribution(data: dict, session_to_new: dict[str, str]) -> bool:
    """Walk an attribution JSON structure and rewrite any ``trace_id`` field
    whose value is a known session_id.

    Returns True when any rewrite happened so callers know to persist.
    """
    changed = False
    files = data.get("files") or {}
    for finfo in files.values():
        if not isinstance(finfo, dict):
            continue
        for ln in finfo.get("lines") or []:
            if not isinstance(ln, dict):
                continue
            tid = ln.get("trace_id")
            if isinstance(tid, str) and tid in session_to_new:
                ln["trace_id"] = session_to_new[tid]
                changed = True
    # Top-level "introducers" list (blame format).
    for intro in data.get("introducers") or []:
        if not isinstance(intro, dict):
            continue
        tid = intro.get("trace_id")
        if isinstance(tid, str) and tid in session_to_new:
            intro["trace_id"] = session_to_new[tid]
            changed = True
    # Top-level "traces" array (per-trace coverage summary).
    for tr in data.get("traces") or []:
        if not isinstance(tr, dict):
            continue
        tid = tr.get("trace_id")
        if isinstance(tid, str) and tid in session_to_new:
            tr["trace_id"] = session_to_new[tid]
            changed = True
    return changed
