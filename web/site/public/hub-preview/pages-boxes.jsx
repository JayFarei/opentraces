// pages-boxes.jsx — Boxes: a thin operational view, not a cloud console.
// Placement class, honest sandbox_tier (derived, never relabelled upward),
// lease lifecycle. Usage is metered by the hour, per environment.

const BOX_STATE_TONE = { leased: "ok", provisioning: "warn", idle: "dim", reaped: "dim" };

// ── usage aggregates (derived from V2_BOX_HOURS / V2_BOX_ENVS) ──
const BU_ENVS = V2_BOX_ENVS.map(env => {
  const hours = +V2_BOX_HOURS.reduce((a, d) => a + d[env.k], 0).toFixed(1);
  return { ...env, hours, cost: +(hours * env.rate).toFixed(1) };
});
const BU_TOTAL_H = +BU_ENVS.reduce((a, e) => a + e.hours, 0).toFixed(1);
const BU_TOTAL_CR = +BU_ENVS.reduce((a, e) => a + e.cost, 0).toFixed(1);
const BU_BILLED_CR = +BU_ENVS.filter(e => e.rate > 0).reduce((a, e) => a + e.cost, 0).toFixed(1);

function BuRing({ pct, color, size = 18 }) {
  const r = (size - 5) / 2, c = 2 * Math.PI * r;
  const p = Math.max(0, Math.min(1, pct || 0));
  const h = size / 2;
  return (
    <svg className="bu-ring" width={size} height={size} viewBox={"0 0 " + size + " " + size} aria-hidden="true">
      <circle cx={h} cy={h} r={r} fill="none" stroke="var(--surface-3)" strokeWidth="2.5"></circle>
      <circle cx={h} cy={h} r={r} fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round"
        strokeDasharray={(c * p) + " " + c} transform={"rotate(-90 " + h + " " + h + ")"}></circle>
    </svg>
  );
}

function BoxUsageOverview() {
  return (
    <div className="ev-card bu-overview">
      <div className="ev-card-head">
        Environments <span className="ev-card-hint">hours leased vs included — month to date</span>
        <span className="bu-head-right mono">{BU_BILLED_CR} / {V2_BOX_BUDGET} cr</span>
      </div>
      <div className="bu-budget-bar"><span style={{ width: (BU_BILLED_CR / V2_BOX_BUDGET * 100) + "%" }}></span></div>
      {BU_ENVS.map(e => (
        <div key={e.k} className="bu-row">
          <BuRing pct={e.included ? e.hours / e.included : 0} color={e.color} />
          <span className="bu-name mono">{e.label}</span>
          <span className="bu-rate mono">{e.rate > 0 ? e.rate + " cr/h" : "never bills"}</span>
          <span className="bu-use mono">
            {e.hours} h{e.included ? " / " + e.included + " h" : ""}
          </span>
          <span className="bu-cost mono">{e.rate > 0 ? e.cost + " cr" : "—"}</span>
        </div>
      ))}
    </div>
  );
}

const AI_LINE_COLOR = "var(--c-exec)";
const BU_AI_TOTAL = +V2_BOX_HOURS.reduce((a, d) => a + d.aiCredits, 0).toFixed(1);

function BoxHoursChart() {
  const [hov, setHov] = React.useState(null);
  const max = Math.max(...V2_BOX_HOURS.map(d => d.container + d.microvm + d.local));
  const top = Math.ceil(max);
  const aiTop = Math.ceil(Math.max(...V2_BOX_HOURS.map(d => d.aiCredits)) / 5) * 5;
  const fmtY = v => (v % 1 ? v.toFixed(1) : v) + " h";
  const stack = ["local", "microvm", "container"]; // bottom → top
  const envBy = {}; V2_BOX_ENVS.forEach(e => { envBy[e.k] = e; });
  const hd = hov != null ? V2_BOX_HOURS[hov] : null;
  const N = V2_BOX_HOURS.length;
  // AI-credit polyline in a 0–100 viewBox; x centered on each bar column
  const pts = V2_BOX_HOURS.map((d, i) => {
    const x = ((i + 0.5) / N) * 100;
    const y = 100 - (d.aiCredits / aiTop) * 100;
    return [x, y];
  });
  const linePath = pts.map((p, i) => (i ? "L" : "M") + p[0] + " " + p[1]).join(" ");
  return (
    <div className="ev-card bu-chart">
      <div className="ev-card-head">
        Box hours &amp; AI credits <span className="ev-card-hint">daily lease hours by environment, with model spend overlaid</span>
        <span className="bu-head-right bu-readout mono">
          {hd
            ? hd.label + " — " + (hd.container + hd.microvm + hd.local).toFixed(1) + " h · " + hd.aiCredits + " ai cr"
            : "metered daily · updated 40s ago"}
        </span>
      </div>
      <div className="bu-total">
        <BuRing pct={BU_BILLED_CR / V2_BOX_BUDGET} color="var(--c-user)" size={20} />
        <span className="mono">{BU_TOTAL_H} h · {BU_BILLED_CR} box cr · {BU_AI_TOTAL} ai cr month to date</span>
      </div>
      <div className="bu-plot bu-plot-dual">
        {[0, 1, 2, 3, 4].map(i => (
          <React.Fragment key={i}>
            <div className="bu-gridline" style={{ bottom: (i * 25) + "%" }}></div>
            <div className="bu-ylab mono" style={{ bottom: (i * 25) + "%" }}>{fmtY(top * i / 4)}</div>
            <div className="bu-ylab bu-ylab-r mono" style={{ bottom: (i * 25) + "%", color: "var(--c-exec)" }}>{Math.round(aiTop * i / 4)}</div>
          </React.Fragment>
        ))}
        <div className="bu-bars">
          {V2_BOX_HOURS.map((d, i) => (
            <div key={i} className="bu-colwrap" data-hov={hov === i || undefined}
              onMouseEnter={() => setHov(i)} onMouseLeave={() => setHov(null)}>
              <div className="bu-col">
                {stack.slice().reverse().map(k => (
                  d[k] > 0 && <span key={k} className="bu-seg" style={{ height: (d[k] / top * 100) + "%", background: envBy[k].color }}></span>
                ))}
              </div>
            </div>
          ))}
        </div>
        <svg className="bu-ai-line" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          <path d={linePath} fill="none" stroke={AI_LINE_COLOR} strokeWidth="1.6"
            vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round"></path>
        </svg>
        {hd && (
          <span className="bu-ai-dot" style={{
            left: "calc(46px + (100% - 80px) * " + (pts[hov][0] / 100) + ")",
            top: (pts[hov][1]) + "%", background: AI_LINE_COLOR,
          }}></span>
        )}
      </div>
      <div className="bu-xlabs">
        {V2_BOX_HOURS.map((d, i) => (
          <span key={i} className="bu-xlab mono">{i % 4 === 2 ? d.label : ""}</span>
        ))}
      </div>
      <div className="bu-leg">
        {BU_ENVS.map(e => (
          <div key={e.k} className="bu-leg-row">
            <span className="bu-dot" style={{ background: e.color }}></span>
            <span className="bu-name mono">{e.label}</span>
            <span className="bu-leg-h mono">{e.hours} h</span>
            <span className="bu-leg-cr mono">{e.rate > 0 ? e.cost + " cr" : "0 cr"}</span>
            <span className="bu-leg-pct mono">{e.rate > 0 ? Math.round(e.cost / BU_BILLED_CR * 100) + "%" : "—"}</span>
          </div>
        ))}
        <div className="bu-leg-row">
          <span className="bu-dot bu-dot-line" style={{ background: AI_LINE_COLOR }}></span>
          <span className="bu-name mono">AI credits</span>
          <span className="bu-leg-h mono">line</span>
          <span className="bu-leg-cr mono">{BU_AI_TOTAL} cr</span>
          <span className="bu-leg-pct mono">spend</span>
        </div>
      </div>
    </div>
  );
}

function BoxesPage({ onOpenBench }) {
  return (
    <div className="landing landing-page v2-page">
      <V2Hero
        plain="Boxes & AI"
        tech="box · lease · ai credits"
        subtitle="Everything metered: boxes bill by the hour, per environment; AI credits are spent per model call."
        scope={`${V2_BOXES.length} boxes · ${V2_BOXES.filter(b => b.state === "leased").length} leased`}
      />

      <BoxUsageMeters>
        <BoxHoursChart />
      </BoxUsageMeters>
    </div>
  );
}

window.BoxesPage = BoxesPage;
