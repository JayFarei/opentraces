"""Deterministic Trace Trails corpus generator/checker.

The corpus is intentionally generated from the full-stack Trace Trails demo so
fixture drift tracks the same command surfaces that the integration harness
already validates. Generation always happens into a temporary directory first;
``--check`` compares that directory with the committed fixture and ``--update``
replaces the fixture with the generated output.
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


CORPUS_VERSION = "v1"
CORPUS_SCHEMA_VERSION = "opentraces.trace_trails_corpus.v1"
COMMAND_SUMMARY_SCHEMA_VERSION = "opentraces.trace_trails_corpus.commands.v1"
EVENTS_SCHEMA_VERSION = "opentraces.trace_trails_corpus.events.v1"
API_SCHEMA_VERSION = "opentraces.trace_trails_corpus.api.v1"
DATASET_SCHEMA_VERSION = "opentraces.trace_trails_corpus.dataset.v1"
SCENARIO_NAME = "full_stack_demo"

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "trace_trails_corpus" / CORPUS_VERSION
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)"
)
UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
HEX_64_RE = re.compile(r"\b[0-9a-f]{64}\b", re.IGNORECASE)
HEX_40_RE = re.compile(r"\b[0-9a-f]{40}\b", re.IGNORECASE)
TRAIL_EVENT_RE = re.compile(r"trailevent-sha256:[0-9a-f]{64}", re.IGNORECASE)
SHA256_REF_RE = re.compile(r"sha256:[0-9a-f]{64}", re.IGNORECASE)
DISPLAY_REF_RE = re.compile(r"\b(?:snap|tp|ga|t|ts):[0-9a-f]{8,64}\b", re.IGNORECASE)
MATERIALIZED_WORKTREE_RE = re.compile(r"trace-worktrees/[0-9a-f]{16}-[0-9a-f]{8}")
REPLACEMENT_KIND_PRIORITY = {
    "trace": 0,
    "trace-patch": 0,
    "git-anchor": 0,
    "snapshot": 0,
    "trail-event": 0,
    "workspace": 0,
    "batch": 1,
    "content-hash": 1,
    "git-commit": 1,
    "git-object": 1,
    "display-ref": 2,
    "materialized-worktree": 2,
    "uuid": 3,
    "hex64": 4,
}


class CorpusMismatchError(AssertionError):
    """Raised when a freshly generated corpus differs from the fixture."""


@dataclass
class StableNormalizer:
    """Normalize run-local ids while preserving relationships between fields."""

    run_root: Path
    slots: dict[str, dict[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.run_root = self.run_root.resolve()

    def normalize(self, value: Any, *, key: str | None = None) -> Any:
        if isinstance(value, dict):
            out: dict[Any, Any] = {}
            for item_key in sorted(value):
                normalized_value = self.normalize(value[item_key], key=item_key)
                normalized_key = (
                    self._normalize_string(item_key, key=None)
                    if isinstance(item_key, str)
                    else item_key
                )
                out[normalized_key] = normalized_value
            return out
        if isinstance(value, list):
            return [self.normalize(item, key=key) for item in value]
        if isinstance(value, str):
            return self._normalize_string(value, key=key)
        if isinstance(value, int | float) and key == "total_duration_s":
            return 0.0
        if isinstance(value, int) and not isinstance(value, bool) and key == "transcript_offset":
            # ``transcript_offset`` is a raw byte offset into the captured
            # Claude Code transcript file (context_tree_capture: rec.offset).
            # The demo transcript embeds environment-sensitive content (the
            # runtime ``allowlisted_env`` block carries HOME / PATH / USER /
            # etc.), so the byte length of earlier records — and therefore this
            # offset — differs per machine/HOME. Pin it to a stable sentinel so
            # normalized output is byte-identical across environments. Node
            # ordering and relationships remain pinned via step_index, the
            # content-hash slots, and the event sequence, so this does not
            # weaken regression detection.
            return "<transcript-offset>"
        return value

    def _slot(self, kind: str, value: str) -> str:
        values = self.slots.setdefault(kind, {})
        if value not in values:
            values[value] = f"<{kind}:{len(values) + 1:02d}>"
        return values[value]

    def _known_replacements(self, text: str) -> str:
        replacements: list[tuple[int, int, str, str]] = []
        for kind, mapping in self.slots.items():
            priority = REPLACEMENT_KIND_PRIORITY.get(kind, 10)
            replacements.extend(
                (priority, -len(original), original, replacement)
                for original, replacement in mapping.items()
            )
        for _priority, _length, original, replacement in sorted(replacements):
            text = text.replace(original, replacement)
        return text

    def _normalize_string(self, value: str, *, key: str | None) -> str:
        text = value.replace(str(self.run_root), "<run-root>")
        text = TIMESTAMP_RE.sub("<timestamp>", text)

        if key in {"event_id", "previous_event_id"}:
            return self._slot("trail-event", text)
        if key == "projection_digest":
            # ``projection_digest`` is a sha256 over content-addressed event_ids
            # (see core/trails/contract.py::projection_digest). The event_id
            # material is machine/run-root-sensitive, so the digest's hash bytes
            # differ per machine. Slotting it positionally (content-hash:NN by
            # first-seen order) makes the slot index drift across machines.
            # Pin it to a single stable, field-keyed sentinel so normalized
            # output is byte-identical regardless of the underlying hash value
            # or discovery order. We still assert the digest is PRESENT and
            # well-shaped below, so a missing/absent watermark is still caught.
            if text in {"", "None"}:
                return text
            return "<projection-digest>"
        if key == "content_hash":
            return self._slot("content-hash", text)
        if key == "batch_id":
            return self._slot("batch", text)
        if key == "trace_id":
            return self._slot("trace", text)
        if key == "trace_patch_id":
            return self._slot("trace-patch", text)
        if key == "git_anchor_id":
            return self._slot("git-anchor", text)
        if key == "snapshot_id":
            return self._slot("snapshot", text)
        if key == "workspace_id":
            return self._slot("workspace", text)
        if key in {"commit_sha", "sha"} and HEX_40_RE.fullmatch(text):
            return self._slot("git-commit", text)
        if key == "hex" and (HEX_40_RE.fullmatch(text) or HEX_64_RE.fullmatch(text)):
            return self._slot("git-object", text)

        text = self._known_replacements(text)
        text = TRAIL_EVENT_RE.sub(lambda match: self._slot("trail-event", match.group(0)), text)
        text = SHA256_REF_RE.sub(lambda match: self._slot("content-hash", match.group(0)), text)
        text = MATERIALIZED_WORKTREE_RE.sub(
            lambda match: "trace-worktrees/"
            + self._slot("materialized-worktree", match.group(0).rsplit("/", 1)[1]),
            text,
        )
        text = UUID_RE.sub(lambda match: self._slot("uuid", match.group(0)), text)
        text = DISPLAY_REF_RE.sub(lambda match: self._slot("display-ref", match.group(0)), text)
        text = HEX_64_RE.sub(lambda match: self._slot("hex64", match.group(0)), text)
        text = HEX_40_RE.sub(lambda match: self._slot("git-object", match.group(0)), text)
        return text


@contextlib.contextmanager
def _deterministic_git_environment() -> Iterator[None]:
    old_env = {
        name: os.environ.get(name)
        for name in (
            "GIT_AUTHOR_DATE",
            "GIT_COMMITTER_DATE",
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
        )
    }
    os.environ.update(
        {
            "GIT_AUTHOR_DATE": "2026-04-28T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-04-28T00:00:00Z",
            "GIT_AUTHOR_NAME": "opentraces-corpus",
            "GIT_AUTHOR_EMAIL": "opentraces-corpus@example.invalid",
            "GIT_COMMITTER_NAME": "opentraces-corpus",
            "GIT_COMMITTER_EMAIL": "opentraces-corpus@example.invalid",
        }
    )
    try:
        yield
    finally:
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _read_events(repo: Path) -> list[dict[str, Any]]:
    from opentraces.core.trails import read_events

    return [event.model_dump(mode="json") for event in read_events(repo)]


def _command_summary(commands: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for name, payload in commands.items():
        rows.append(
            {
                "name": name,
                "path": f"{name}.json",
                "top_level_keys": sorted(payload) if isinstance(payload, dict) else [],
            }
        )
    return {
        "schema_version": COMMAND_SUMMARY_SCHEMA_VERSION,
        "scenario": SCENARIO_NAME,
        "command_count": len(rows),
        "commands": rows,
    }


def _api_payloads(commands: dict[str, Any]) -> dict[str, Any]:
    return {
        "blame_commit": commands["blame_commit"],
        "graph": commands["graph"],
        "trail_explain_step": commands["trail_explain_step"],
        "trail_play": commands["trail_play"],
        "trail_search_commit": commands["trail_search_commit"],
    }


def _api_summary(api_payloads: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": API_SCHEMA_VERSION,
        "scenario": SCENARIO_NAME,
        "payloads": [
            {
                "name": name,
                "path": f"{name}.json",
                "top_level_keys": sorted(payload) if isinstance(payload, dict) else [],
            }
            for name, payload in sorted(api_payloads.items())
        ],
    }


def _workspace_payloads(workspace_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = _read_json(workspace_dir / "manifest.json")
    trace_rows: list[dict[str, Any]] = []
    trace_record_rel = manifest.get("trace_record")
    if isinstance(trace_record_rel, str):
        trace_record_path = workspace_dir / trace_record_rel
        if trace_record_path.exists():
            trace_rows = _read_jsonl(trace_record_path)
    return manifest, trace_rows


def _dataset_readme() -> str:
    return """# Trace Trails Corpus v1

This deterministic fixture is generated from the Trace Trails full-stack demo.
It is intentionally small and synthetic: one TraceRecord row, canonical
TrailEvents, selected command/API payloads, and a Trace Workspace manifest
without a binary Git bundle.

Regenerate it with:

```bash
.venv/bin/python tests/integration/harness/trace_trails_corpus.py --update
```
"""


def build_corpus(output_dir: Path, *, run_root: Path) -> dict[str, Any]:
    """Generate the normalized corpus into ``output_dir``."""
    from tests.integration.harness.trace_trails_full_stack_demo import run_demo

    output_dir = output_dir.resolve()
    run_root = run_root.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output path is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)

    with _deterministic_git_environment():
        summary = run_demo(run_root / SCENARIO_NAME, verbose=True)

    source_repo = Path(summary["source_repo_unavailable"])
    events = _read_events(source_repo)
    normalizer = StableNormalizer(run_root / SCENARIO_NAME)

    commands = summary["commands"]
    workspace_manifest, trace_rows = _workspace_payloads(Path(summary["workspace"]))
    summary_without_commands = {key: value for key, value in summary.items() if key != "commands"}
    normalized_events = [normalizer.normalize(event) for event in events]
    normalized_summary = normalizer.normalize(summary_without_commands)
    normalized_commands = normalizer.normalize(commands)
    normalized_workspace_manifest = normalizer.normalize(workspace_manifest)
    normalized_trace_rows = [normalizer.normalize(row) for row in trace_rows]
    normalized_api_payloads = _api_payloads(normalized_commands)

    event_type_counts = Counter(event["event_type"] for event in normalized_events)
    event_summary = {
        "schema_version": EVENTS_SCHEMA_VERSION,
        "scenario": SCENARIO_NAME,
        "event_count": len(normalized_events),
        "event_type_counts": dict(sorted(event_type_counts.items())),
    }
    command_summary = _command_summary(normalized_commands)
    api_summary = _api_summary(normalized_api_payloads)
    dataset_manifest = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "scenario": SCENARIO_NAME,
        "trace_record_count": len(normalized_trace_rows),
        "trail_event_count": len(normalized_events),
        "data_files": {
            "traces": "data/traces.jsonl",
            "trail_events": "data/trail_events.jsonl",
        },
    }
    manifest = {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "corpus_version": CORPUS_VERSION,
        "source_harness": "tests.integration.harness.trace_trails_full_stack_demo.run_demo",
        "scenarios": [
            {
                "name": SCENARIO_NAME,
                "summary": "summaries/full_stack_demo.json",
                "commands": "commands/full_stack_demo/",
                "api": "api/full_stack_demo/",
                "dataset": "dataset/",
                "workspace_manifest": "workspace/manifest.json",
                "workspace_trace_records": "workspace/traces/full_stack_demo.jsonl",
                "trail_events": "trail/events.jsonl",
                "status": normalized_summary["status"],
                "trace_id": normalized_summary["trace_id"],
                "step_index": normalized_summary["step_index"],
                "event_count": len(normalized_events),
                "event_type_counts": event_summary["event_type_counts"],
            }
        ],
    }

    _write_json(output_dir / "manifest.json", manifest)
    _write_json(output_dir / "summaries" / "full_stack_demo.json", normalized_summary)
    _write_json(output_dir / "commands" / "full_stack_demo" / "summary.json", command_summary)
    for command_name, payload in normalized_commands.items():
        _write_json(output_dir / "commands" / "full_stack_demo" / f"{command_name}.json", payload)
    _write_json(output_dir / "api" / "full_stack_demo" / "summary.json", api_summary)
    for api_name, payload in normalized_api_payloads.items():
        _write_json(output_dir / "api" / "full_stack_demo" / f"{api_name}.json", payload)
    _write_json(output_dir / "workspace" / "manifest.json", normalized_workspace_manifest)
    _write_jsonl(output_dir / "workspace" / "traces" / "full_stack_demo.jsonl", normalized_trace_rows)
    _write_json(output_dir / "trail" / "events.summary.json", event_summary)
    _write_jsonl(output_dir / "trail" / "events.jsonl", normalized_events)
    _write_text(output_dir / "dataset" / "README.md", _dataset_readme())
    _write_json(output_dir / "dataset" / "manifest.json", dataset_manifest)
    try:
        from opentraces.publish.huggingface.schema import build_dataset_infos

        dataset_infos = build_dataset_infos("trace_trails_corpus_v1")
    except Exception:
        dataset_infos = {
            "schema_version": "opentraces.dataset_infos.unavailable",
            "reason": "build_dataset_infos_failed",
        }
    _write_json(output_dir / "dataset" / "dataset_infos.json", dataset_infos)
    _write_jsonl(output_dir / "dataset" / "data" / "traces.jsonl", normalized_trace_rows)
    _write_jsonl(output_dir / "dataset" / "data" / "trail_events.jsonl", normalized_events)
    return manifest


def _files_under(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    files: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        files[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return files


def _compare_dirs(expected: Path, actual: Path) -> dict[str, Any]:
    expected_files = _files_under(expected)
    actual_files = _files_under(actual)
    expected_names = set(expected_files)
    actual_names = set(actual_files)
    changed = sorted(
        name
        for name in expected_names & actual_names
        if expected_files[name] != actual_files[name]
    )
    return {
        "missing": sorted(expected_names - actual_names),
        "extra": sorted(actual_names - expected_names),
        "changed": changed,
        "expected_files": expected_files,
        "actual_files": actual_files,
    }


def _format_mismatch(expected: Path, actual: Path, comparison: dict[str, Any]) -> str:
    lines = [
        f"Trace Trails corpus fixture mismatch: expected={expected} actual={actual}",
    ]
    if comparison["missing"]:
        lines.append("missing files: " + ", ".join(comparison["missing"]))
    if comparison["extra"]:
        lines.append("extra files: " + ", ".join(comparison["extra"]))
    for name in comparison["changed"][:3]:
        lines.append(f"changed file: {name}")
        diff = difflib.unified_diff(
            comparison["expected_files"][name].splitlines(),
            comparison["actual_files"][name].splitlines(),
            fromfile=f"fixture/{name}",
            tofile=f"generated/{name}",
            lineterm="",
        )
        lines.extend(list(diff)[:80])
    return "\n".join(lines)


def _generated_temp_dir() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="opentraces-trace-trails-corpus-")


def check_corpus(fixture_dir: Path = DEFAULT_FIXTURE_DIR) -> dict[str, Any]:
    fixture_dir = fixture_dir.resolve()
    with _generated_temp_dir() as td:
        temp_root = Path(td)
        generated = temp_root / CORPUS_VERSION
        build_corpus(generated, run_root=temp_root / "run")
        comparison = _compare_dirs(fixture_dir, generated)
        has_diff = bool(comparison["missing"] or comparison["extra"] or comparison["changed"])
        if has_diff:
            raise CorpusMismatchError(_format_mismatch(fixture_dir, generated, comparison))
        return {
            "status": "ok",
            "fixture_dir": str(fixture_dir),
            "file_count": len(_files_under(fixture_dir)),
        }


def update_corpus(fixture_dir: Path = DEFAULT_FIXTURE_DIR) -> dict[str, Any]:
    fixture_dir = fixture_dir.resolve()
    with _generated_temp_dir() as td:
        temp_root = Path(td)
        generated = temp_root / CORPUS_VERSION
        build_corpus(generated, run_root=temp_root / "run")
        replacement = fixture_dir.parent / f".{fixture_dir.name}.tmp"
        if replacement.exists():
            shutil.rmtree(replacement)
        shutil.copytree(generated, replacement)
        if fixture_dir.exists():
            shutil.rmtree(fixture_dir)
        replacement.rename(fixture_dir)
    return {
        "status": "updated",
        "fixture_dir": str(fixture_dir),
        "file_count": len(_files_under(fixture_dir)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="compare generated corpus with fixture")
    mode.add_argument("--update", action="store_true", help="replace fixture with generated corpus")
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        default=DEFAULT_FIXTURE_DIR,
        help=f"version fixture directory (default: {DEFAULT_FIXTURE_DIR})",
    )
    args = parser.parse_args(argv)

    try:
        result = update_corpus(args.fixture_dir) if args.update else check_corpus(args.fixture_dir)
    except CorpusMismatchError as exc:
        print(str(exc))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
