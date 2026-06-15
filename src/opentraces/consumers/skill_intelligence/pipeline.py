"""Skill Intelligence data/eval pipeline.

This consumer connects the existing OpenTraces substrate to SkillOpt without
creating a second skill ledger. The source of truth for observed skill use is
Trace Index ``skill_invocation`` units; everything else here is a projection or
case-study artifact over that read-side surface.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from opentraces_schema import TraceFacet, TraceRecord, TraceUnit

from ...core import trace_index
from ...core._time import utc_now_str as _utc_now
from ...core.bucket_store import iter_trace_record_objects
from ..skill_opt import engine as skill_opt
from ..skill_opt.proposers import default_proposer
from ..skill_opt.rerollout import FakeReRolloutRunner, ReRolloutTask
from ..skill_opt.runner import DEFAULT_INITIAL_SKILL

DEFAULT_SKILL_CANDIDATES: tuple[str, ...] = (
    "handoff",
    "opentraces",
    "scout-research",
    "goal-forge",
)
EPISODE_SCHEMA_VERSION = "opentraces.skill_episodes.v1"
EVAL_TASK_SCHEMA_VERSION = "opentraces.skill_eval_tasks.v1"
ROLLOUT_SCHEMA_VERSION = "opentraces.skill_rollouts.v1"
REPORT_SCHEMA_VERSION = "opentraces.skill_update_report.v1"
APPROVAL_STATE = "manual_required_default_off"


@dataclass(frozen=True)
class SkillIntelligenceRequest:
    out_dir: Path
    project: str | None = None
    skill: str | None = None
    min_usable_episodes: int = 30
    seed: str = "skill-intelligence"
    dry_run: bool = True


@dataclass(frozen=True)
class SkillIntelligenceResult:
    out_dir: Path
    selected_skill: str
    report_path: Path
    markdown_report_path: Path
    audit_path: Path
    case_study_dir: Path
    dtest_score: float
    dsel_before: float
    dsel_after: float
    accepted: bool
    split_counts: dict[str, int]
    metadata: dict[str, Any]


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest_payload(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _digest_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip().lower()).strip("-")
    return safe or "skill"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, sort_keys=True, ensure_ascii=True) for row in rows]
    path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _facet_value(facets: Sequence[TraceFacet], name: str) -> str | None:
    for facet in facets:
        if facet.name == name and facet.value not in (None, ""):
            return str(facet.value)
    return None


def _unit_confidence(unit: TraceUnit) -> str:
    for signal in unit.signals:
        if signal.name == "skill_invoked":
            return signal.confidence or "medium"
    return "medium"


def _unit_skill(unit: TraceUnit) -> str:
    if unit.skills:
        return str(unit.skills[0])
    value = unit.metadata.get("skill_name")
    return str(value or "")


def _source_for_unit(unit: TraceUnit) -> str:
    value = unit.metadata.get("source")
    return str(value or "unknown")


def _refresh_skill_units(
    *,
    project: str | None = None,
    index_path: Path | None = None,
) -> list[TraceUnit]:
    if index_path is None:
        return trace_index.list_skill_invocation_units_from_records(project_slug=project)
    db_path = index_path or trace_index.default_index_path()
    if db_path.exists():
        trace_index.refresh_index(db_path)
    else:
        trace_index.rebuild_index(db_path)
    units = trace_index.list_units(
        index_path=db_path,
        project_slug=project,
        unit_type="skill_invocation",
    )
    return trace_index.latest_units(units)


def audit_skill_invocations(
    *,
    project: str | None = None,
    index_path: Path | None = None,
    min_usable_episodes: int = 30,
    preferred_skill: str | None = None,
    candidates: Sequence[str] = DEFAULT_SKILL_CANDIDATES,
) -> dict[str, Any]:
    """Aggregate Trace Index ``skill_invocation`` units and choose a skill pack."""

    units = _refresh_skill_units(project=project, index_path=index_path)
    by_skill: dict[str, dict[str, Any]] = {}
    by_agent: dict[str, int] = {}
    by_source: dict[str, int] = {}

    for unit in units:
        skill = _unit_skill(unit)
        if not skill:
            continue
        agent = _facet_value(unit.facets, "agent.name") or "unknown"
        source = _source_for_unit(unit)
        confidence = _unit_confidence(unit)
        project_slug = unit.project_slug or "unknown"
        entry = by_skill.setdefault(
            skill,
            {
                "skill_id": skill,
                "total_units": 0,
                "usable_episodes": 0,
                "agents": {},
                "sources": {},
                "projects": {},
                "confidences": {},
            },
        )
        entry["total_units"] += 1
        if confidence in {"high", "medium"}:
            entry["usable_episodes"] += 1
        entry["agents"][agent] = entry["agents"].get(agent, 0) + 1
        entry["sources"][source] = entry["sources"].get(source, 0) + 1
        entry["projects"][project_slug] = entry["projects"].get(project_slug, 0) + 1
        entry["confidences"][confidence] = entry["confidences"].get(confidence, 0) + 1
        by_agent[agent] = by_agent.get(agent, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1

    selected = _select_skill(
        by_skill,
        min_usable_episodes=min_usable_episodes,
        preferred_skill=preferred_skill,
        candidates=candidates,
    )
    data_gap = int(selected["usable_episodes"]) < min_usable_episodes
    return {
        "schema_version": "opentraces.skill_intelligence.corpus_audit.v1",
        "generated_at": _utc_now(),
        "project": project,
        "total_skill_invocation_units": sum(int(v["total_units"]) for v in by_skill.values()),
        "counts_by_skill": {
            skill: _sorted_nested_counts(payload) for skill, payload in sorted(by_skill.items())
        },
        "counts_by_agent": dict(sorted(by_agent.items())),
        "counts_by_source": dict(sorted(by_source.items())),
        "candidate_skills": list(candidates),
        "min_usable_episodes": min_usable_episodes,
        "selected_skill": selected["skill_id"],
        "selected_skill_counts": selected,
        "data_gap": data_gap,
        "seeded_ci_corpus_required": data_gap,
        "selection_reason": selected["selection_reason"],
    }


def _sorted_nested_counts(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    for key in ("agents", "sources", "projects", "confidences"):
        out[key] = dict(sorted(out.get(key, {}).items()))
    return out


def _select_skill(
    by_skill: dict[str, dict[str, Any]],
    *,
    min_usable_episodes: int,
    preferred_skill: str | None,
    candidates: Sequence[str],
) -> dict[str, Any]:
    if preferred_skill:
        payload = by_skill.get(preferred_skill, {})
        return {
            "skill_id": preferred_skill,
            "usable_episodes": int(payload.get("usable_episodes", 0)),
            "total_units": int(payload.get("total_units", 0)),
            "selection_reason": "requested explicitly",
        }

    eligible = [
        payload
        for skill, payload in by_skill.items()
        if skill in candidates and int(payload.get("usable_episodes", 0)) >= min_usable_episodes
    ]
    if eligible:
        best = sorted(
            eligible,
            key=lambda item: (-int(item["usable_episodes"]), str(item["skill_id"])),
        )[0]
        return {
            "skill_id": str(best["skill_id"]),
            "usable_episodes": int(best["usable_episodes"]),
            "total_units": int(best["total_units"]),
            "selection_reason": "meets minimum usable episode threshold",
        }

    observed_candidates = [by_skill[skill] for skill in candidates if skill in by_skill]
    if observed_candidates:
        best = sorted(
            observed_candidates,
            key=lambda item: (-int(item["usable_episodes"]), str(item["skill_id"])),
        )[0]
        return {
            "skill_id": str(best["skill_id"]),
            "usable_episodes": int(best["usable_episodes"]),
            "total_units": int(best["total_units"]),
            "selection_reason": "best observed preferred skill; seeded CI corpus required",
        }

    if by_skill:
        best = sorted(
            by_skill.values(),
            key=lambda item: (-int(item["usable_episodes"]), str(item["skill_id"])),
        )[0]
        return {
            "skill_id": str(best["skill_id"]),
            "usable_episodes": int(best["usable_episodes"]),
            "total_units": int(best["total_units"]),
            "selection_reason": "best observed skill outside preferred set; seeded CI corpus required",
        }

    fallback = "opentraces"
    return {
        "skill_id": fallback,
        "usable_episodes": 0,
        "total_units": 0,
        "selection_reason": "no observed skill invocations; seeded CI corpus required",
    }


def build_episode_rows(
    *,
    selected_skill: str,
    project: str | None = None,
    min_rows: int = 0,
    index_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Project Trace Index skill units into ``skill-episodes-v1`` rows."""

    if index_path is None:
        try:
            from ...core.trace_search_snapshot import list_skill_invocation_units

            units = list_skill_invocation_units(
                skill=selected_skill,
                project=project,
            )
            rows = [_episode_row_from_unit(unit, None) for unit in units]
        except Exception:
            records = _records_by_trace_id(project)
            rows = [
                _episode_row_from_unit(unit, records.get(unit.trace_id))
                for unit in _refresh_skill_units(project=project, index_path=index_path)
                if _unit_skill(unit) == selected_skill
            ]
    else:
        records = _records_by_trace_id(project)
        rows = [
            _episode_row_from_unit(unit, records.get(unit.trace_id))
            for unit in _refresh_skill_units(project=project, index_path=index_path)
            if _unit_skill(unit) == selected_skill
        ]
    rows.sort(key=lambda row: (row["source_trace_id"], row["source_unit_id"]))
    if len(rows) < min_rows:
        rows.extend(_seed_episode_rows(selected_skill, start=len(rows), count=min_rows - len(rows)))
    return rows


def _records_by_trace_id(project: str | None) -> dict[str, TraceRecord]:
    return {
        obj.trace_id: obj.record
        for obj in iter_trace_record_objects(project_slug=project)
    }


def _episode_row_from_unit(unit: TraceUnit, record: TraceRecord | None) -> dict[str, Any]:
    skill = _unit_skill(unit)
    metadata = unit.metadata or {}
    outcome = getattr(record, "outcome", None) if record is not None else None
    outcome_success = getattr(outcome, "success", None)
    outcome_committed = bool(getattr(outcome, "committed", False))
    reward = skill_opt.outcome_reward(
        success=outcome_success,
        committed=outcome_committed,
        survival_state=getattr(outcome, "survival_state", None),
    )
    step = metadata.get("step_index")
    if not isinstance(step, int):
        step = 0
    limitations = []
    if record is None:
        limitations.append("source_trace_record_unavailable")
    if outcome_success is None:
        limitations.append("missing_verifier_feedback")
    return {
        "schema_version": EPISODE_SCHEMA_VERSION,
        "episode_id": "episode:" + hashlib.sha256(unit.unit_id.encode("utf-8")).hexdigest()[:16],
        "source_trace_id": unit.trace_id,
        "source_unit_id": unit.unit_id,
        "skill_id": skill,
        "skill_version_digest": "sha256:unknown",
        "agent": _facet_value(unit.facets, "agent.name") or "unknown",
        "skill_source": _source_for_unit(unit),
        "user_intent": unit.intent_text,
        "task_summary": unit.title_text,
        "trajectory_start_step": step,
        "trajectory_end_step": step,
        "files_touched_json": json.dumps(sorted(unit.files), sort_keys=True),
        "tools_used_json": json.dumps(_record_tools(record), sort_keys=True),
        "outcome_label": _outcome_label(outcome_success),
        "outcome_reward": reward,
        "confidence": _unit_confidence(unit),
        "limitations_json": json.dumps(limitations, sort_keys=True),
        "evidence_refs_json": json.dumps(
            {
                "trace_id": unit.trace_id,
                "unit_id": unit.unit_id,
                "trace_index_unit_type": unit.unit_type,
            },
            sort_keys=True,
        ),
        "review_status": "approved" if not limitations else "approved_with_limitations",
    }


def _record_tools(record: TraceRecord | None) -> list[str]:
    if record is None:
        return []
    return sorted(
        {
            str(call.tool_name)
            for step in record.steps
            for call in step.tool_calls
            if call.tool_name
        }
    )


def _outcome_label(value: bool | None) -> str:
    if value is True:
        return "success"
    if value is False:
        return "failure"
    return "unknown"


def _seed_episode_rows(skill: str, *, start: int, count: int) -> list[dict[str, Any]]:
    safe = _safe_id(skill)
    rows: list[dict[str, Any]] = []
    for offset in range(count):
        idx = start + offset
        episode_id = f"episode:ci-{safe}-{idx:04d}"
        trace_id = f"ci-seed-{safe}-{idx:04d}"
        reward = 0.0 if idx % 3 else 1.0
        outcome = "failure" if reward < 0.5 else "success"
        rows.append(
            {
                "schema_version": EPISODE_SCHEMA_VERSION,
                "episode_id": episode_id,
                "source_trace_id": trace_id,
                "source_unit_id": f"tu:{trace_id}:skill:metadata:0",
                "skill_id": skill,
                "skill_version_digest": "sha256:ci-seeded-baseline",
                "agent": "synthetic-ci",
                "skill_source": "seeded_ci_corpus",
                "user_intent": f"Use {skill} to produce the deterministic CI echo sentinel.",
                "task_summary": f"Seeded Skill Intelligence CI task {idx}",
                "trajectory_start_step": 1,
                "trajectory_end_step": 2,
                "files_touched_json": json.dumps(["src/app.py"], sort_keys=True),
                "tools_used_json": json.dumps(["synthetic-echo"], sort_keys=True),
                "outcome_label": outcome,
                "outcome_reward": reward,
                "confidence": "high",
                "limitations_json": json.dumps(["seeded_ci_corpus"], sort_keys=True),
                "evidence_refs_json": json.dumps(
                    {"seed": "skill-intelligence-ci", "ordinal": idx},
                    sort_keys=True,
                ),
                "review_status": "approved",
            }
        )
    return rows


def build_eval_task_rows(
    episodes: Sequence[dict[str, Any]],
    *,
    seed: str = "skill-intelligence",
) -> list[dict[str, Any]]:
    """Turn reviewed episodes into leakage-safe ``skill-eval-tasks-v1`` rows."""

    approved = [
        row for row in episodes
        if str(row.get("review_status", "")).startswith("approved")
    ]
    leakage_keys = {
        _leakage_key(row, seed=seed)
        for row in approved
    }
    split_by_key = _assign_splits(leakage_keys, seed=seed)
    tasks: list[dict[str, Any]] = []
    for index, row in enumerate(approved):
        leakage_key = _leakage_key(row, seed=seed)
        skill_id = str(row["skill_id"])
        marker = _marker_for_skill(skill_id)
        task_id = "task:" + hashlib.sha256(
            f"{row['episode_id']}:{leakage_key}".encode("utf-8")
        ).hexdigest()[:16]
        task = {
            "schema_version": EVAL_TASK_SCHEMA_VERSION,
            "task_id": task_id,
            "skill_id": skill_id,
            "family_id": f"skill-intelligence/{_safe_id(skill_id)}",
            "source_episode_ids_json": json.dumps([row["episode_id"]], sort_keys=True),
            "prompt": (
                f"Use the {skill_id} skill to complete task {index}. "
                "The verifier expects the OPENTRACES_OK sentinel."
            ),
            "workspace_template": "synthetic-ci-echo",
            "verifier": "contains:OPENTRACES_OK",
            "reward_spec_json": json.dumps(
                {
                    "kind": "required_skill_markers",
                    "required_markers": [marker],
                    "reward": "fraction_of_required_markers_present",
                },
                sort_keys=True,
            ),
            "split": split_by_key[leakage_key],
            "leakage_key": leakage_key,
            "source_agent_mix_json": json.dumps(
                {str(row.get("agent") or "unknown"): 1},
                sort_keys=True,
            ),
            "evidence_refs_json": json.dumps(
                {
                    "source_trace_id": row["source_trace_id"],
                    "source_unit_id": row["source_unit_id"],
                    "source_episode_id": row["episode_id"],
                },
                sort_keys=True,
            ),
        }
        tasks.append(task)
    return tasks


def _marker_for_skill(skill_id: str) -> str:
    return f"rl.{_safe_id(skill_id)}.ci_echo"


def _leakage_key(row: dict[str, Any], *, seed: str) -> str:
    source = row.get("source_trace_id") or row.get("episode_id")
    return hashlib.sha256(f"{seed}:{source}".encode("utf-8")).hexdigest()[:16]


def _assign_splits(keys: Iterable[str], *, seed: str) -> dict[str, str]:
    ordered = sorted(set(keys), key=lambda key: hashlib.sha256(f"{seed}:{key}".encode()).hexdigest())
    splits: dict[str, str] = {}
    for index, key in enumerate(ordered):
        mod = index % 5
        if mod == 0:
            split = "Dsel"
        elif mod == 1:
            split = "Dtest"
        else:
            split = "Dtrain"
        splits[key] = split
    return splits


@dataclass(frozen=True)
class _SyntheticTask:
    task_id: str
    skill_id: str
    split: str
    prompt: str
    required_markers: tuple[str, ...]
    source_eval_task_id: str

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "_SyntheticTask":
        reward_spec = json.loads(str(row.get("reward_spec_json") or "{}"))
        markers = tuple(str(marker) for marker in reward_spec.get("required_markers") or ())
        return cls(
            task_id=str(row["task_id"]),
            skill_id=str(row["skill_id"]),
            split=str(row["split"]),
            prompt=str(row["prompt"]),
            required_markers=markers,
            source_eval_task_id=str(row["task_id"]),
        )

    def to_rerollout_task(self) -> ReRolloutTask:
        return ReRolloutTask(
            task_id=self.task_id,
            prompt=self.prompt,
            required_markers=self.required_markers,
            source_trace_id=self.source_eval_task_id,
        )


class _SyntheticHarness:
    def __init__(self) -> None:
        self.runner = FakeReRolloutRunner()

    def collect_rollouts(
        self,
        skill_text: str,
        tasks: Sequence[object],
    ) -> list[skill_opt.RolloutRow]:
        rows: list[skill_opt.RolloutRow] = []
        for task in tasks:
            if not isinstance(task, _SyntheticTask):
                continue
            result = self.runner.run(skill_text, task.to_rerollout_task())
            missing = tuple(
                marker
                for marker in task.required_markers
                if skill_opt.RULE_MARKER.format(tag=marker) not in skill_text
            )
            rows.append(
                skill_opt.RolloutRow(
                    trace_id=task.task_id,
                    score=result.reward,
                    reward=result.reward,
                    failure_tags=missing if result.reward < 1.0 else (),
                    success_tags=task.required_markers if result.reward >= 1.0 else (),
                    summary=result.detail,
                )
            )
        return rows

    def score(self, skill_text: str, tasks: Sequence[object]) -> float:
        rewards = [
            self.runner.run(skill_text, task.to_rerollout_task()).reward
            for task in tasks
            if isinstance(task, _SyntheticTask)
        ]
        return round(sum(rewards) / len(rewards), 6) if rewards else 0.0


def build_rollout_rows(
    eval_tasks: Sequence[dict[str, Any]],
    *,
    skill_text: str,
    skill_digest: str,
    out_dir: Path,
    agent_binary: str = "synthetic-echo",
) -> list[dict[str, Any]]:
    """Run deterministic synthetic re-rollouts into ``skill-rollouts-v1`` rows."""

    runner = FakeReRolloutRunner()
    rows: list[dict[str, Any]] = []
    for task_row in eval_tasks:
        task = _SyntheticTask.from_row(task_row)
        result = runner.run(skill_text, task.to_rerollout_task())
        missing = [
            marker
            for marker in task.required_markers
            if skill_opt.RULE_MARKER.format(tag=marker) not in skill_text
        ]
        success = [] if missing else list(task.required_markers)
        rollout_id = "rollout:" + hashlib.sha256(
            f"{task.task_id}:{skill_digest}".encode("utf-8")
        ).hexdigest()[:16]
        transcript = {
            "schema_version": "opentraces.skill_rollout_transcript.v1",
            "rollout_id": rollout_id,
            "task_id": task.task_id,
            "skill_digest": skill_digest,
            "prompt": task.prompt,
            "required_markers": list(task.required_markers),
            "reward": result.reward,
            "detail": result.detail,
            "agent_binary": agent_binary,
        }
        transcript_path = out_dir / "transcripts" / f"{_safe_id(rollout_id)}.json"
        _write_json(transcript_path, transcript)
        rows.append(
            {
                "schema_version": ROLLOUT_SCHEMA_VERSION,
                "rollout_id": rollout_id,
                "task_id": task.task_id,
                "skill_id": task.skill_id,
                "skill_digest": skill_digest,
                "split": task.split,
                "harness": "synthetic-otbox",
                "agent_binary": agent_binary,
                "reward": result.reward,
                "failure_tags_json": json.dumps(missing, sort_keys=True),
                "success_tags_json": json.dumps(success, sort_keys=True),
                "transcript_ref": transcript_path.relative_to(out_dir).as_posix(),
                "artifact_refs_json": json.dumps([], sort_keys=True),
                "source_eval_task_id": task.source_eval_task_id,
            }
        )
    return rows


def rollout_rows_to_skillopt(rows: Sequence[dict[str, Any]]) -> list[skill_opt.RolloutRow]:
    out: list[skill_opt.RolloutRow] = []
    for row in rows:
        out.append(
            skill_opt.RolloutRow(
                trace_id=str(row.get("task_id") or row.get("rollout_id") or ""),
                score=float(row.get("reward") or 0.0),
                reward=float(row.get("reward") or 0.0),
                failure_tags=tuple(json.loads(str(row.get("failure_tags_json") or "[]"))),
                success_tags=tuple(json.loads(str(row.get("success_tags_json") or "[]"))),
                summary=str(row.get("transcript_ref") or ""),
            )
        )
    return out


def run_pipeline(request: SkillIntelligenceRequest) -> SkillIntelligenceResult:
    """Run audit -> episodes -> eval tasks -> synthetic rollouts -> SkillOpt report."""

    out_dir = Path(request.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    case_dir = out_dir / "case-study" / "ci-echo"
    case_dir.mkdir(parents=True, exist_ok=True)

    audit = audit_skill_invocations(
        project=request.project,
        min_usable_episodes=request.min_usable_episodes,
        preferred_skill=request.skill,
    )
    selected_skill = str(audit["selected_skill"])
    audit_path = out_dir / "corpus-audit.json"
    _write_json(audit_path, audit)
    existing_case = out_dir / "case-study" / "existing-bucket"
    existing_case.mkdir(parents=True, exist_ok=True)
    _write_json(existing_case / "corpus-audit.json", audit)

    episode_rows = build_episode_rows(
        selected_skill=selected_skill,
        project=request.project,
        min_rows=request.min_usable_episodes,
    )
    episodes_path = case_dir / "skill-episodes-v1.jsonl"
    _write_jsonl(episodes_path, episode_rows)

    eval_task_rows = build_eval_task_rows(episode_rows, seed=request.seed)
    eval_tasks_path = case_dir / "skill-eval-tasks-v1.jsonl"
    _write_jsonl(eval_tasks_path, eval_task_rows)
    split_counts = _split_counts(eval_task_rows)

    initial_skill = _initial_skill_for(selected_skill)
    baseline_digest = _digest_text(initial_skill)
    baseline_rollouts = build_rollout_rows(
        eval_task_rows,
        skill_text=initial_skill,
        skill_digest=baseline_digest,
        out_dir=case_dir / "baseline",
    )
    baseline_rollouts_path = case_dir / "skill-rollouts-v1.baseline.jsonl"
    _write_jsonl(baseline_rollouts_path, baseline_rollouts)

    train_tasks, selection_tasks, test_tasks = _tasks_by_split(eval_task_rows)
    opt_result = skill_opt.run_optimization(
        initial_skill,
        rollout_rows_to_skillopt(baseline_rollouts),
        propose=default_proposer,
        harness=_SyntheticHarness(),
        train_tasks=train_tasks,
        selection_tasks=selection_tasks,
        test_tasks=test_tasks,
        budget=1,
        budget_floor=1,
        schedule="constant",
        max_steps=4,
        epochs=1,
        slow_update=None,
        meta_update=None,
    )
    skill_artifacts = opt_result.state.export(case_dir)
    candidate_digest = _digest_text(opt_result.best_skill)
    candidate_rollouts = build_rollout_rows(
        eval_task_rows,
        skill_text=opt_result.best_skill,
        skill_digest=candidate_digest,
        out_dir=case_dir / "candidate",
    )
    candidate_rollouts_path = case_dir / "skill-rollouts-v1.candidate.jsonl"
    _write_jsonl(candidate_rollouts_path, candidate_rollouts)
    combined_rollouts_path = case_dir / "skill-rollouts-v1.jsonl"
    _write_jsonl(combined_rollouts_path, [*baseline_rollouts, *candidate_rollouts])

    report = _skill_update_report(
        selected_skill=selected_skill,
        baseline_digest=baseline_digest,
        candidate_digest=candidate_digest,
        baseline_skill=initial_skill,
        candidate_skill=opt_result.best_skill,
        opt_result=opt_result,
        eval_tasks=eval_task_rows,
        baseline_rollouts=baseline_rollouts,
        candidate_rollouts=candidate_rollouts,
        audit=audit,
        artifacts=skill_artifacts,
    )
    report_path = case_dir / "skill-update-report-v1.json"
    _write_json(report_path, report)
    markdown_report_path = case_dir / "skill-update-report-v1.md"
    markdown_report_path.write_text(_render_markdown_report(report), encoding="utf-8")
    (case_dir / "README.md").write_text(_render_case_study_readme(report), encoding="utf-8")

    metadata = {
        "audit_path": str(audit_path),
        "episodes_path": str(episodes_path),
        "eval_tasks_path": str(eval_tasks_path),
        "rollouts_path": str(combined_rollouts_path),
        "report_path": str(report_path),
        "markdown_report_path": str(markdown_report_path),
        "case_study_dir": str(case_dir),
        "selected_skill": selected_skill,
        "dtest_score": report["dtest_score"],
        "accepted": report["accepted"],
        "approval_state": report["approval_state"],
    }
    return SkillIntelligenceResult(
        out_dir=out_dir,
        selected_skill=selected_skill,
        report_path=report_path,
        markdown_report_path=markdown_report_path,
        audit_path=audit_path,
        case_study_dir=case_dir,
        dtest_score=float(report["dtest_score"]),
        dsel_before=float(report["dsel_before"]),
        dsel_after=float(report["dsel_after"]),
        accepted=bool(report["accepted"]),
        split_counts=split_counts,
        metadata=metadata,
    )


def _initial_skill_for(skill_id: str) -> str:
    return (
        f"# {skill_id} Skill Intelligence baseline\n\n"
        "Procedural rules learned from reviewed skill episodes. Candidate edits are "
        "accepted only when deterministic held-out Dsel reward improves.\n\n"
        "<!-- SLOW_UPDATE_START -->\n<!-- SLOW_UPDATE_END -->\n"
    )


def _split_counts(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts = {"Dtrain": 0, "Dsel": 0, "Dtest": 0}
    for row in rows:
        split = str(row.get("split") or "")
        if split in counts:
            counts[split] += 1
    return counts


def _tasks_by_split(
    rows: Sequence[dict[str, Any]],
) -> tuple[list[_SyntheticTask], list[_SyntheticTask], list[_SyntheticTask]]:
    tasks = [_SyntheticTask.from_row(row) for row in rows]
    train = [task for task in tasks if task.split == "Dtrain"]
    selection = [task for task in tasks if task.split == "Dsel"]
    test = [task for task in tasks if task.split == "Dtest"]
    return train or tasks, selection or tasks, test or selection or tasks


def _skill_update_report(
    *,
    selected_skill: str,
    baseline_digest: str,
    candidate_digest: str,
    baseline_skill: str,
    candidate_skill: str,
    opt_result: skill_opt.OptimizationResult,
    eval_tasks: Sequence[dict[str, Any]],
    baseline_rollouts: Sequence[dict[str, Any]],
    candidate_rollouts: Sequence[dict[str, Any]],
    audit: dict[str, Any],
    artifacts: dict[str, str],
) -> dict[str, Any]:
    candidate_diff = "".join(
        difflib.unified_diff(
            baseline_skill.splitlines(keepends=True),
            candidate_skill.splitlines(keepends=True),
            fromfile="baseline_skill.md",
            tofile="candidate_skill.md",
        )
    )
    split_counts = _split_counts(eval_tasks)
    limitations = []
    if audit.get("data_gap"):
        limitations.append("current_bucket_below_minimum_episode_threshold")
        limitations.append("deterministic_ci_seeded_corpus_used")
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": "skill-intelligence-" + hashlib.sha256(
            f"{selected_skill}:{baseline_digest}:{candidate_digest}".encode("utf-8")
        ).hexdigest()[:12],
        "skill_id": selected_skill,
        "baseline_digest": baseline_digest,
        "candidate_digest": candidate_digest,
        "dtrain_count": split_counts["Dtrain"],
        "dsel_count": split_counts["Dsel"],
        "dtest_count": split_counts["Dtest"],
        "dsel_before": opt_result.initial_score,
        "dsel_after": opt_result.best_score,
        "dtest_score": opt_result.test_score,
        "accepted": opt_result.accepted > 0,
        "candidate_diff": candidate_diff,
        "edit_audit_ref": Path(artifacts["report"]).name,
        "rollout_refs_json": json.dumps(
            {
                "baseline": [row["transcript_ref"] for row in baseline_rollouts],
                "candidate": [row["transcript_ref"] for row in candidate_rollouts],
            },
            sort_keys=True,
        ),
        "limitations_json": json.dumps(limitations, sort_keys=True),
        "approval_state": APPROVAL_STATE,
        "promotion_state": "default_off",
        "automatic_promotion": False,
        "split_counts": split_counts,
        "accepted_edits": opt_result.accepted,
        "rejected_edits": opt_result.rejected,
        "baseline_rollout_count": len(baseline_rollouts),
        "candidate_rollout_count": len(candidate_rollouts),
    }


def _render_markdown_report(report: dict[str, Any]) -> str:
    refs = _rollout_refs(report)
    limitations = _report_list_field(report, "limitations_json")
    baseline_ref = refs["baseline"][0] if refs["baseline"] else "none"
    candidate_ref = refs["candidate"][0] if refs["candidate"] else "none"
    limitations_line = (
        f"- Limitations: {', '.join(f'`{item}`' for item in limitations)}\n"
        if limitations
        else "- Limitations: none\n"
    )
    return (
        f"# Skill Update Report: {report['skill_id']}\n\n"
        f"- Baseline digest: `{report['baseline_digest']}`\n"
        f"- Candidate digest: `{report['candidate_digest']}`\n"
        f"- Dtrain/Dsel/Dtest: {report['dtrain_count']} / "
        f"{report['dsel_count']} / {report['dtest_count']}\n"
        f"- Dsel gate: {report['dsel_before']:.3f} -> {report['dsel_after']:.3f}\n"
        f"- Held-out Dtest: {report['dtest_score']:.3f}\n"
        f"- Baseline rollouts: {len(refs['baseline'])}; first ref: `{baseline_ref}`\n"
        f"- Candidate rollouts: {len(refs['candidate'])}; first ref: `{candidate_ref}`\n"
        f"- Accepted: {str(report['accepted']).lower()}\n"
        f"- Approval state: `{report['approval_state']}`\n\n"
        f"{limitations_line}\n"
        f"Promotion state: `{report['promotion_state']}`. Automatic promotion: "
        f"{str(report['automatic_promotion']).lower()}.\n"
    )


def _render_case_study_readme(report: dict[str, Any]) -> str:
    refs = _rollout_refs(report)
    limitations = _report_list_field(report, "limitations_json")
    baseline_ref = refs["baseline"][0] if refs["baseline"] else "none"
    candidate_ref = refs["candidate"][0] if refs["candidate"] else "none"
    limitations_text = ", ".join(f"`{item}`" for item in limitations) or "none"
    return (
        "# CI Echo Skill Intelligence Case Study\n\n"
        f"Selected skill: `{report['skill_id']}`\n\n"
        f"Baseline digest: `{report['baseline_digest']}`\n\n"
        f"Candidate digest: `{report['candidate_digest']}`\n\n"
        f"Split counts: Dtrain={report['dtrain_count']}, "
        f"Dsel={report['dsel_count']}, Dtest={report['dtest_count']}.\n\n"
        f"Dsel gate: {report['dsel_before']:.3f} -> {report['dsel_after']:.3f}.\n\n"
        f"Held-out Dtest score: {report['dtest_score']:.3f}.\n\n"
        f"Rollout refs: baseline={len(refs['baseline'])} first `{baseline_ref}`; "
        f"candidate={len(refs['candidate'])} first `{candidate_ref}`.\n\n"
        f"Limitations: {limitations_text}.\n\n"
        f"Approval state: `{report['approval_state']}`. Promotion state: "
        f"`{report['promotion_state']}`. Automatic promotion: "
        f"{str(report['automatic_promotion']).lower()}.\n"
    )


def _rollout_refs(report: dict[str, Any]) -> dict[str, list[str]]:
    try:
        parsed = json.loads(str(report.get("rollout_refs_json") or "{}"))
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    return {
        "baseline": [
            str(item) for item in parsed.get("baseline", [])
            if isinstance(item, str)
        ],
        "candidate": [
            str(item) for item in parsed.get("candidate", [])
            if isinstance(item, str)
        ],
    }


def _report_list_field(report: dict[str, Any], field: str) -> list[str]:
    try:
        parsed = json.loads(str(report.get(field) or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def build_eval_tasks_from_file(source: Path, output: Path, *, seed: str) -> list[dict[str, Any]]:
    rows = build_eval_task_rows(_read_jsonl(source), seed=seed)
    _write_jsonl(output, rows)
    return rows


def build_rollouts_from_file(
    source: Path,
    output: Path,
    *,
    skill_path: Path | None = None,
    skill_text: str | None = None,
) -> list[dict[str, Any]]:
    text = skill_text
    if text is None and skill_path is not None:
        text = skill_path.read_text(encoding="utf-8")
    if text is None:
        text = DEFAULT_INITIAL_SKILL
    rows = build_rollout_rows(
        _read_jsonl(source),
        skill_text=text,
        skill_digest=_digest_text(text),
        out_dir=output.parent,
    )
    _write_jsonl(output, rows)
    return rows
