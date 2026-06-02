"""``opentraces skill-verifier`` — author + calibrate a trace-grounded skill verifier.

Two modes (see ``skill/verifier-creator/SKILL.md``): AUTOVERIFY (the agent self-aligns a rubric
to the skill's goal) and MANUAL (an alignment session where a human labels a few traces). The
verbs are read-only over the bucket and never promote: the agent proposes, the factory scores
against evidence + calibration, a human approves. Autoverify caps at ``provisional_weak_only``;
``calibrated`` requires real human gold.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from ._help import OpentracesCommand, OpentracesGroup


@click.group("skill-verifier", cls=OpentracesGroup)
def skill_verifier_group() -> None:
    """Author and calibrate a trace-grounded verifier (rubric) for a skill."""


def _load(skill: str, project: str | None):
    from ..consumers.skill_intelligence import pipeline as si
    from ..core.bucket_store import iter_trace_record_objects

    episodes = si.build_episode_rows(selected_skill=skill, min_rows=0, project=project)
    records = {
        o.trace_id: o.record
        for o in iter_trace_record_objects(project_slug=project)
        if o.trace_id in {str(e.get("source_trace_id") or "") for e in episodes}
    }
    return episodes, records


@skill_verifier_group.command("autoverify", cls=OpentracesCommand)
@click.argument("skill")
@click.option("--project", default=None, help="Restrict to one project slug.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def sv_autoverify(skill: str, project: str | None, as_json: bool) -> None:
    """Self-align a rubric to the skill's goal and calibrate it (autoverify mode)."""
    from ..consumers.verifier_factory import autoverify

    episodes, records = _load(skill, project)
    res = autoverify(skill, episodes=episodes, records=records)
    payload = res.as_dict()
    payload["episodes"] = len(episodes)
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"autoverify {skill}: status={res.calibration.status} recommended={res.recommended}")
    click.echo(f"  auc={res.calibration.auc_human} rho={res.calibration.rho_secondary}")
    for b in res.calibration.blockers:
        click.echo(f"  blocker: {b}")
    click.echo("  (autoverify caps at provisional_weak_only; calibrated requires real human gold)")


@skill_verifier_group.command("align", cls=OpentracesCommand)
@click.argument("skill")
@click.option("--project", default=None, help="Restrict to one project slug.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def sv_align(skill: str, project: str | None, as_json: bool) -> None:
    """Scaffold a manual alignment session (desired outcome + draft rubric + traces to label)."""
    from ..consumers.verifier_factory import align_session

    episodes, records = _load(skill, project)
    sess = align_session(skill, episodes=episodes, records=records)
    if as_json:
        click.echo(json.dumps(sess, indent=2, sort_keys=True))
        return
    click.echo(sess["desired_outcome_prompt"])
    click.echo(f"  label >= {sess['labels_needed']['min_total']} traces "
               f"(>= {sess['labels_needed']['min_per_class']}/class) via record_human_label")


@skill_verifier_group.command("score", cls=OpentracesCommand)
@click.argument("skill")
@click.option("--project", default=None, help="Restrict to one project slug.")
@click.option("--out", "out_dir", type=click.Path(file_okay=False), default=None,
              help="Output package directory.")
@click.option("--emulate-labels", is_flag=True,
              help="Use EMULATED gold (a flagged demo; caps at provisional, never calibrated).")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def sv_score(skill: str, project: str | None, out_dir: str | None, emulate_labels: bool,
             as_json: bool) -> None:
    """Drive SkillOpt with the calibrated rubric (Phase 5 reward swap); emit a package."""
    from ..consumers.verifier_factory import (
        autoverify_draft_rubric, emulate_human_labels, score_rubric,
    )

    episodes, records = _load(skill, project)
    rubric = autoverify_draft_rubric(skill, episodes=episodes, records=records)
    labels = emulate_human_labels(episodes, records) if emulate_labels else None
    out = Path(out_dir).expanduser() if out_dir else Path("runs") / "skill-verifier" / skill
    res = score_rubric(
        rubric, out_dir=out, episodes=episodes, records=records, human_labels=labels,
        gold_is_emulated=emulate_labels, same_session_self_judge=True,
    )
    payload = {
        "skill": skill, "status": res.status, "usable": res.usable,
        "dsel_before": res.dsel_before, "dsel_after": res.dsel_after,
        "dtest_score": res.dtest_score, "accepted": res.accepted,
        "recommended": res.recommended, "addressable_markers": list(res.addressable_markers),
        "blockers": list(res.blockers), "package_dir": str(res.package_dir),
        "gold_emulated": emulate_labels,
    }
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"score {skill}: status={res.status} usable={res.usable} "
               f"Dsel {res.dsel_before:.3f}->{res.dsel_after:.3f} Dtest {res.dtest_score:.3f} "
               f"recommended={res.recommended}  -> {res.package_dir}")


@skill_verifier_group.command("status", cls=OpentracesCommand)
@click.argument("skill")
@click.option("--project", default=None, help="Restrict to one project slug.")
@click.option("--json", "as_json", is_flag=True, help="Emit structured JSON.")
def sv_status(skill: str, project: str | None, as_json: bool) -> None:
    """Quick autoverify status for a skill (feasibility triage)."""
    from ..consumers.verifier_factory import autoverify

    episodes, records = _load(skill, project)
    res = autoverify(skill, episodes=episodes, records=records)
    payload = {"skill": skill, "status": res.calibration.status, "recommended": res.recommended,
               "episodes": len(episodes), "blockers": list(res.calibration.blockers)}
    if as_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"{skill}: {res.calibration.status} (recommended={res.recommended}, "
               f"{len(episodes)} episodes)")
