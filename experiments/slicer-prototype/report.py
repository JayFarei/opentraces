"""Phase-0 report synthesizer (issue #141).

Reads the committed evidence (sweep_summary, mechanical_signals, manifests,
reviewer_panel) and emits REPORT.md + report.html with an explicit per-slicer
GO/NO-GO verdict and the failing trace IDs named.

GO/NO-GO logic (codifies the issue's grounded stop-condition):
  HARD GATE (must all hold over the full stratified sample, recomputed
  independently — crashes count as fail, never a silent skip):
    - reliability_rate == 1.0 (independent verifier: coverage 1.0 / 0 gaps /
      0 overlaps on every trace)
    - idempotent on every trace (byte-identical re-run)
    - cheap_llm: request set bounded (<= marker density) AND deterministic
      under recorded answers on every trace
  ADVISORY (reported, NEVER gates): mean conformance + 3-persona utility, plus
  the surfaced worst cases. Conformance is GROUNDED for S1/S2 (mechanical vs
  the inlined rule); utility is advisory.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

SLICER_NAMES = {
    "s1": "S1 user-turn (deterministic)",
    "s2": "S2 change-burst (deterministic)",
    "s3": "S3 milestone (cheap-LLM)",
    "s4": "S4 subgoal (cheap-LLM)",
}
TIER = {"s1": "deterministic", "s2": "deterministic", "s3": "cheap_llm", "s4": "cheap_llm"}

# Honest advisory caveats + the Phase-1 remediation each GO slicer carries.
REMEDIATION = {
    "s1": ("Strong (conformance grounded/mechanical). Caveat: control messages NOT in the LOCKED "
           "blocklist (`<system-reminder>`, `[Request interrupted]`, and `<turn_aborted>` when a trace "
           "has no genuine user turn) open trajectories — 25 leaky openers across the 200-sample. The "
           "S1 rule is LOCKED for v1; widening the blocklist is a v1.1 follow-up. No reliability impact."),
    "s2": ("Reliable + deterministic. Advisory: on no-edit traces S2 degrades to an S1 copy with "
           "`explore` labels, and long edit sessions can lump multiple bursts into one trajectory "
           "(reviewer mis-cuts surfaced). Phase 1 reuses `core/bursts.py` clustering verbatim (gap=35); "
           "mis-cuts are a tuning/utility matter, not a tiling defect."),
    "s3": ("Reliable + deterministic + bounded, BUT lowest advisory conformance (0.50, 15/36 worst "
           "cases). The PROTOTYPE's deterministic success-detector was too loose — it closed trajectories "
           "on test FAILURES ('3 pass 1 fail') and fabricated `<test>` artifacts. PHASE-1 REMEDIATION "
           "(mandatory): tighten success detection to require an UNAMBIGUOUS deliverable signal and NEVER "
           "close on a failure; the same-outcome collapse is the agent-loop judgment. Conformance is "
           "advisory and does not gate GO; reliability is the gate and it holds."),
    "s4": ("Reliable + deterministic + bounded. With the prototype's conservative default judge S4 is "
           "identical to S1 (reviewers confirmed real internal pivots a good judge would catch). S4's "
           "value is ENTIRELY the agent-loop pivot judgment, which Phase 1 ships (the prototype only "
           "stubbed it). GO on the hard gate; utility realised by the real `agent` judge."),
}


def load(name, default=None):
    p = HERE / name
    if p.exists():
        return json.loads(p.read_text())
    return default


def verdict_for(sl, summ, rev):
    s = summ[sl]
    n = s["n"]
    reliability_ok = s["reliability_rate"] == 1.0 and s["crashed"] == 0
    idem_ok = s["idempotent"] == n
    cheap = TIER[sl] == "cheap_llm"
    bounded_ok = s["requests_bounded"] == n
    ans_det, ans_n = s["answers_deterministic"]
    ans_ok = (ans_det == ans_n) if cheap else True
    hard_ok = reliability_ok and idem_ok and (bounded_ok and ans_ok if cheap else True)
    fail_ids = [t for t, _why in s.get("fail_ids", [])]
    return {
        "verdict": "GO" if hard_ok else "NO-GO",
        "reliability_ok": reliability_ok,
        "idem_ok": idem_ok,
        "bounded_ok": bounded_ok,
        "ans_ok": ans_ok,
        "fail_ids": fail_ids,
    }


def main():
    summ = load("sweep_summary.json")
    mech = load("mechanical_signals.json", {})
    det = load("manifest_det.json", {})
    llm = load("manifest_llm.json", {})
    rev = load("reviewer_panel.json", {})  # {per_trace, aggregates}
    rev_agg = (rev or {}).get("aggregates", {})

    verdicts = {sl: verdict_for(sl, summ, rev_agg.get(sl, {})) for sl in SLICER_NAMES}

    # ---------------- REPORT.md ----------------
    L = []
    L.append("# Trace Slicer Library — Phase 0 prototype REPORT (issue #141)\n")
    L.append("> THROWAWAY prototype validation. Deliverable = this report + the per-slicer GO/NO-GO verdict. "
             "Code under `experiments/slicer-prototype/` is disposable and NOT wired into `src/`.\n")
    L.append("## Verdict summary\n")
    L.append("| Slicer | Tier | Reliability (independent verifier) | Idempotent | Bounded+Det (cheap-LLM) | Mean conformance | Mean utility (ml/obs/eval) | **Verdict** |")
    L.append("|---|---|---|---|---|---|---|---|")
    for sl in SLICER_NAMES:
        s = summ[sl]
        v = verdicts[sl]
        a = rev_agg.get(sl, {})
        cheap = TIER[sl] == "cheap_llm"
        ans_det, ans_n = s["answers_deterministic"]
        bd = f"{s['requests_bounded']}/{s['n']} bounded, {ans_det}/{ans_n} det" if cheap else "n/a (deterministic)"
        conf = a.get("mean_conformance")
        util = f"{a.get('mean_utility_ml')}/{a.get('mean_utility_obs')}/{a.get('mean_utility_eval')}" if a else "—"
        L.append(
            f"| {SLICER_NAMES[sl]} | {TIER[sl]} | {s['valid']}/{s['n']} ({s['reliability_rate']:.3f}) | "
            f"{s['idempotent']}/{s['n']} | {bd} | {conf if conf is not None else '—'} | {util} | **{v['verdict']}** |"
        )
    L.append("")

    n_det = det.get("count", 0)
    n_llm = llm.get("count", 0)
    L.append("## Sample (committed deterministic manifest)\n")
    L.append(f"- Deterministic-slicer sweep (S1/S2 + S3/S4 mechanical checks): **{n_det} traces** "
             f"(`manifest_det.json`). The full bucket is **1896 traces**.\n")
    L.append(f"  - by agent: `{det.get('by_agent')}`")
    L.append(f"  - by length bucket: `{det.get('by_length_bucket')}`")
    L.append(f"  - projects represented: **{len(det.get('by_project', {}))}** (not only community-traces-hf)")
    L.append(f"  - nasty-case coverage: `{det.get('nasty_flag_coverage')}`")
    L.append(f"- Cheap-LLM / reviewer subset: **{n_llm} traces** (`manifest_llm.json`), stratified from the det sample.")
    L.append(f"  - by agent: `{llm.get('by_agent')}`; by length: `{llm.get('by_length_bucket')}`; nasty: `{llm.get('nasty_flag_coverage')}`\n")

    L.append("## Mechanical reliability (every trace, deterministic — the HARD gate)\n")
    L.append("Recomputed by an INDEPENDENT verifier (`verify.recompute_tiling`) from the emitted `trajectories[]` "
             "alone — it never reads any self-reported `tiling.valid`. A crash on any trace counts as a FAIL "
             "(it shrinks no denominator).\n")
    for sl in SLICER_NAMES:
        s = summ[sl]
        v = verdicts[sl]
        ans_det, ans_n = s["answers_deterministic"]
        L.append(f"- **{SLICER_NAMES[sl]}**: tiling valid {s['valid']}/{s['n']}, idempotent {s['idempotent']}/{s['n']}, "
                 f"crashed {s['crashed']}, requests bounded {s['requests_bounded']}/{s['n']}"
                 + (f", deterministic-under-recorded-answers {ans_det}/{ans_n}" if TIER[sl]=='cheap_llm' else "")
                 + f". → reliability **{'PASS' if v['reliability_ok'] else 'FAIL'}**.")
        if v["fail_ids"]:
            L.append(f"  - FAILING trace IDs: {', '.join(v['fail_ids'][:30])}")
    L.append("")

    L.append("## Mechanical worst-cases surfaced (anti-cherry-pick, independent of the LLM panel)\n")
    for sl in SLICER_NAMES:
        m = mech.get(sl, {})
        L.append(f"- **{sl}**: single-trajectory (over-coarse) traces = {m.get('single_trajectory_count', 0)} "
                 f"(e.g. `{(m.get('single_trajectory') or [''])[0][:12]}`); leaky openers (control msgs that "
                 f"slipped the LOCKED S1 blocklist, e.g. `<system-reminder>`/`[Request interrupted]`) = "
                 f"{m.get('leaky_openers', 0)}; mean trajectories/100 steps = {m.get('mean_seg_per_100', 0)}.")
    L.append("")

    if rev_agg:
        L.append("## Reviewer-agent panel (advisory — conformance + 3-persona utility, NEVER gates)\n")
        L.append(f"Independent reviewer agents over the {n_llm}-trace subset (one per trace, all four slicers each). "
                 "Sentiment conformance is grounded for S1/S2 (mechanical vs the inlined rule); utility is advisory.\n")
        for sl in SLICER_NAMES:
            a = rev_agg.get(sl, {})
            L.append(f"### {SLICER_NAMES[sl]}")
            L.append(f"- mean conformance **{a.get('mean_conformance')}**; utility ml/obs/eval "
                     f"**{a.get('mean_utility_ml')} / {a.get('mean_utility_obs')} / {a.get('mean_utility_eval')}** "
                     f"(n={a.get('n')}).")
            lowest = a.get("lowest_conformance", [])[:6]
            if lowest:
                L.append("- lowest-conformance examples (worst surfaced, linked to real trace IDs):")
                for ex in lowest:
                    L.append(f"  - `{ex['trace_id'][:12]}` conformance={ex['conformance']}: {ex['note']}")
            L.append("")

    L.append("## Per-slicer GO/NO-GO verdict + rationale\n")
    for sl in SLICER_NAMES:
        v = verdicts[sl]
        a = rev_agg.get(sl, {})
        L.append(f"### {SLICER_NAMES[sl]} → **{v['verdict']}**")
        if v["verdict"] == "GO":
            L.append(f"- HARD gate PASS: reliability 1.000, idempotent, "
                     + ("bounded + deterministic-under-answers, " if TIER[sl]=='cheap_llm' else "")
                     + "no crashes over the stratified sample. Tiling recomputed independently.")
        else:
            L.append(f"- HARD gate FAIL. Failing trace IDs: {', '.join(v['fail_ids'][:30]) or '(see sweep)'}")
        if a:
            L.append(f"- Advisory: conformance {a.get('mean_conformance')}, utility "
                     f"{a.get('mean_utility_ml')}/{a.get('mean_utility_obs')}/{a.get('mean_utility_eval')} "
                     f"({len(a.get('worst_cases', []))} reviewer worst-cases).")
        L.append(f"- {REMEDIATION[sl]}")
        L.append("")

    go = [sl for sl in SLICER_NAMES if verdicts[sl]["verdict"] == "GO"]
    nogo = [sl for sl in SLICER_NAMES if verdicts[sl]["verdict"] == "NO-GO"]
    L.append("## Gate decision\n")
    L.append(f"- **GO slicers (enter Phase 1):** {', '.join(go) or '(none)'}")
    L.append(f"- **NO-GO slicers (excluded from Phase 1):** {', '.join(nogo) or '(none)'}\n")

    (HERE / "REPORT.md").write_text("\n".join(L))
    print("wrote REPORT.md")
    print("GO:", go, " NO-GO:", nogo)

    render_html(summ, mech, det, llm, rev_agg, verdicts)
    return verdicts


def render_html(summ, mech, det, llm, rev_agg, verdicts):
    rows = ""
    for sl in SLICER_NAMES:
        s = summ[sl]
        v = verdicts[sl]
        a = rev_agg.get(sl, {})
        color = "#0a7d2c" if v["verdict"] == "GO" else "#b00020"
        conf = a.get("mean_conformance", "—")
        util = (f"{a.get('mean_utility_ml')}/{a.get('mean_utility_obs')}/{a.get('mean_utility_eval')}"
                if a else "—")
        rows += (
            f"<tr><td><b>{SLICER_NAMES[sl]}</b></td><td>{TIER[sl]}</td>"
            f"<td>{s['valid']}/{s['n']} ({s['reliability_rate']:.3f})</td>"
            f"<td>{s['idempotent']}/{s['n']}</td>"
            f"<td>{conf}</td><td>{util}</td>"
            f"<td style='color:{color};font-weight:700'>{v['verdict']}</td></tr>"
        )
    worst = ""
    for sl in SLICER_NAMES:
        a = rev_agg.get(sl, {})
        items = "".join(
            f"<li><code>{ex['trace_id'][:12]}</code> conf={ex['conformance']}: {ex['note']}</li>"
            for ex in a.get("lowest_conformance", [])[:5]
        )
        m = mech.get(sl, {})
        worst += (f"<h3>{SLICER_NAMES[sl]}</h3>"
                  f"<p class=mut>single-trajectory traces: {m.get('single_trajectory_count',0)} · "
                  f"leaky openers: {m.get('leaky_openers',0)} · mean trajectories/100 steps: {m.get('mean_seg_per_100',0)}</p>"
                  f"<ul>{items or '<li>(no reviewer data)</li>'}</ul>")
    go = [sl for sl in SLICER_NAMES if verdicts[sl]['verdict'] == 'GO']
    nogo = [sl for sl in SLICER_NAMES if verdicts[sl]['verdict'] == 'NO-GO']
    html = f"""<!doctype html><html><head><meta charset=utf-8><title>Trace Slicer — Phase 0 verdict</title>
<style>
:root{{--bg:#0d1117;--fg:#e6edf3;--mut:#8b949e;--card:#161b22;--line:#30363d;--accent:#58a6ff}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Inter,sans-serif}}
.wrap{{max-width:1040px;margin:0 auto;padding:40px 28px}}
h1{{font-size:30px;margin:0 0 6px}} h2{{margin:34px 0 12px;font-size:21px;border-bottom:1px solid var(--line);padding-bottom:8px}}
h3{{margin:18px 0 4px;font-size:16px}} .mut{{color:var(--mut)}} code{{background:#21262d;padding:1px 6px;border-radius:5px;font-size:13px}}
table{{width:100%;border-collapse:collapse;margin:10px 0;background:var(--card);border-radius:10px;overflow:hidden}}
th,td{{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line);font-size:13.5px}}
th{{background:#1c2430;color:var(--mut);text-transform:uppercase;font-size:11px;letter-spacing:.04em}}
.pill{{display:inline-block;padding:3px 12px;border-radius:999px;font-weight:700;font-size:13px}}
.go{{background:rgba(10,125,44,.18);color:#3fb950;border:1px solid #238636}}
.nogo{{background:rgba(176,0,32,.18);color:#f85149;border:1px solid #b00020}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:12px 0}}
.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}
.kpi b{{font-size:24px;display:block}} ul{{margin:4px 0 14px;padding-left:20px}} li{{margin:3px 0;font-size:13.5px}}
</style></head><body><div class=wrap>
<h1>Trace Slicer Library — Phase 0 verdict</h1>
<p class=mut>Issue #141 · throwaway prototype validation on a stratified real-bucket sample · independent tiling verifier · advisory LLM panel</p>
<div class=grid>
  <div class=kpi><span class=mut>Bucket corpus</span><b>1896</b>traces</div>
  <div class=kpi><span class=mut>Deterministic sweep</span><b>{det.get('count',0)}</b>traces · {len(det.get('by_project',{}))} projects</div>
  <div class=kpi><span class=mut>Reviewer subset</span><b>{llm.get('count',0)}</b>traces · 3-persona utility</div>
</div>
<h2>Per-slicer verdict</h2>
<table><tr><th>Slicer</th><th>Tier</th><th>Reliability (independent)</th><th>Idempotent</th><th>Conformance (adv.)</th><th>Utility ml/obs/eval (adv.)</th><th>Verdict</th></tr>{rows}</table>
<div class=card><b>Gate decision:</b>
  <span class="pill go">GO → Phase 1: {', '.join(go) or '(none)'}</span>
  &nbsp;<span class="pill nogo">NO-GO: {', '.join(nogo) or '(none)'}</span>
</div>
<h2>How tiling is verified (trust the world, not the narration)</h2>
<div class=card>Every slicer emits a sorted boundary set (always including step 0); trajectories fill the gaps between boundaries with the last running to <code>total_steps-1</code>. This <b>guarantees</b> perfect tiling by construction. The judge (S3/S4) only moves boundaries — it cannot break the invariant. The verdict above is computed by an <b>independent</b> verifier that recomputes coverage / gaps / overlaps from <code>trajectories[]</code> alone, and NEVER reads a self-reported <code>tiling.valid</code>.</div>
<h2>Worst-cases surfaced (anti-cherry-pick)</h2>
{worst}
</div></body></html>"""
    (HERE / "report.html").write_text(html)
    print("wrote report.html")


if __name__ == "__main__":
    main()
