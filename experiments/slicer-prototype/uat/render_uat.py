"""Render the Trace Slicer UAT side-by-side (issue #141 follow-up).

Reads:
  /tmp/sidebyside.json   — objective 10-trace x 4-slicer matrix (this script's
                           sibling generator computes it deterministically)
  /tmp/agent-uat.json    — the slicer-uat Workflow result (10 agents driving the
                           real CLI cold), if present.
Emits: sidebyside.html
"""
from __future__ import annotations

import html
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SB = json.loads(Path("/tmp/sidebyside.json").read_text())
AG = {}
agp = Path("/tmp/agent-uat.json")
if agp.exists():
    raw = json.loads(agp.read_text())
    for r in raw.get("per_trace", []):
        AG[r["trace_id"]] = r
    AGG = raw.get("agg", {})
    MATRIX = raw.get("matrix", {})
else:
    AGG, MATRIX = {}, {}

SLN = {"s1": "S1 user-turn", "s2": "S2 change-burst", "s3": "S3 milestone", "s4": "S4 subgoal"}
TIER = {"s1": "deterministic", "s2": "deterministic", "s3": "cheap-LLM", "s4": "cheap-LLM"}


def cell(c):
    if not c:
        return "<td class=na>—</td>"
    rq = f"<span class=jbadge>+{c['reqs']}j</span>" if c.get("reqs") else ""
    chk = "✓" if c["valid"] else "✗"
    cls = "ok" if c["valid"] else "bad"
    labels = "<br>".join(html.escape(l) for l in c.get("labels", [])[:3])
    return (f"<td class='cell {cls}'><div class=ct>{c['n']}<span class=tt>traj</span>{rq} "
            f"<span class=chk>{chk}</span></div><div class=lab>{labels}</div></td>")


rows = ""
for r in SB:
    s = r["slicers"]
    rows += (
        f"<tr><td class=meta><b>{html.escape(r['label'])}</b><br>"
        f"<span class=mut>{html.escape(r['agent'])} · {r['n_steps']} steps</span></td>"
        f"{cell(s['s1'])}{cell(s['s2'])}{cell(s['s3'])}{cell(s['s4'])}</tr>"
    )

# agent-experience rows
exp_rows = ""
for r in SB:
    a = AG.get(r["trace_id"])
    if not a:
        continue
    e = a.get("experience", {})
    fr = "; ".join(html.escape(x) for x in (e.get("friction") or [])) or "<span class=mut>none</span>"
    res = "✓ all 4" if e.get("fully_resolved") else "partial"
    disc = "✓" if e.get("rc10_discoverable") else "✗"
    suf = "✓" if e.get("instruction_sufficient") else "✗"
    exp_rows += (
        f"<tr><td class=meta><b>{html.escape(r['label'])}</b></td>"
        f"<td class=ctr>{res}</td><td class=ctr>{disc}</td><td class=ctr>{suf}</td>"
        f"<td>{html.escape(e.get('verdict','') or '')}</td></tr>"
    )

agg_html = ""
if AGG:
    agg_html = (
        f"<div class=kpis>"
        f"<div class=kpi><b>{AGG.get('fully_resolved','?')}/{AGG.get('n','?')}</b>agents resolved ALL four slicers</div>"
        f"<div class=kpi><b>{AGG.get('rc10_discoverable','?')}/{AGG.get('n','?')}</b>found the rc=10 loop self-explanatory</div>"
        f"<div class=kpi><b>{AGG.get('instruction_sufficient','?')}/{AGG.get('n','?')}</b>wrote a valid --answers file first try</div>"
        f"<div class=kpi><b>{MATRIX.get('valid','?')}/{MATRIX.get('cells','?')}</b>(trace×slicer) cells tiling-valid</div>"
        f"</div>"
    )

HUMAN = html.escape("""$ opentraces trace partition <codex-trace> --by s1
s1 (deterministic): 1 trajectories over 139 steps — coverage 1.0 gaps 0 overlaps 0
  [   0..138 ] user_turn    You are working autonomously towards an objective...

$ opentraces trace partition <codex-trace> --by s2
s2 (deterministic): 3 trajectories over 139 steps — coverage 1.0 gaps 0 overlaps 0
  [   0..26  ] change_burst explore
  [  27..92  ] change_burst burst: <apply_patch>
  [  93..138 ] change_burst explore""")

AGENT = html.escape("""$ opentraces trace partition <codex-trace> --by s3   ; echo rc=$?
needs-judgment: s3 needs 9 cheap-LLM judgment(s) to finalize boundaries.
  [s3-same-outcome-1] same_outcome @step 31: Steps [27, 28, 31] all report success
    on artifact '<apply_patch>'. Do they form ONE outcome (only the last closes a
    trajectory) or DISTINCT outcomes (each closes one)?
  ... (8 more) ...
  Answer EACH with a decision + confidence, write {"answers":[{"id","decision",
  "confidence"}]} to a file, and re-run with --answers <file>.  rc=10

$ opentraces trace partition <codex-trace> --by s3 --answers a.json
s3 (cheap_llm): 16 trajectories over 139 steps — coverage 1.0 gaps 0 overlaps 0
  [   0..22  ] milestone    milestone: <test>
  ...  rc=0""")

doc = f"""<!doctype html><html><head><meta charset=utf-8><title>Trace Slicer — UAT side-by-side</title>
<style>
:root{{--bg:#0d1117;--fg:#e6edf3;--mut:#8b949e;--card:#161b22;--line:#30363d;--accent:#58a6ff;--ok:#3fb950;--bad:#f85149;--j:#d29922}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--fg);font:14.5px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Inter,sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:38px 26px}}
h1{{font-size:29px;margin:0 0 4px}} h2{{margin:34px 0 12px;font-size:20px;border-bottom:1px solid var(--line);padding-bottom:8px}}
.mut{{color:var(--mut)}} code,pre{{font-family:'SF Mono',ui-monospace,Menlo,monospace}}
table{{width:100%;border-collapse:collapse;margin:10px 0;background:var(--card);border-radius:10px;overflow:hidden}}
th,td{{padding:9px 11px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top;font-size:13px}}
th{{background:#1c2430;color:var(--mut);text-transform:uppercase;font-size:10.5px;letter-spacing:.05em}}
td.meta{{white-space:nowrap}} td.na{{color:var(--mut);text-align:center}}
.cell .ct{{font-size:17px;font-weight:700}} .cell .tt{{font-size:10px;color:var(--mut);font-weight:400;margin-left:3px}}
.cell .chk{{float:right}} .cell.ok .chk{{color:var(--ok)}} .cell.bad .chk{{color:var(--bad)}}
.cell .lab{{font-size:10.5px;color:var(--mut);margin-top:4px;font-family:ui-monospace,monospace;line-height:1.35}}
.jbadge{{display:inline-block;background:rgba(210,153,34,.18);color:var(--j);border:1px solid #9e7016;border-radius:5px;font-size:10px;padding:0 5px;margin-left:4px;vertical-align:middle;font-weight:600}}
.ctr{{text-align:center;font-weight:600}}
pre{{background:#0a0e14;border:1px solid var(--line);border-radius:9px;padding:14px 16px;overflow:auto;font-size:12px;line-height:1.5}}
.kpis{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:11px;margin:14px 0}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px}} .kpi b{{font-size:23px;display:block;color:var(--accent)}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}} .legend{{color:var(--mut);font-size:12.5px;margin:6px 0 0}}
.note{{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:8px;padding:12px 15px;margin:12px 0;font-size:13.5px}}
@media(max-width:820px){{.grid2,.kpis{{grid-template-columns:1fr}}}}
</style></head><body><div class=wrap>
<h1>Trace Slicer Library — UAT, side-by-side</h1>
<p class=mut>10 real bucket traces (claude + codex; tiny → 1,490 steps; control-leak / autonomous / subagent-heavy / error-heavy) × the four slicers, driven through the shipped <code>opentraces trace partition</code> CLI. Every cell independently tiling-checked.</p>
{agg_html}
<h2>The matrix — trajectories per (trace × slicer)</h2>
<table><tr><th>trace</th><th>S1 user-turn<br><span class=mut>deterministic</span></th><th>S2 change-burst<br><span class=mut>deterministic</span></th><th>S3 milestone<br><span class=mut>cheap-LLM</span></th><th>S4 subgoal<br><span class=mut>cheap-LLM</span></th></tr>
{rows}</table>
<p class=legend><b>N traj</b> = trajectories in the final tiling · <span class=jbadge>+Kj</span> = K cheap-LLM judgments the agent loop asks before the final tiling · <span style='color:var(--ok)'>✓</span> = tiling recomputed valid (coverage 1.0 / 0 gaps / 0 overlaps). S3/S4 counts shown for the deterministic default judge; the agent judge moves boundaries within the same tiling guarantee.</p>

<div class=note><b>What the matrix says.</b> S2 always yields useful structure (even where S1 collapses to one blob on autonomous/codex sessions with no user turns). S1 mirrors the human-ask count. S3 is the most granular (one trajectory per verified outcome). S4 shares S1's default shape but asks by far the most judgments — its segmentation is realised only by answering them. The cheap-LLM cost is the judgment column, and it grows with trace size: S3 stays gentle (≤56 on a 1,490-step trace), S4 grows steeply (216 on the same trace).</div>

<h2>What it actually looks like</h2>
<div class=grid2>
<div><h3 class=mut>Human / one-shot (S1, S2)</h3><pre>{HUMAN}</pre></div>
<div><h3 class=mut>Agent loop (S3/S4): rc=10 → answers → rc=0</h3><pre>{AGENT}</pre></div>
</div>

<h2>Agent experience — fresh agents driving the CLI cold</h2>
<p class=mut>Each agent was given a trace and the command name only — it had to discover the rc=10 → <code>--answers</code> → rc=0 protocol from the CLI's own output.</p>
<table><tr><th>trace</th><th>resolved</th><th>rc=10 self-explanatory</th><th>valid answers 1st try</th><th>verdict</th></tr>
{exp_rows or '<tr><td colspan=5 class=mut>agent UAT pending…</td></tr>'}</table>
</div></body></html>"""

(HERE / "sidebyside.html").write_text(doc)
print("wrote", HERE / "sidebyside.html")
