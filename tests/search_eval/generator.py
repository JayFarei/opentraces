"""U1 - Deterministic planting generator.

Given ``(profile, seed, tier)`` this produces, byte-deterministically:

* a **corpus** of synthetic ``TraceRecord`` rows sized to the tier, and
* a **query set** whose every row carries the *exact planted gold* (the
  structure the archetype probes) plus its documented ``expected_phase_a``
  (green/red), so the eval's known gaps become *checked* invariants.

Plant-and-record (Decision Audit #2): the generator deliberately plants the
structures each archetype probes and emits the gold as a byproduct, so no
hand-labelling or LLM judgement is needed.

The three archetypes (+ supporting cases) and what they probe:

  chronological   K distinct traces sharing a topic at increasing times.
                  gold = the time-ordered set.   metric = Kendall tau (R5).
                  Phase A: RED (no --sort time; results are score-only).
  recency         K distinct traces on a topic at increasing times.
                  gold = the latest.             metric = recency-hit@1 (R6).
                  Phase A: RED (no recency weighting).
  reference-bare  one rare *indexable* needle token in one old trace.
                  gold = that trace.             metric = recall@10 / MRR (R4).
                  Phase A: GREEN (proves the recall scorer works).
  reference-id    one *hyphenated identifier* needle (S3/S4 analog).
                  gold = that trace.             metric = recall@10.
                  Phase A: RED (FTS unicode61 splits 'pi-x' -> total=0).
  facet           several traces touching one file (S8).
                  gold = that set.               metric = recall + orderability.
                  Phase A: recall GREEN, time-order RED (facets unordered).
  descriptive     a distinctive natural-language task phrase (S6/S7).
                  gold = that trace.             metric = top-3 rank.
                  Phase A: GREEN (descriptive lexical works).
  superseded      a 3-generation session; gold = an *older* generation,
                  reachable only with --include-superseded (R4 tail).

Determinism is structural: ordinal IDs and hash-derived sampling only - no
``random`` module, no wall-clock. The content-addressed snapshot key is
``sha256(profile_digest + seed + tier + generator_version + code_version)``.

Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .profiler import canonical_json, profile_digest

GENERATOR_VERSION = "1"
PROFILE_PATH_DEFAULT = Path(__file__).resolve().parent / "real-bucket-profile.json"
EPOCH = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
_SCHEMA_VERSION = "0.5.0"

# How many gold-bearing scenarios of each archetype to plant. These are FIXED
# across tiers (the gold structures are constant); only filler scales, so the
# needle stays genuinely rare and the scaling-slope test (R2) is honest.
SCENARIOS = {
    "chronological": 3,   # K=4 traces each
    "recency": 3,         # K=4 traces each
    "reference_bare": 3,
    "reference_id": 3,
    "facet": 2,           # K=4 traces each touch the file
    "descriptive": 4,
    "superseded": 2,      # 3-generation session each
}
CHRONO_K = 4
RECENCY_K = 4
FACET_K = 4
SUPERSEDE_GENS = 3

TIER_TRACE_COUNT = {
    "dev": 150,
    "real-scale": None,   # filled from profile.trace_count
    "xl": 10000,
}

# A small deterministic vocabulary for realistic filler / TF-IDF background.
_FILLER_TOPICS = [
    "refactor parser module", "fix flaky integration test", "add cli flag",
    "update dependency lockfile", "investigate memory regression",
    "write migration script", "improve error messages", "tune cache layer",
    "document the api surface", "rename internal helper", "harden auth flow",
    "optimise the build step", "wire up the webhook", "backfill the index",
    "patch the race condition", "add retry with backoff", "split the monolith",
    "clean up dead code", "validate the schema", "instrument the hot path",
]
_FILE_DIRS = ["src/core", "src/cli", "pkg/util", "lib/io", "app/handlers"]


# --------------------------------------------------------------------------- #
# deterministic helpers (no random module, no wall-clock)
# --------------------------------------------------------------------------- #
def _u(*parts: Any) -> float:
    """Deterministic uniform in [0, 1) from the parts (stable across machines)."""

    key = ":".join(str(p) for p in parts)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:16], 16) / float(1 << 64)


def _pick(seq: list[Any], *parts: Any) -> Any:
    return seq[int(_u(*parts) * len(seq)) % len(seq)]


def _inverse_cdf(dist: dict[str, Any], u: float) -> int:
    """Piecewise-linear inverse CDF over the profile percentile breakpoints."""

    pts = [
        (0.0, float(dist.get("min", 1))),
        (0.25, float(dist.get("p25", 1))),
        (0.50, float(dist.get("p50", 1))),
        (0.75, float(dist.get("p75", 1))),
        (0.90, float(dist.get("p90", 1))),
        (0.99, float(dist.get("p99", 1))),
        (1.0, float(dist.get("max", 1))),
    ]
    u = min(max(u, 0.0), 0.999999)
    for (q0, v0), (q1, v1) in zip(pts, pts[1:]):
        if q0 <= u < q1 or q1 == 1.0 and u <= q1:
            frac = (u - q0) / (q1 - q0) if q1 > q0 else 0.0
            return max(1, int(round(v0 + frac * (v1 - v0))))
    return max(1, int(pts[-1][1]))


def _ts(ordinal: int) -> str:
    return (EPOCH + timedelta(minutes=ordinal * 7)).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# data model
# --------------------------------------------------------------------------- #
@dataclass
class TraceSpec:
    trace_id: str
    session_id: str
    generation_index: int
    preceded_by: str | None
    ordinal: int          # global emission order -> timestamp; also intended time
    description: str
    body_tokens: list[str]      # extra tokens woven into step content
    files: list[str]            # file paths touched via tool calls
    step_count: int


@dataclass
class QueryRow:
    id: str
    archetype: str
    intent: str
    mode: str                   # "lex" | "semantic" | "files"
    query: str
    extra_flags: dict[str, Any] = field(default_factory=dict)
    gold_trace_ids: list[str] = field(default_factory=list)
    gold_kind: str = "single"   # "single" | "set" | "ordered"
    gold_latest: str | None = None
    targets: dict[str, Any] = field(default_factory=dict)
    expected_phase_a: str = "green"   # "green" | "red"
    seed_case: str | None = None
    note: str = ""


@dataclass
class CorpusPlan:
    tier: str
    seed: int
    snapshot_key: str
    generator_version: str
    code_version: str
    profile_digest: str
    traces: list[TraceSpec]
    queries: list[QueryRow]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "seed": self.seed,
            "snapshot_key": self.snapshot_key,
            "generator_version": self.generator_version,
            "code_version": self.code_version,
            "profile_digest": self.profile_digest,
            "trace_count": len(self.traces),
            "query_count": len(self.queries),
            "queries": [asdict(q) for q in self.queries],
        }


# --------------------------------------------------------------------------- #
# code_version + snapshot key
# --------------------------------------------------------------------------- #
def code_version() -> str:
    """sha256 over this generator's own source (changes only when it changes)."""

    src = Path(__file__).read_bytes()
    return f"sha256:{hashlib.sha256(src).hexdigest()[:16]}"


def snapshot_key(prof_digest: str, seed: int, tier: str) -> str:
    payload = {
        "profile_digest": prof_digest,
        "seed": seed,
        "tier": tier,
        "generator_version": GENERATOR_VERSION,
        "code_version": code_version(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


# --------------------------------------------------------------------------- #
# the planner (pure, deterministic)
# --------------------------------------------------------------------------- #
def plan_corpus(profile: dict[str, Any], seed: int, tier: str) -> CorpusPlan:
    if tier not in TIER_TRACE_COUNT:
        raise ValueError(f"unknown tier {tier!r}; choose from {sorted(TIER_TRACE_COUNT)}")
    prof = profile.get("profile", profile)
    target_count = TIER_TRACE_COUNT[tier]
    if target_count is None:
        target_count = int(prof.get("trace_count", 1084))
    steps_dist = prof.get("steps_per_trace", {"min": 1, "p50": 24, "max": 200})

    traces: list[TraceSpec] = []
    queries: list[QueryRow] = []
    ordinal = 0

    def _next_ordinal() -> int:
        nonlocal ordinal
        ordinal += 1
        return ordinal

    # ---- chronological: K distinct traces sharing a topic at rising times --- #
    for c in range(SCENARIOS["chronological"]):
        token = f"chronotopic{seed}{c:02d}"
        members: list[str] = []
        for k in range(CHRONO_K):
            o = _next_ordinal()
            tid = f"eval-{tier}-chrono-{c:02d}-{k:02d}"
            traces.append(TraceSpec(
                trace_id=tid, session_id=f"sess-chrono-{c:02d}-{k:02d}",
                generation_index=0, preceded_by=None, ordinal=o,
                description=f"work on {token} milestone {k}",
                body_tokens=[token, f"milestone {k}"], files=[], step_count=10,
            ))
            members.append(tid)
        queries.append(QueryRow(
            id=f"chrono-{c:02d}", archetype="chronological",
            intent="what happened around this topic, in time order",
            mode="lex", query=token,
            gold_trace_ids=list(members), gold_kind="ordered",
            targets={"recall_at": 10, "recall_min": 0.95, "tau_min": 0.9},
            expected_phase_a="red",
            seed_case="S4" if c == 0 else None,
            note="time order requires --sort time (U4); score-only today",
        ))

    # ---- recency: K distinct traces on a topic; gold = the latest ----------- #
    for r in range(SCENARIOS["recency"]):
        token = f"recencytopic{seed}{r:02d}"
        members = []
        for k in range(RECENCY_K):
            o = _next_ordinal()
            tid = f"eval-{tier}-recency-{r:02d}-{k:02d}"
            traces.append(TraceSpec(
                trace_id=tid, session_id=f"sess-recency-{r:02d}-{k:02d}",
                generation_index=0, preceded_by=None, ordinal=o,
                description=f"latest work on the {token} stack as tools, pass {k}",
                body_tokens=[token, "security stack tools"], files=[], step_count=12,
            ))
            members.append(tid)
        latest = members[-1]
        queries.append(QueryRow(
            id=f"recency-{r:02d}", archetype="recency",
            intent="the latest work on this topic, reload its context",
            mode="semantic", query=f"latest work on the {token} stack",
            gold_trace_ids=list(members), gold_kind="set", gold_latest=latest,
            targets={"recall_at": 10, "recall_min": 0.95, "recency_hit_at": 1},
            expected_phase_a="red",
            seed_case="S5" if r == 0 else None,
            note="latest-at-rank-1 requires recency weighting (U5)",
        ))

    # ---- reference (bare, indexable needle) -> GREEN ------------------------ #
    for n in range(SCENARIOS["reference_bare"]):
        needle = f"znqxneedle{seed}{n:02d}"
        o = _next_ordinal()
        tid = f"eval-{tier}-refbare-{n:02d}"
        traces.append(TraceSpec(
            trace_id=tid, session_id=f"sess-refbare-{n:02d}",
            generation_index=0, preceded_by=None, ordinal=o,
            description=f"have we ever handled the {needle} edge case before",
            body_tokens=[needle], files=[], step_count=8,
        ))
        queries.append(QueryRow(
            id=f"refbare-{n:02d}", archetype="reference_bare",
            intent="have we done this before (rare indexable needle)",
            mode="lex", query=needle,
            gold_trace_ids=[tid], gold_kind="single",
            targets={"recall_at": 10, "recall_min": 0.95, "mrr_min": 0.8},
            expected_phase_a="green",
            note="bare token indexes fine; proves the recall scorer",
        ))

    # ---- reference (URL/identifier needle, S3/S4) -> RED -------------------- #
    # Faithful to the live S3 mechanism (verified 2026-05-31): the needle lives
    # ONLY inside a URL ("install this https://github.com/<owner>/<repo>"); the
    # tokenizer swallows the URL into an unreachable compound, so the bare
    # identifier query returns total=0 on both the index and projection paths.
    # The fix (URL/identifier sub-tokenization) folds into the capability phase.
    for n in range(SCENARIOS["reference_id"]):
        owner = f"nico{seed}{n:02d}"           # bare-username analog ('nicobailon')
        repo = f"pi-sub-{seed}-{n:02d}"         # hyphenated-identifier analog ('pi-subagents')
        url = f"https://github.com/{owner}/{repo}"
        o = _next_ordinal()
        tid = f"eval-{tier}-refid-{n:02d}"
        traces.append(TraceSpec(
            trace_id=tid, session_id=f"sess-refid-{n:02d}",
            generation_index=0, preceded_by=None, ordinal=o,
            description=f"install this {url}",   # needle appears ONLY inside the URL
            body_tokens=[f"install this {url}"], files=[], step_count=8,
        ))
        queries.append(QueryRow(
            id=f"refid-{n:02d}", archetype="reference_id",
            intent="find by identifier / URL token (S3/S4 needle)",
            mode="lex", query=owner,             # bare segment -> unreachable today
            gold_trace_ids=[tid], gold_kind="single",
            targets={"recall_at": 10, "recall_min": 0.95, "mrr_min": 0.8},
            expected_phase_a="red",
            seed_case="S3" if n == 0 else None,
            note="needle only inside a URL compound; bare query unreachable -> total=0",
        ))

    # ---- facet (file-touch set, S8) ----------------------------------------- #
    for f_idx in range(SCENARIOS["facet"]):
        fpath = f"src/core/intent-{seed}-{f_idx:02d}.py"
        members = []
        for k in range(FACET_K):
            o = _next_ordinal()
            tid = f"eval-{tier}-facet-{f_idx:02d}-{k:02d}"
            traces.append(TraceSpec(
                trace_id=tid, session_id=f"sess-facet-{f_idx:02d}-{k:02d}",
                generation_index=0, preceded_by=None, ordinal=o,
                description=f"edit the intent module pass {k}",
                body_tokens=[f"intent module {k}"], files=[fpath], step_count=10,
            ))
            members.append(tid)
        queries.append(QueryRow(
            id=f"facet-{f_idx:02d}", archetype="facet",
            intent="which traces touched this file (reuse)",
            mode="files", query=f"*{fpath}",
            gold_trace_ids=list(members), gold_kind="set",
            gold_latest=members[-1],
            targets={"recall_at": 20, "recall_min": 0.95, "orderable": True},
            expected_phase_a="green",
            seed_case="S8" if f_idx == 0 else None,
            note="recall green; time-ordering of the set is RED (facets unordered)",
        ))

    # ---- descriptive natural-language phrase (S6/S7) -> GREEN --------------- #
    _phrases = [
        ("how many lines of code in this project", "S7"),
        ("does our cli allow fast search over previous traces", "S6"),
        ("do we know how to slice a trajectory per intent", "S1"),
        ("summarise the security pipeline as a tool registry", None),
    ]
    for d in range(SCENARIOS["descriptive"]):
        phrase, sc = _phrases[d % len(_phrases)]
        marker = f"descmarker{seed}{d:02d}"
        o = _next_ordinal()
        tid = f"eval-{tier}-desc-{d:02d}"
        traces.append(TraceSpec(
            trace_id=tid, session_id=f"sess-desc-{d:02d}",
            generation_index=0, preceded_by=None, ordinal=o,
            description=f"{phrase} ({marker})",
            body_tokens=[marker], files=[], step_count=8,
        ))
        queries.append(QueryRow(
            id=f"desc-{d:02d}", archetype="descriptive",
            intent="re-find by remembered descriptive phrase",
            mode="lex", query=phrase,
            gold_trace_ids=[tid], gold_kind="single",
            targets={"recall_at": 10, "recall_min": 0.95, "top_k_rank": 3},
            expected_phase_a="green", seed_case=sc,
            note="descriptive lexical recall must be preserved",
        ))

    # ---- superseded needle (R4 tail): older generation, --include-superseded  #
    for s in range(SCENARIOS["superseded"]):
        needle = f"supersededneedle{seed}{s:02d}"
        session = f"sess-supersede-{s:02d}"
        prev: str | None = None
        gens: list[str] = []
        for g in range(SUPERSEDE_GENS):
            o = _next_ordinal()
            tid = f"eval-{tier}-supersede-{s:02d}-g{g}"
            # plant the needle only in the OLDEST generation (g == 0)
            tokens = [needle] if g == 0 else [f"supersede iteration {g}"]
            traces.append(TraceSpec(
                trace_id=tid, session_id=session,
                generation_index=g, preceded_by=prev, ordinal=o,
                description=(f"first attempt at {needle}" if g == 0
                             else f"superseding attempt {g} of the same task"),
                body_tokens=tokens, files=[], step_count=8,
            ))
            prev = tid
            gens.append(tid)
        queries.append(QueryRow(
            id=f"supersede-{s:02d}", archetype="superseded",
            intent="recall a superseded older generation",
            mode="lex", query=needle,
            extra_flags={"include_superseded": True},
            gold_trace_ids=[gens[0]], gold_kind="single",
            targets={"recall_at": 10, "recall_min": 0.95, "mrr_min": 0.8},
            expected_phase_a="green",
            note="needle lives in the oldest gen; requires --include-superseded",
        ))

    planted = len(traces)

    # ---- filler to reach the tier size (realistic TF-IDF background) -------- #
    f_idx = 0
    while len(traces) < target_count:
        o = _next_ordinal()
        topic = _pick(_FILLER_TOPICS, seed, "filler", f_idx)
        n_files = 1 if _u(seed, "ffile", f_idx) < 0.4 else 0
        files = []
        if n_files:
            d = _pick(_FILE_DIRS, seed, "fdir", f_idx)
            files = [f"{d}/mod_{f_idx % 37}.py"]
        sc = _inverse_cdf(steps_dist, _u(seed, "fsteps", f_idx))
        sc = min(sc, 2600)  # guard the long tail
        traces.append(TraceSpec(
            trace_id=f"eval-{tier}-filler-{f_idx:06d}",
            session_id=f"sess-filler-{f_idx:06d}",
            generation_index=0, preceded_by=None, ordinal=o,
            description=f"{topic} item {f_idx}",
            body_tokens=[topic], files=files, step_count=sc,
        ))
        f_idx += 1

    prof_digest = profile_digest(profile)
    key = snapshot_key(prof_digest, seed, tier)
    return CorpusPlan(
        tier=tier, seed=seed, snapshot_key=key,
        generator_version=GENERATOR_VERSION, code_version=code_version(),
        profile_digest=prof_digest, traces=traces, queries=queries,
    )


# --------------------------------------------------------------------------- #
# TraceRecord builder (one staging JSONL dict per TraceSpec)
# --------------------------------------------------------------------------- #
def build_trace_record(spec: TraceSpec) -> dict[str, Any]:
    base = spec.ordinal
    steps: list[dict[str, Any]] = []
    woven = " ".join(spec.body_tokens)
    for i in range(spec.step_count):
        ts = _ts(base) if i == 0 else _ts_step(base, i)
        if i % 2 == 0:
            steps.append({
                "step_index": i + 1, "role": "user",
                "content": f"{spec.description}. {woven} step {i}",
                "timestamp": ts,
            })
            continue
        call_id = f"{spec.trace_id}-call-{i:04d}"
        # cycle planted files into tool calls so --files facets match
        fpath = spec.files[i % len(spec.files)] if spec.files else f"pkg/file_{i % 5}.py"
        steps.append({
            "step_index": i + 1, "role": "agent",
            "content": f"agent on {spec.description} :: {woven} step {i}",
            "timestamp": ts,
            "tool_calls": [{
                "tool_call_id": call_id,
                "tool_name": "Edit" if spec.files and i % 3 == 1 else "Read",
                "input": {"file_path": fpath, "path": fpath},
            }],
            "observations": [{
                "source_call_id": call_id, "content": f"observation {i}",
                "output_summary": "ok",
            }],
        })
    record: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "trace_id": spec.trace_id,
        "session_id": spec.session_id,
        "generation_index": spec.generation_index,
        "timestamp_start": _ts(base),
        "timestamp_end": _ts_step(base, max(1, spec.step_count)),
        "agent": {"name": "claude-code", "model": "anthropic/claude-opus-4-8"},
        "task": {"description": spec.description},
        "metrics": {
            "total_steps": len(steps),
            "total_input_tokens": len(steps) * 120,
            "total_output_tokens": len(steps) * 60,
        },
        "steps": steps,
    }
    if spec.preceded_by:
        record["preceded_by"] = spec.preceded_by
    return record


def _ts_step(base: int, i: int) -> str:
    # sub-ordinal step times stay within this trace's 7-minute window
    return (EPOCH + timedelta(minutes=base * 7, seconds=i * 5)).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# materialiser (writes to a project staging dir + state; reuses opentraces APIs)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MaterializedCorpus:
    home_dir: Path
    project_dir: Path
    project_slug: str
    staging_dir: Path
    plan: CorpusPlan


def materialize_corpus(plan: CorpusPlan, base_dir: Path, home_dir: Path) -> MaterializedCorpus:
    """Write the planned corpus into a project staging dir under ``home_dir``."""

    # reuse the perf harness' home-configuration (single source of the
    # process-global path mutation the index/projection read from).
    from tests.perf.harness.fixtures import _configure_opentraces_home
    from opentraces.core.config import (
        get_project_state_path,
        get_project_traces_dir,
        save_project_config,
    )
    from opentraces.core.state import StateManager, TraceStatus

    _configure_opentraces_home(home_dir)
    project_dir = base_dir / "proj"
    project_dir.mkdir(parents=True, exist_ok=True)
    save_project_config(
        project_dir,
        {
            "review_policy": "review",
            "push_policy": "manual",
            "agents": ["claude-code"],
        },
    )
    _git_init(project_dir)

    staging_dir = get_project_traces_dir(project_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    state = StateManager(state_path=get_project_state_path(project_dir))

    for spec in plan.traces:
        record = build_trace_record(spec)
        path = staging_dir / f"{spec.trace_id}.jsonl"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        state.set_trace_status(
            spec.trace_id,
            TraceStatus.PARSED,
            session_id=spec.session_id,
            file_path=str(path),
        )

    return MaterializedCorpus(
        home_dir=home_dir, project_dir=project_dir,
        project_slug=project_dir.name, staging_dir=staging_dir, plan=plan,
    )


def _git_init(project_dir: Path) -> None:
    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=str(project_dir), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if (project_dir / ".git").exists():
        return
    run("init", "-q", "-b", "main")
    run("config", "user.email", "seval@opentraces.ai")
    run("config", "user.name", "SearchEval")
    (project_dir / "README.md").write_text("# search-eval corpus\n")
    run("add", ".")
    run("-c", "user.email=seval@opentraces.ai", "-c", "user.name=SearchEval",
        "commit", "-q", "-m", "init")


# --------------------------------------------------------------------------- #
# profile loading + CLI
# --------------------------------------------------------------------------- #
def load_profile(path: Path = PROFILE_PATH_DEFAULT) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plant a deterministic eval corpus (U1).")
    parser.add_argument("--tier", default="dev", choices=sorted(TIER_TRACE_COUNT))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--profile", type=Path, default=PROFILE_PATH_DEFAULT)
    parser.add_argument("--out-manifest", type=Path, default=None,
                        help="write the query-set + gold manifest JSON here")
    args = parser.parse_args(argv)

    profile = load_profile(args.profile)
    plan = plan_corpus(profile, args.seed, args.tier)
    manifest = plan.to_manifest()
    if args.out_manifest:
        args.out_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.out_manifest.write_text(canonical_json(manifest), encoding="utf-8")
    counts: dict[str, int] = {}
    for q in plan.queries:
        counts[q.archetype] = counts.get(q.archetype, 0) + 1
    red = sum(1 for q in plan.queries if q.expected_phase_a == "red")
    print(
        f"tier={plan.tier} seed={plan.seed} traces={len(plan.traces)} "
        f"queries={len(plan.queries)} (red={red}) key={plan.snapshot_key[:23]}…"
    )
    print("  archetypes:", json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
