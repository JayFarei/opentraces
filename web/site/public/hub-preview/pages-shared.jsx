// Shared visual primitives for the new intelligence pages.
// - StreamChart: stacked-area chart with smooth bezier paths
// - MatchedRow: a 2-line trace match (user + agent) with metadata
// - HeroBar: page hero with title + subtitle + actions

// Smooth a series of [x, y] points into an SVG path using Catmull-Rom → cubic.
function smoothPath(points) {
  if (points.length < 2) return "";
  const d = ["M" + points[0][0] + " " + points[0][1]];
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i - 1] || points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] || p2;
    const cp1x = p1[0] + (p2[0] - p0[0]) / 6;
    const cp1y = p1[1] + (p2[1] - p0[1]) / 6;
    const cp2x = p2[0] - (p3[0] - p1[0]) / 6;
    const cp2y = p2[1] - (p3[1] - p1[1]) / 6;
    d.push(`C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${p2[0]} ${p2[1]}`);
  }
  return d.join(" ");
}

// StreamChart: stacked-area chart. Series come from buildStreamSeries.
// Renders top N series (by total) with the rest grouped into a faint "other" band.
function StreamChart({ data, height = 200, topN = 4, xAxis = ["9:10 AM", "9:25 AM", "9:40 AM", "9:55 AM", "10:10 AM"], yAxisCap }) {
  const { series } = data;
  // Sort by total — top N keep their color, rest collapsed into "other"
  const totals = series.map(s => ({ id: s.id, total: s.values.reduce((a, b) => a + b, 0) }));
  totals.sort((a, b) => b.total - a.total);
  const topIds = new Set(totals.slice(0, topN).map(t => t.id));
  const top    = series.filter(s => topIds.has(s.id));
  const rest   = series.filter(s => !topIds.has(s.id));
  const restCount = rest.length;
  const display = [...top];
  if (rest.length > 0) {
    const restValues = rest[0].values.map((_, i) => rest.reduce((a, s) => a + s.values[i], 0));
    display.push({ id: "__rest__", name: `+${restCount} more`, color: "color-mix(in oklch, var(--fg-mute) 30%, transparent)", values: restValues, isRest: true });
  }

  const n = display[0]?.values.length || 0;
  // Stack
  const stack = display.map((s, i) => {
    const baseline = i === 0 ? Array(n).fill(0) : null;
    return baseline;
  });
  const cumulative = Array(n).fill(0);
  const stackedSeries = display.map(s => {
    const base = cumulative.slice();
    const top  = s.values.map((v, i) => cumulative[i] + v);
    for (let i = 0; i < n; i++) cumulative[i] = top[i];
    return { ...s, base, top };
  });

  const maxY = Math.max(...cumulative, 1);
  const yCap = yAxisCap || Math.ceil(maxY / 40) * 40;

  // Layout
  const W = 1000;
  const H = height;
  const padL = 36;
  const padR = 8;
  const padT = 6;
  const padB = 22;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const xAt = (i) => padL + (i / (n - 1)) * innerW;
  const yAt = (v) => padT + innerH - (v / yCap) * innerH;

  return (
    <div className="streamchart">
      <div className="sc-legend">
        {display.map((s) => (
          <span key={s.id} className={"sc-leg" + (s.isRest ? " rest" : "")}>
            <span className="sc-leg-dot" style={{ background: s.color }} />
            <span className="sc-leg-name">{s.name}</span>
          </span>
        ))}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="sc-svg" role="img" aria-label="Activity over time">
        {/* y axis ticks */}
        {[0, 0.5, 1].map((p, i) => (
          <g key={i}>
            <line x1={padL} x2={W - padR} y1={padT + innerH * (1 - p)} y2={padT + innerH * (1 - p)} stroke="var(--border)" strokeWidth="0.5" strokeDasharray="2 3" opacity="0.5" />
            <text x={padL - 6} y={padT + innerH * (1 - p) + 3} textAnchor="end" fontSize="9.5" fill="var(--fg-mute)" fontFamily="var(--font-mono)">{Math.round(yCap * p)}</text>
          </g>
        ))}
        {/* areas */}
        {stackedSeries.map((s) => {
          const topPts = s.top.map((v, i) => [xAt(i), yAt(v)]);
          const basePts = s.base.map((v, i) => [xAt(i), yAt(v)]).reverse();
          const dTop = smoothPath(topPts);
          const dBase = smoothPath(basePts);
          // Build closed path: top, line to last base, smooth base back
          const closeD = dTop + " L " + basePts[0][0] + " " + basePts[0][1] + " " + dBase.replace(/^M[^C]*/, "") + " Z";
          return (
            <path
              key={s.id}
              d={closeD}
              fill={s.color}
              opacity={s.isRest ? 0.45 : 0.78}
              stroke={s.color}
              strokeOpacity={s.isRest ? 0.5 : 0.9}
              strokeWidth="1"
            />
          );
        })}
        {/* x axis labels */}
        {xAxis.map((lbl, i) => {
          const x = padL + (i / (xAxis.length - 1)) * innerW;
          return <text key={i} x={x} y={H - 6} textAnchor={i === 0 ? "start" : i === xAxis.length - 1 ? "end" : "middle"} fontSize="9.5" fill="var(--fg-mute)" fontFamily="var(--font-mono)">{lbl}</text>;
        })}
      </svg>
    </div>
  );
}

// MatchedRow: one matched trace, two lines (user + agent), plus meta
function MatchedRow({ row, label, onOpen }) {
  return (
    <button className="match-row" onClick={onOpen}>
      <div className="mr-head">
        <span className="mr-title">{row.title}</span>
        <span className="mr-meta">
          <AgentGlyph agent={row.agent} size={11} />
          <span className="mr-agent">{row.agent.name}</span>
          <span className="dot-sep" />
          <RepoLabel repo={row.repo} />
          <span className="dot-sep" />
          <span className="mr-when">{row.when}</span>
        </span>
        <span className="mr-pill">{label}</span>
      </div>
      <div className="mr-msg user">
        <span className="mr-avatar"><Icon name="user" size={11} /></span>
        <span className="mr-text">{row.userMsg}</span>
      </div>
      <div className="mr-msg agent">
        <span className="mr-avatar bot"><Icon name="bot" size={11} /></span>
        <span className="mr-text">{row.agentMsg}</span>
      </div>
    </button>
  );
}

// Page hero used by Intents / Evals / Alerts / Self-Improving
function PageHero({ kicker, title, subtitle, actions, scope }) {
  return (
    <header className="page-hero">
      <div className="ph-head">
        <div className="ph-titles">
          {kicker && <div className="ph-kicker">{kicker}</div>}
          <h1 className="ph-h1">{title}</h1>
          {subtitle && <p className="ph-sub">{subtitle}</p>}
        </div>
        {actions && <div className="ph-actions">{actions}</div>}
      </div>
      {scope && (
        <div className="ph-scope">
          <Icon name="clock" size={12} />
          <span className="ph-scope-lbl">Scope</span>
          <span className="ph-scope-v mono">{scope}</span>
        </div>
      )}
    </header>
  );
}

// Small toolbar button used inside cards (Add intent, Run, Auto-generate)
function ToolBtn({ icon, label, onClick, primary, disabled, ...rest }) {
  return (
    <button
      className={"tool-btn" + (primary ? " primary" : "") + (disabled ? " disabled" : "")}
      onClick={onClick}
      disabled={disabled}
      {...rest}
    >
      {icon && <Icon name={icon} size={12} />}
      <span>{label}</span>
    </button>
  );
}

window.StreamChart = StreamChart;
window.MatchedRow = MatchedRow;
window.PageHero = PageHero;
window.ToolBtn = ToolBtn;
window.smoothPath = smoothPath;
