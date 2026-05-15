"""Artifacts bundle — PR-ready evidence for a journey run (spec M4 / R6).

Mirrors crabbox's ``artifacts collect`` contract: capture enough of a run
to reproduce and review it. ``otbox artifacts`` writes a self-contained
directory with run logs, per-step transcripts, a JUnit summary, and
env/seed/box metadata — suitable to drop on a PR as before/after proof.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .env import ARTIFACTS_DIR, Box, ensure_state_root
from .journey import JourneyResult


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in text).strip("-")


def _junit(results: list[JourneyResult]) -> str:
    suite = ET.Element(
        "testsuite",
        name="otbox",
        tests=str(len(results)),
        failures=str(sum(r.verdict == "FAIL" for r in results)),
        skipped=str(sum(r.verdict == "SKIP" for r in results)),
    )
    for r in results:
        case = ET.SubElement(suite, "testcase", classname=f"otbox.{r.lane}", name=r.name)
        if r.verdict == "FAIL":
            failure = ET.SubElement(case, "failure", message=r.reason or "journey failed")
            failure.text = "\n".join(
                f"step[{s.index}] {s.step_id}: {'ok' if s.ok else s.message}"
                for s in r.steps
            ) + "\n" + "\n".join(
                f"assert[{a.index}] {a.kind}: {'ok' if a.ok else a.message}"
                for a in r.assertions
            )
        elif r.verdict == "SKIP":
            ET.SubElement(case, "skipped", message=r.reason)
    return ET.tostring(suite, encoding="unicode")


def collect_artifacts(
    box: Box,
    results: list[JourneyResult],
    *,
    run_label: str | None = None,
    extra: dict | None = None,
) -> Path:
    """Write an artifact bundle for a set of journey results, return its path."""
    ensure_state_root()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = _slug(run_label or (results[0].name if results else "run"))
    out = ARTIFACTS_DIR / f"{stamp}-{box.box_id}-{label}"
    out.mkdir(parents=True, exist_ok=True)

    # box + env metadata
    meta = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "box": box.to_dict(),
        "box_root": str(box.root),
        "seed": box.seed,
        "journeys": [r.name for r in results],
        "verdicts": {r.name: r.verdict for r in results},
    }
    if extra:
        meta["extra"] = extra
    (out / "metadata.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")

    # full structured results
    (out / "results.json").write_text(
        json.dumps([r.to_dict() for r in results], indent=2, sort_keys=True) + "\n"
    )

    # JUnit summary
    (out / "junit.xml").write_text(_junit(results))

    # per-step transcripts — the human-readable evidence trail
    transcript_dir = out / "transcripts"
    transcript_dir.mkdir(exist_ok=True)
    for r in results:
        lines: list[str] = [
            f"# journey: {r.name}  [{r.verdict}]",
            f"# {r.description}",
            f"# lane={r.lane} tier={r.tier} seed={r.seed} box={r.box_id}",
            f"# reason: {r.reason}" if r.reason else "#",
            "",
        ]
        for s in r.steps:
            lines.append(f"## step[{s.index}] {s.step_id} ({s.type}) {'OK' if s.ok else 'FAIL'}")
            if s.result is not None:
                lines.append(f"$ {' '.join(s.result.argv)}")
                lines.append(f"# rc={s.result.returncode} duration={s.result.duration_s:.3f}s cwd={s.result.cwd}")
                if s.result.stdout.strip():
                    lines.append("--- stdout ---")
                    lines.append(s.result.stdout.rstrip())
                if s.result.stderr.strip():
                    lines.append("--- stderr ---")
                    lines.append(s.result.stderr.rstrip())
            else:
                lines.append(f"# {s.message}")
            lines.append("")
        for a in r.assertions:
            lines.append(f"## assert[{a.index}] {a.kind} {'OK' if a.ok else 'FAIL'}: {a.message}")
        (transcript_dir / f"{_slug(r.name)}.txt").write_text("\n".join(lines) + "\n")

    # human-readable index
    summary = ["# otbox artifact bundle", "", f"box: {box.box_id}  seed: {box.seed}", ""]
    for r in results:
        summary.append(f"- [{r.verdict}] {r.name} — {r.reason or 'ok'}")
    (out / "SUMMARY.md").write_text("\n".join(summary) + "\n")

    return out
