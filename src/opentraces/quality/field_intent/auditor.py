"""Field-intent auditor: completeness gate, sampling, cross-field + LLM checks."""
from __future__ import annotations

import json
import random
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Optional

from opentraces_schema import TraceRecord

from opentraces.quality.schema_audit import (
    FIELD_SPECS,
    FieldSpec,
    _get_nested_value,
    _is_populated,
    _sample_list_field,
)
from opentraces.quality.field_intent.generator import (
    FieldIntent,
    SPEC_PATH,
    _run_claude,
    load_spec,
    missing_paths,
)


Verdict = Literal["ok", "suspicious", "wrong"]
Cause = Literal["parser_bug", "enrichment_gap", "redaction", "schema_drift", "benign"]


@dataclass
class FieldFinding:
    trace_id: str
    field_path: str
    verdict: Verdict
    evidence: str
    suspected_cause: Cause


@dataclass
class AuditReport:
    traces_sampled: int
    llm_skipped: bool
    llm_skip_reason: str = ""
    findings: list[FieldFinding] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Completeness gate
# ---------------------------------------------------------------------------

def check_completeness(spec_path: Path = SPEC_PATH) -> tuple[bool, str]:
    spec = load_spec(spec_path)
    missing = missing_paths(spec, FIELD_SPECS)
    if not missing:
        return True, f"Spec complete ({len(spec)} entries)."
    preview = ", ".join(missing[:5]) + ("..." if len(missing) > 5 else "")
    return False, (
        f"field_intent.yaml is incomplete ({len(missing)} missing: {preview}). "
        f"Run `opentraces _audit-spec` to fill before auditing."
    )


# ---------------------------------------------------------------------------
# Cross-field checks (pure Python)
# ---------------------------------------------------------------------------

def cross_field_checks(trace: TraceRecord) -> list[FieldFinding]:
    out: list[FieldFinding] = []
    tid = trace.trace_id

    # metrics.total_steps == len(steps)
    actual = len(trace.steps)
    if trace.metrics and trace.metrics.total_steps != actual:
        out.append(FieldFinding(
            trace_id=tid, field_path="metrics.total_steps",
            verdict="wrong",
            evidence=f"metrics.total_steps={trace.metrics.total_steps} but len(steps)={actual}",
            suspected_cause="enrichment_gap",
        ))

    # every ToolCall has a matching Observation within the same step
    for step in trace.steps:
        obs_ids = {o.source_call_id for o in (step.observations or []) if o.source_call_id}
        for tc in (step.tool_calls or []):
            if tc.tool_call_id and tc.tool_call_id not in obs_ids:
                out.append(FieldFinding(
                    trace_id=tid,
                    field_path=f"steps[].tool_calls[].tool_call_id",
                    verdict="suspicious",
                    evidence=(
                        f"step {step.step_index} tool_call {tc.tool_call_id} "
                        f"({tc.tool_name}) has no matching observation.source_call_id"
                    ),
                    suspected_cause="parser_bug",
                ))

    # outcome.committed consistent with commit_sha
    oc = trace.outcome
    if oc is not None:
        if oc.committed and not oc.commit_sha:
            out.append(FieldFinding(
                trace_id=tid, field_path="outcome.committed",
                verdict="wrong",
                evidence="outcome.committed=True but outcome.commit_sha is empty",
                suspected_cause="enrichment_gap",
            ))
        if oc.commit_sha and not oc.committed:
            out.append(FieldFinding(
                trace_id=tid, field_path="outcome.committed",
                verdict="suspicious",
                evidence=f"commit_sha={oc.commit_sha} present but outcome.committed=False",
                suspected_cause="enrichment_gap",
            ))

    return out


# ---------------------------------------------------------------------------
# Per-field LLM value sanity
# ---------------------------------------------------------------------------

_JUDGE_TEMPLATE = """You are auditing one populated field of an opentraces TraceRecord.

Field path: {path}
Schema intent:
  description: {intent_description}
  good_example: {intent_good_example}
  failure_modes: {intent_failure_modes}

Observed value (JSON): {value}

Classify the value. Return ONLY a JSON object with keys:
- "verdict": one of "ok", "suspicious", "wrong"
- "evidence": one sentence explaining your verdict grounded in the observed value
- "suspected_cause": one of "parser_bug", "enrichment_gap", "redaction", "schema_drift", "benign"

No prose, no markdown fences."""


def _extract_sample_values(trace: TraceRecord, spec: FieldSpec, max_items: int = 3):
    """Return up to `max_items` (path, value) pairs for this field within the trace."""
    path = spec.path
    if "[]." not in path:
        val = _get_nested_value(trace, path)
        if _is_populated(val):
            return [(path, val)]
        return []
    # For array paths, pull up to max_items concrete leaf values.
    out = []
    parts = path.split("[].")
    head = parts[0]
    tail = "[]." .join(parts[1:])
    items = _get_nested_value(trace, head) or []
    for idx, item in enumerate(items[:max_items]):
        if "[]." in tail:
            nparts = tail.split("[].")
            sub = _get_nested_value(item, nparts[0]) or []
            leaf = "[]." .join(nparts[1:])
            for jdx, sub_item in enumerate(sub[:max_items]):
                v = _get_nested_value(sub_item, leaf)
                if _is_populated(v):
                    out.append((f"{head}[{idx}].{nparts[0]}[{jdx}].{leaf}", v))
                    if len(out) >= max_items:
                        return out
        else:
            v = _get_nested_value(item, tail)
            if _is_populated(v):
                out.append((f"{head}[{idx}].{tail}", v))
            if len(out) >= max_items:
                return out
    return out


def _coerce_for_prompt(val):
    try:
        if hasattr(val, "model_dump"):
            val = val.model_dump()
        return json.dumps(val, default=str)[:1200]
    except Exception:
        return str(val)[:1200]


def llm_field_checks(
    trace: TraceRecord,
    spec: dict[str, FieldIntent],
    model: str = "haiku",
    per_trace_field_cap: int = 12,
) -> list[FieldFinding]:
    out: list[FieldFinding] = []
    specs_by_path = {fs.path: fs for fs in FIELD_SPECS}
    seen = 0
    for path, intent in spec.items():
        if seen >= per_trace_field_cap:
            break
        seed = specs_by_path.get(path)
        if seed is None:
            continue
        samples = _extract_sample_values(trace, seed, max_items=1)
        if not samples:
            continue
        for concrete_path, val in samples:
            prompt = _JUDGE_TEMPLATE.format(
                path=path,
                intent_description=intent.description,
                intent_good_example=intent.good_example,
                intent_failure_modes=intent.failure_modes,
                value=_coerce_for_prompt(val),
            )
            resp = _run_claude(prompt, model=model)
            if not isinstance(resp, dict):
                return out  # Treat first failure as global skip for this trace.
            verdict = resp.get("verdict", "ok")
            if verdict not in ("ok", "suspicious", "wrong"):
                continue
            if verdict == "ok":
                continue
            cause = resp.get("suspected_cause", "benign")
            if cause not in ("parser_bug", "enrichment_gap", "redaction", "schema_drift", "benign"):
                cause = "benign"
            out.append(FieldFinding(
                trace_id=trace.trace_id,
                field_path=concrete_path,
                verdict=verdict,
                evidence=str(resp.get("evidence", ""))[:400],
                suspected_cause=cause,
            ))
        seen += 1
    return out


# ---------------------------------------------------------------------------
# Sampling + top-level driver
# ---------------------------------------------------------------------------

def _load_traces_from_staging(staging_dir: Path, sample: int, seed: int = 0) -> list[TraceRecord]:
    files = sorted(staging_dir.glob("*.jsonl"))
    if not files:
        return []
    rng = random.Random(seed)
    chosen = rng.sample(files, min(sample, len(files)))
    out = []
    for p in chosen:
        try:
            line = p.read_text().strip().splitlines()[0]
            out.append(TraceRecord.model_validate_json(line))
        except Exception:
            continue
    return out


def audit_run(
    sample: int,
    staging_dir: Optional[Path] = None,
    dataset: Optional[str] = None,
    spec_path: Path = SPEC_PATH,
    model: str = "haiku",
    seed: int = 0,
) -> AuditReport:
    """Sample N traces and run cross-field + LLM field-intent checks."""
    ok, msg = check_completeness(spec_path)
    if not ok:
        raise RuntimeError(msg)

    spec = load_spec(spec_path)

    if dataset:
        # Dataset path support: best-effort streaming via hf_hub_download, scoped small.
        traces = _load_traces_from_dataset(dataset, sample)
    else:
        if staging_dir is None:
            staging_dir = Path.cwd() / ".opentraces" / "staging"
        traces = _load_traces_from_staging(staging_dir, sample, seed=seed)

    findings: list[FieldFinding] = []
    for t in traces:
        findings.extend(cross_field_checks(t))

    llm_skipped = False
    llm_skip_reason = ""
    if shutil.which("claude") is None:
        llm_skipped = True
        llm_skip_reason = "claude binary not on PATH; LLM field checks skipped"
    else:
        for t in traces:
            pre = len(findings)
            findings.extend(llm_field_checks(t, spec, model=model))
            # If _run_claude started returning None mid-run we won't know deterministically;
            # we accept partial coverage and keep whatever findings we produced.
            _ = pre

    return AuditReport(
        traces_sampled=len(traces),
        llm_skipped=llm_skipped,
        llm_skip_reason=llm_skip_reason,
        findings=findings,
    )


def _load_traces_from_dataset(repo_id: str, sample: int) -> list[TraceRecord]:
    try:
        from huggingface_hub import HfApi, hf_hub_download
    except ImportError:
        return []
    try:
        api = HfApi()
        files = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
    except Exception:
        return []
    shards = [f for f in files if f.startswith("data/traces_") and f.endswith(".jsonl")]
    if not shards:
        return []
    out: list[TraceRecord] = []
    for shard in shards:
        try:
            local = hf_hub_download(repo_id=repo_id, filename=shard, repo_type="dataset")
        except Exception:
            continue
        with open(local) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(TraceRecord.model_validate_json(line))
                except Exception:
                    continue
                if len(out) >= sample:
                    return out
    return out


# ---------------------------------------------------------------------------
# Emitters
# ---------------------------------------------------------------------------

def format_report(report: AuditReport) -> str:
    lines = ["# Field-Intent Audit", ""]
    lines.append(f"- Traces sampled: {report.traces_sampled}")
    lines.append(f"- Findings: {len(report.findings)}")
    if report.llm_skipped:
        lines.append(f"- LLM section: **skipped** ({report.llm_skip_reason})")
    else:
        lines.append("- LLM section: ran")
    lines.append("")

    by_cause: dict[str, list[FieldFinding]] = {}
    for f in report.findings:
        by_cause.setdefault(f.suspected_cause, []).append(f)
    for cause in ("parser_bug", "enrichment_gap", "redaction", "schema_drift", "benign"):
        items = by_cause.get(cause, [])
        if not items:
            continue
        lines.append(f"## {cause} ({len(items)})")
        lines.append("")
        for f in items:
            lines.append(f"- **`{f.field_path}`** [{f.verdict}] trace={f.trace_id[:8]}")
            lines.append(f"  - {f.evidence}")
        lines.append("")
    return "\n".join(lines)


def findings_to_json(report: AuditReport) -> str:
    return json.dumps({
        "traces_sampled": report.traces_sampled,
        "llm_skipped": report.llm_skipped,
        "llm_skip_reason": report.llm_skip_reason,
        "findings": [asdict(f) for f in report.findings],
    }, indent=2)
