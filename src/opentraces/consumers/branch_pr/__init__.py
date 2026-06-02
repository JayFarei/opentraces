"""Branch-context consumer of the workflow runtime.

This is the first non-dataset consumer of the workflow language. It composes
a branch scope (base, head, commits) and runs a workflow against it via
:func:`opentraces.core.workflow_runner.execute_workflow`. The workflow's
output JSONL rows are then rendered for one specific destination (a GitHub
PR body, by way of :func:`render_pr_markdown` + the ``gh_*`` helpers).

Other consumers — Slack, dashboards, CI checks — would live in their own
modules using the same execute_workflow primitive but a different scope
shape and a different renderer. This file is intentionally PR-specific
on the rendering side; the workflow it drives (``pr-intent-summary-v1``)
is reusable.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from opentraces.core import paths as ot_paths
from opentraces.core.config import _project_slug_for, load_config
from opentraces.core.text_redaction import redact_index_text
from opentraces.core.workflow_runner import (
    WorkflowExecutionResult,
    WorkflowScriptError,
    execute_workflow,
)
from opentraces.enrichment.git.attribution import git as _git_run


# Stable ``no_lineage_reason`` codes — the cross-process string contract
# between the pr-intent-summary-v1 workflow's build_rows.py (producer) and
# this module's renderer (consumer). Keep both sides on these constants.
NO_LINEAGE_MERGE_COMMIT = "merge_commit"
NO_LINEAGE_NO_ATTRIBUTION = "no_attribution"
NO_LINEAGE_NO_BURST = "no_burst"
NO_LINEAGE_NO_TRACES = "no_traces_in_bucket"


# ---------------------------------------------------------------------------
# Branch walking
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    short_sha: str
    subject: str
    body: str
    parents: list[str]

    @property
    def is_merge(self) -> bool:
        return len(self.parents) >= 2


@dataclass(frozen=True)
class BranchScope:
    project_dir: Path
    project_slug: str
    base: str
    head: str
    merge_base: str
    commits: list[CommitInfo]
    scope_digest: str

    def to_packet_scope(self) -> dict[str, Any]:
        return {
            "kind": "branch_context",
            "project_dir": str(self.project_dir),
            "project_slug": self.project_slug,
            "base": self.base,
            "head": self.head,
            "merge_base": self.merge_base,
            "commits": [
                {
                    "sha": c.sha,
                    "short_sha": c.short_sha,
                    "subject": c.subject,
                    "body": c.body,
                    "is_merge": c.is_merge,
                    "parents": list(c.parents),
                }
                for c in self.commits
            ],
            "scope_digest": self.scope_digest,
        }


def walk_branch_commits(
    project_dir: Path,
    base: str,
    head: str = "HEAD",
    *,
    include_merges: bool = True,
) -> list[CommitInfo]:
    """Return commits unique to ``head`` since it diverged from ``base``."""

    project_dir = project_dir.resolve()
    merge_base = _git(
        project_dir, ["merge-base", base, head]
    ).strip()
    if not merge_base:
        return []
    raw = _git(
        project_dir,
        [
            "log",
            "--reverse",
            f"{merge_base}..{head}",
            "--format=%H%x1f%h%x1f%P%x1f%s%x1f%b%x1e",
        ],
    )
    commits: list[CommitInfo] = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split("\x1f")
        if len(parts) < 5:
            continue
        sha, short_sha, parent_str, subject, body = parts[0], parts[1], parts[2], parts[3], parts[4]
        parents = [p for p in parent_str.split() if p]
        info = CommitInfo(
            sha=sha,
            short_sha=short_sha,
            subject=subject,
            body=body.strip("\n"),
            parents=parents,
        )
        if not include_merges and info.is_merge:
            continue
        commits.append(info)
    return commits


def build_branch_scope(
    project_dir: Path,
    base: str,
    head: str = "HEAD",
) -> BranchScope:
    project_dir = project_dir.resolve()
    project_slug = _project_slug_for(project_dir)
    head_resolved = _git(project_dir, ["rev-parse", head]).strip()
    merge_base = _git(project_dir, ["merge-base", base, head_resolved]).strip()
    commits = walk_branch_commits(project_dir, base, head_resolved)
    digest_material = "\n".join(
        [merge_base, head_resolved, *(c.sha for c in commits)]
    )
    scope_digest = hashlib.sha256(digest_material.encode("utf-8")).hexdigest()
    return BranchScope(
        project_dir=project_dir,
        project_slug=project_slug,
        base=base,
        head=head_resolved,
        merge_base=merge_base,
        commits=commits,
        scope_digest=scope_digest,
    )


# ---------------------------------------------------------------------------
# Branch workflow runner (cache-aware)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BranchRunResult:
    workflow_name: str
    scope: BranchScope
    output_path: Path
    rows: list[dict[str, Any]]
    cache_hit: bool
    execution: WorkflowExecutionResult | None


def branch_run_output_path(
    project_dir: Path, workflow_name: str, scope_digest: str,
    *, llm_polish: bool = True,
) -> Path:
    # The polish flag is part of the cache key: a --no-llm run must not
    # return a body cached by an earlier --llm run at the same scope.
    suffix = "" if llm_polish else ".deterministic"
    return (
        project_dir.resolve()
        / ".opentraces"
        / "branch_runs"
        / workflow_name
        / f"{scope_digest}{suffix}.jsonl"
    )


def run_branch_workflow(
    workflow_name: str,
    *,
    project_dir: Path,
    base: str,
    head: str = "HEAD",
    force: bool = False,
    llm_polish: bool = True,
) -> BranchRunResult:
    """Run ``workflow_name`` on a branch context, with per-digest caching.

    ``llm_polish`` (default ``True``) enables the LLM enrichment pass inside
    the workflow's builder script. Set to ``False`` (or pass ``--no-llm`` on
    the CLI) for fully deterministic output. The polish step degrades
    gracefully on missing ``ANTHROPIC_API_KEY`` regardless of this flag.
    """

    scope = build_branch_scope(project_dir, base, head)
    output_path = branch_run_output_path(
        scope.project_dir, workflow_name, scope.scope_digest,
        llm_polish=llm_polish,
    )
    if not force and output_path.exists():
        return BranchRunResult(
            workflow_name=workflow_name,
            scope=scope,
            output_path=output_path,
            rows=_read_rows(output_path),
            cache_hit=True,
            execution=None,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    execution = execute_workflow(
        workflow_name,
        scope=scope.to_packet_scope(),
        output_path=output_path,
        executor="script",
        extra_env={"OT_LLM_POLISH": "1" if llm_polish else "0"},
    )
    rows = _read_rows(output_path)
    return BranchRunResult(
        workflow_name=workflow_name,
        scope=scope,
        output_path=output_path,
        rows=rows,
        cache_hit=False,
        execution=execution,
    )


# ---------------------------------------------------------------------------
# Intent redaction (also strips adversarial sentinels)
# ---------------------------------------------------------------------------


_ADVERSARIAL_SENTINEL_RE = re.compile(
    r"-----\s*(?:BEGIN|END)\s+USER\s+INTENT\s*-----",
    re.IGNORECASE,
)
_FENCE_BREAKOUT_RE = re.compile(r"`{3,}")
_LEADING_HEADING_RE = re.compile(r"^\s*#+\s+", re.MULTILINE)
_INTENT_MAX_CHARS = 600
_INTENT_HEADLINE_MAX_CHARS = 400


def redact_intent(
    text: str | None, *, max_chars: int = _INTENT_MAX_CHARS,
) -> str:
    """Run intent text through security redaction + adversarial stripping.

    Also neutralizes markdown that would break the surrounding body —
    leading ``#`` heading sentinels are demoted to ``\\#`` so a quoted
    intent doesn't introduce a new ``## Section`` into the PR body.
    """

    if not text:
        return ""
    value = redact_index_text(text)
    value = _ADVERSARIAL_SENTINEL_RE.sub("[stripped:user-intent-sentinel]", value)
    value = _FENCE_BREAKOUT_RE.sub("```", value)
    value = _LEADING_HEADING_RE.sub(lambda m: m.group(0).replace("#", "\\#"), value)
    value = value.strip()
    if len(value) > max_chars:
        value = value[: max_chars - 1].rstrip() + "…"
    return value


def _quote_intent(text: str) -> str:
    """Render intent as a markdown blockquote so it visually nests inside the body."""

    if not text:
        return ""
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def render_pr_markdown(
    rows: list[dict[str, Any]],
    *,
    opentraces_version: str,
    bucket_remote_url: str | None = None,
    scope_digest: str | None = None,
    workflow_name: str = "pr-intent-summary-v1",
) -> str:
    """Render branch-context rows as a GitHub PR markdown body.

    If a ``branch_summary`` row is present (LLM-enriched), the body uses the
    progressive-disclosure layout: ``## What this PR does``, ``## Why``,
    ``## Confidence signals``, then a commit table with one-liners, then
    detailed lineage in a collapsible. If no LLM enrichment is present, falls
    back to the deterministic layout (``## Intent`` headline + ``## What
    Changed`` + per-commit collapsibles).
    """

    commit_rows = [r for r in rows if r.get("_kind") != "branch_summary"]
    summary_row = next(
        (r for r in rows if r.get("_kind") == "branch_summary"), None,
    )
    enrichment = summary_row.get("llm") if summary_row else None
    digest_short = (scope_digest or "")[:12]
    trace_count = sum(len(r.get("lineage") or []) for r in commit_rows)

    if enrichment:
        return _render_with_llm_polish(
            commit_rows,
            enrichment=enrichment,
            bucket_remote_url=bucket_remote_url,
            workflow_name=workflow_name,
            opentraces_version=opentraces_version,
            digest_short=digest_short,
            trace_count=trace_count,
        )
    return _render_deterministic_only(
        commit_rows,
        bucket_remote_url=bucket_remote_url,
        workflow_name=workflow_name,
        opentraces_version=opentraces_version,
        digest_short=digest_short,
        trace_count=trace_count,
    )


def _render_deterministic_only(
    commit_rows: list[dict[str, Any]],
    *,
    bucket_remote_url: str | None,
    workflow_name: str,
    opentraces_version: str,
    digest_short: str,
    trace_count: int,
) -> str:
    headline_intent = _pick_headline_intent(commit_rows)
    what_changed_lines = [
        f"- {row.get('subject') or row.get('short_sha') or row.get('commit_sha', '')[:7]}"
        for row in commit_rows
        if not row.get("is_merge")
    ]
    commit_blocks = [_render_commit_block(row, bucket_remote_url) for row in commit_rows]

    sections: list[str] = []
    sections.append("## Intent")
    sections.append("")
    sections.append(headline_intent or "_No traced intent for the commits on this branch._")
    sections.append("")
    sections.append("## What Changed")
    sections.extend(what_changed_lines or ["_No commits on this branch yet._"])
    sections.append("")
    sections.append("## Commits & Trails")
    sections.append("")
    sections.extend(commit_blocks)
    sections.append("")
    sections.extend(_reviewer_toolbox_section())
    sections.append("")
    sections.append("## Trace Provenance")
    sections.append(_provenance_line(
        workflow_name=workflow_name,
        trace_count=trace_count,
        digest_short=digest_short,
        opentraces_version=opentraces_version,
        llm_meta=None,
    ))
    return "\n".join(sections).rstrip() + "\n"


def _render_with_llm_polish(
    commit_rows: list[dict[str, Any]],
    *,
    enrichment: dict[str, Any],
    bucket_remote_url: str | None,
    workflow_name: str,
    opentraces_version: str,
    digest_short: str,
    trace_count: int,
) -> str:
    """Progressive-disclosure layout when LLM enrichment is available."""

    headline_what = (enrichment.get("headline_what") or "").strip()
    headline_why = (enrichment.get("headline_why") or "").strip()
    commit_one_liners = {
        item.get("commit_sha"): item.get("summary", "")
        for item in (enrichment.get("commit_summaries") or [])
    }
    confidence_signals = enrichment.get("confidence_signals") or []
    llm_meta = {
        "model": enrichment.get("model") or "",
        "provider": enrichment.get("provider") or "",
        "provider_notes": enrichment.get("provider_notes") or "",
        "input_tokens": int(enrichment.get("input_tokens") or 0),
        "output_tokens": int(enrichment.get("output_tokens") or 0),
    }

    sections: list[str] = []
    sections.append("## What this PR does")
    sections.append("")
    sections.append(headline_what or "_LLM polish produced no headline; see commit-level detail below._")
    sections.append("")
    sections.append(_llm_provenance_callout(commit_rows, headline_what))
    sections.append("")
    sections.append("## Why")
    sections.append("")
    sections.append(headline_why or "_LLM polish produced no motivation; see verbatim user specs in the drilldown._")
    sections.append("")
    sections.append("> _Synthesized from user-typed specs across the most-substantive bursts. Source specs verbatim in the drilldown below._")
    sections.append("")

    if confidence_signals:
        sections.append("## Confidence signals")
        sections.append("")
        for sig in confidence_signals:
            sections.append(_format_confidence_signal(sig))
        sections.append("")
        sections.append("> _Each signal cites the opentraces command that verifies it. Run any of them locally to expand the claim into source data._")
        sections.append("")

    sections.append("## Commits")
    sections.append("")
    sections.extend(_render_commits_table(commit_rows, commit_one_liners))
    sections.append("")

    sections.append(
        f"<details>\n<summary><strong>📂 Detailed trace lineage — "
        f"{trace_count} trace(s) across {len(commit_rows)} commit(s) — "
        "<em>expand to walk the trail</em></strong></summary>\n"
    )
    sections.append("")
    for row in commit_rows:
        sections.append(_render_commit_block(row, bucket_remote_url))
    sections.append("</details>")
    sections.append("")

    sections.extend(_verify_section())
    sections.append("")
    sections.append("## Trace Provenance")
    sections.append(_provenance_line(
        workflow_name=workflow_name,
        trace_count=trace_count,
        digest_short=digest_short,
        opentraces_version=opentraces_version,
        llm_meta=llm_meta,
    ))
    return "\n".join(sections).rstrip() + "\n"


def _llm_provenance_callout(
    commit_rows: list[dict[str, Any]], headline: str,
) -> str:
    trace_count = sum(len(r.get("lineage") or []) for r in commit_rows)
    return (
        f"> _LLM-summarized from {trace_count} trace(s) + {len(commit_rows)} commit(s). "
        "Verify any claim with the commands in [§ Verify any claim](#verify-any-claim-in-this-pr) below._"
    )


def _format_confidence_signal(sig: dict[str, Any]) -> str:
    glyph = {"ok": "✅", "warn": "⚠️", "info": "📊"}.get(sig.get("kind") or "info", "·")
    claim = (sig.get("claim") or "").strip()
    cmd = sig.get("evidence_command")
    if cmd:
        return f"- {glyph} {claim} — `{cmd}`"
    return f"- {glyph} {claim}"


def _render_commits_table(
    commit_rows: list[dict[str, Any]],
    commit_one_liners: dict[str, str],
) -> list[str]:
    out: list[str] = [
        "| sha | summary | trace coverage |",
        "|---|---|---|",
    ]
    for row in commit_rows:
        sha = row.get("commit_sha") or ""
        short_sha = row.get("short_sha") or sha[:7]
        if row.get("is_merge"):
            summary = "_merge commit_"
            coverage = "—"
        else:
            llm_summary = (commit_one_liners.get(sha) or "").strip()
            summary = llm_summary or row.get("subject") or "_no summary_"
            lineage = row.get("lineage") or []
            if lineage:
                top_credit = max(
                    (l.get("credit") or {}).get("line_ratio", 0.0) for l in lineage
                )
                coverage = f"{len(lineage)} trace(s), top {int(top_credit * 100)}% credit"
            else:
                coverage = f"_{(row.get('no_lineage_reason') or 'no lineage')}_"
        # Escape pipe chars in summary so they don't break the table.
        summary_escaped = summary.replace("|", "\\|")
        out.append(f"| `{short_sha}` | {summary_escaped} | {coverage} |")
    return out


def _reviewer_toolbox_section() -> list[str]:
    return [
        "## Reviewer Toolbox",
        "",
        "```bash",
        "# One-time setup to walk any trail referenced above:",
        "opentraces bucket remote pull",
        "opentraces bucket replay --repo .",
        "```",
    ]


def _verify_section() -> list[str]:
    return [
        "## Verify any claim in this PR",
        "",
        "> _Reviewer (human or agent): every assertion above is backed by trace-level "
        "evidence in the bucket. Pull the bucket once, then run any command below to "
        "expand a claim into its source data._",
        "",
        "**One-time setup:**",
        "```bash",
        "opentraces bucket remote pull",
        "opentraces bucket replay --repo .",
        "```",
        "",
        "**Then for any claim:**",
        "",
        "| To verify | Run | What you get |",
        "|---|---|---|",
        "| Trace X authored commit Y | `opentraces trail blame commit <sha> --json` | per-line attribution + entity changes |",
        "| What did the agent do at step N? | `opentraces trail explain --commit <sha> --json` | full evidence chain |",
        "| Step-by-step replay of any trace | `opentraces trail timeline <trace_id> --json` | full trace timeline |",
        "| Pull a trace bundle for offline inspection | `opentraces trail teleport export <trace_id> --output ./trace.zip` | portable zip with everything |",
        "| Show me the raw bucket record | `opentraces trace get ot://trace/<trace_id>` | the JSON envelope |",
        "| Did the LLM summary above hallucinate? | `opentraces trail blame pr render --base main --no-llm` | regenerate without LLM polish — compare side-by-side |",
    ]


def _provenance_line(
    *,
    workflow_name: str,
    trace_count: int,
    digest_short: str,
    opentraces_version: str,
    llm_meta: dict[str, Any] | None,
) -> str:
    parts = [
        f"Generated by `opentraces trail blame pr render` from workflow `{workflow_name}`",
        f"across {trace_count} trace(s)",
    ]
    if digest_short:
        parts.append(f"scope digest `{digest_short}`")
    parts.append(f"opentraces v{opentraces_version}")
    if llm_meta and llm_meta.get("model"):
        provider = llm_meta.get("provider") or "anthropic-api"
        notes = llm_meta.get("provider_notes") or ""
        polish_part = f"LLM polish: `{provider}` ({llm_meta['model']})"
        if notes:
            polish_part += f" — {notes}"
        if llm_meta.get("input_tokens") or llm_meta.get("output_tokens"):
            polish_part += (
                f" — input: {llm_meta['input_tokens']} tokens, "
                f"output: {llm_meta['output_tokens']} tokens"
            )
        parts.append(polish_part)
    return " · ".join(parts)


def _pick_headline_intent(rows: list[dict[str, Any]]) -> str:
    """Pick the most-substantive intent across all lineages on the branch."""

    best_text = ""
    best_weight = -1
    for row in rows:
        for lineage in row.get("lineage") or []:
            spec = (lineage.get("intent") or {}).get("most_substantive_spec") or {}
            text = redact_intent(spec.get("text"), max_chars=_INTENT_HEADLINE_MAX_CHARS)
            if not text:
                continue
            weight = (lineage.get("credit") or {}).get("line_count") or 0
            # Tiebreak on text length so verbose intents beat terse ones at
            # equal credit weight.
            score = (weight, len(text))
            if score > (best_weight, len(best_text)):
                best_weight = weight
                best_text = text
    return _quote_intent(best_text) if best_text else ""


_NO_LINEAGE_REASON_LABELS = {
    NO_LINEAGE_MERGE_COMMIT: "merge commit",
    NO_LINEAGE_NO_ATTRIBUTION: "no attribution cache for this commit",
    NO_LINEAGE_NO_BURST: "no burst in any traced session matched this commit",
    NO_LINEAGE_NO_TRACES: "no agent traces in this project's bucket touched any commit on this branch",
}


def _render_commit_block(
    row: dict[str, Any], bucket_remote_url: str | None,
) -> str:
    short_sha = row.get("short_sha") or (row.get("commit_sha") or "")[:7]
    subject = row.get("subject") or "(no subject)"
    lineage = row.get("lineage") or []

    if not lineage:
        reason = row.get("no_lineage_reason") or "no lineage"
        reason_text = _NO_LINEAGE_REASON_LABELS.get(str(reason), str(reason))
        return (
            f"<details>\n"
            f"<summary>✏️ {_md_escape(subject)} · <code>{short_sha}</code></summary>\n\n"
            f"_no opentraces lineage — {reason_text}_\n"
            f"</details>\n\n"
        )

    summary_traces = ", ".join(
        f"<code>{(entry.get('short_id') or '')}</code>" for entry in lineage
    )
    trace_count = len(lineage)
    plural = "s" if trace_count != 1 else ""
    lines = [
        "<details>\n",
        (
            f"<summary>✏️ {_md_escape(subject)} · <code>{short_sha}</code> · "
            f"{trace_count} trace{plural}: {summary_traces}</summary>\n\n"
        ),
    ]
    for index, entry in enumerate(lineage, start=1):
        lines.append(_render_lineage_section(
            index, trace_count, short_sha, entry, bucket_remote_url,
        ))
    lines.append("</details>\n\n")
    return "".join(lines)


def _render_lineage_section(
    index: int,
    total: int,
    short_sha: str,
    lineage: dict[str, Any],
    bucket_remote_url: str | None,
) -> str:
    trace_id = lineage.get("trace_id") or ""
    short_id = lineage.get("short_id") or trace_id[:8]
    intent = lineage.get("intent") or {}
    spec = intent.get("most_substantive_spec") or {}
    why_text = redact_intent(spec.get("text"))
    why_block = (
        _quote_intent(why_text) if why_text
        else "> _no extracted intent_"
    )
    spec_chain_count = int(intent.get("spec_chain_count") or 0)
    did_what = lineage.get("did_what_bullets") or []
    files = lineage.get("files_touched") or []
    pointer = lineage.get("bucket_pointer") or {}

    bucket_line = f"`{pointer.get('uri') or ''}`"
    object_path = pointer.get("object_path")
    remote_url = pointer.get("remote_url") or bucket_remote_url
    if object_path:
        if remote_url:
            bucket_line += f" → `{remote_url}` · `{object_path}`"
        else:
            bucket_line += f" → local `{object_path}`"

    files_line = ", ".join(f"`{f}`" for f in files[:8])
    if len(files) > 8:
        files_line += f", and {len(files) - 8} more"

    out: list[str] = []
    if total > 1:
        out.append(f"**Trace {index} of {total}:** `{short_id}`\n\n")
    out.append("**Why:**\n")
    out.append(f"{why_block}\n")
    if spec_chain_count:
        out.append(f"\n_Across {spec_chain_count} user turn(s) in this burst._\n")
    out.append("\n")
    if did_what:
        out.append("**What:**\n")
        for bullet in did_what:
            out.append(f"- {bullet}\n")
        out.append("\n")
    if files_line:
        out.append(f"**Files touched:** {files_line}\n\n")
    out.append("**Walk the trail:**\n")
    out.append(f"- `opentraces trail timeline {trace_id}`\n")
    out.append(f"- `opentraces trail explain --commit {short_sha}`\n")
    out.append(f"- `opentraces trail blame commit {short_sha}`\n")
    out.append(
        f"- `opentraces trail teleport export {trace_id} --output ./trace.zip`\n"
    )
    out.append("\n")
    out.append(f"**Bucket pointer:** {bucket_line}\n")
    if index < total:
        out.append("\n---\n\n")
    return "".join(out)


_MD_ESCAPE_RE = re.compile(r"([\\`*_{}\[\]()#+\-!|<>])")


def _md_escape(text: str) -> str:
    return _MD_ESCAPE_RE.sub(r"\\\1", str(text))


# ---------------------------------------------------------------------------
# `gh` wrappers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PRInfo:
    number: int
    url: str
    head_ref: str
    state: str = "OPEN"


class GhUnavailableError(RuntimeError):
    pass


class GhError(RuntimeError):
    pass


def gh_available() -> bool:
    return shutil.which("gh") is not None


def _require_gh() -> str:
    path = shutil.which("gh")
    if not path:
        raise GhUnavailableError(
            "GitHub CLI (`gh`) not found on PATH. "
            "Install via `brew install gh` or see https://cli.github.com/."
        )
    return path


def find_pr_for_branch(
    project_dir: Path, head: str | None = None,
) -> PRInfo | None:
    gh = _require_gh()
    args = [gh, "pr", "view", "--json", "number,url,headRefName,state"]
    if head:
        args.append(head)
    completed = subprocess.run(
        args,
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        # `gh pr view` exits 1 when there is no PR for the branch.
        return None
    payload = json.loads(completed.stdout or "{}")
    return PRInfo(
        number=int(payload.get("number") or 0),
        url=str(payload.get("url") or ""),
        head_ref=str(payload.get("headRefName") or ""),
        state=str(payload.get("state") or "OPEN"),
    )


def create_pr(
    *,
    project_dir: Path,
    base: str,
    title: str,
    body: str,
    draft: bool = False,
) -> PRInfo:
    gh = _require_gh()
    args = [
        gh, "pr", "create",
        "--base", base,
        "--title", title,
        "--body-file", "-",
    ]
    if draft:
        args.append("--draft")
    completed = subprocess.run(
        args,
        cwd=str(project_dir),
        input=body,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise GhError(completed.stderr or completed.stdout or "gh pr create failed")
    url = (completed.stdout or "").strip().splitlines()[-1] if completed.stdout else ""
    info = find_pr_for_branch(project_dir)
    if info is not None:
        return info
    return PRInfo(number=0, url=url, head_ref="", state="OPEN")


def update_pr(
    *,
    project_dir: Path,
    number: int,
    body: str,
) -> PRInfo:
    gh = _require_gh()
    args = [
        gh, "pr", "edit", str(number),
        "--body-file", "-",
    ]
    completed = subprocess.run(
        args,
        cwd=str(project_dir),
        input=body,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise GhError(completed.stderr or completed.stdout or "gh pr edit failed")
    info = find_pr_for_branch(project_dir)
    if info is not None:
        return info
    return PRInfo(number=number, url=(completed.stdout or "").strip(), head_ref="")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _git(project_dir: Path, args: list[str]) -> str:
    # Delegates to the shared git wrapper (permissive UTF-8 decode, raises
    # RuntimeError on non-zero exit).
    return _git_run(*args, cwd=project_dir)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def bucket_remote_url() -> str | None:
    """Public accessor: bucket remote URL from config (``None`` if local-only)."""

    cfg = load_config()
    remote = cfg.bucket.remote
    if not remote.enabled:
        return None
    return remote.url or None


__all__ = [
    "BranchRunResult",
    "BranchScope",
    "CommitInfo",
    "GhError",
    "GhUnavailableError",
    "PRInfo",
    "branch_run_output_path",
    "bucket_remote_url",
    "build_branch_scope",
    "create_pr",
    "find_pr_for_branch",
    "gh_available",
    "redact_intent",
    "render_pr_markdown",
    "run_branch_workflow",
    "update_pr",
    "walk_branch_commits",
]
