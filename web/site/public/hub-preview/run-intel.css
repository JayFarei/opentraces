// Run-intelligence layer — presentational components (read-only overlays).
// FidelityBadge · RunHealthStrip · RowMarks (waste + health glyphs w/ hover) ·
// ContextWasteChip · HealthCounts.

function FidelityBadge({ fidelity }) {
  const otel = fidelity === "otel";
  return (
    <span
      className="fidelity-badge"
      data-fidelity={fidelity}
      title={otel
        ? "Wire-fidelity — char counts & commands captured from full OTel wire data"
        : "Record — values reconstructed from the run record"}
    >
      <span className="fb-dot" />
      {otel ? "wire-fidelity" : "record"}
    </span>
  );
}

// Generic hover-popover marker used for both waste + health marks.
function HoverMark({ className, style, label, popClass, children, ...rest }) {
  const [open, setOpen] = React.useState(false);
  return (
    <span
      className={"row-mark " + className}
      style={style}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onClick={(e) => e.stopPropagation()}
      {...rest}
    >
      {label}
      {open && <div className={"mark-pop " + (popClass || "")}>{children}</div>}
    </span>
  );
}

function WastePop({ finding }) {
  const sevColor = finding.severity === "medium" ? "var(--c-waste-med)" : "var(--c-waste-low)";
  return (
    <>
      <div className="mp-head">
        <span className="mp-kind" style={{ background: sevColor, color: finding.severity === "medium" ? "#1a1206" : "#fff" }}>
          {WASTE_PATTERN_LABEL[finding.pattern] || finding.pattern}
        </span>
        <span className="mp-sev">{finding.severity}</span>
      </div>
      <div className="mp-reason">{finding.reason}</div>
      <div className="mp-ev">{finding.evidence}</div>
      <div className="mp-foot">
        <span className="mp-conf">conf {Math.round(finding.confidence * 100)}%</span>
        <span className="mp-node mono">{finding.node_id}</span>
      </div>
    </>
  );
}

function HealthPop({ signal }) {
  const cfg = HEALTH_KINDS[signal.kind];
  return (
    <>
      <div className="mp-head">
        <span className="mp-kind" style={{ background: cfg.color }}>{cfg.glyph} {cfg.label}</span>
        <span className="mp-sev">{signal.severity}</span>
      </div>
      <div className="mp-reason">{signal.reason}</div>
      <div className="mp-ev">“{signal.matched_text}”</div>
      <div className="mp-foot">
        <span className="mp-conf">conf {Math.round(signal.confidence * 100)}%</span>
        <span className="mp-node mono">{signal.node_id}</span>
      </div>
    </>
  );
}

// Left-gutter marker cluster for a single timeline row.
function RowMarks({ wasteFindings, healthSignals, healthFilter }) {
  const waste = wasteFindings && wasteFindings.length
    ? wasteFindings.slice().sort((a, b) => (a.severity === "medium" ? -1 : 1))[0]
    : null;
  const signal = healthSignals && healthSignals.length ? healthSignals[0] : null;
  if (!waste && !signal) return null;

  return (
    <span className="row-marks" aria-hidden="false">
      {signal && (() => {
        const cfg = HEALTH_KINDS[signal.kind];
        const dim = healthFilter ? (signal.kind !== healthFilter ? "true" : "false") : undefined;
        return (
          <HoverMark
            className="health"
            style={{ background: cfg.color, "--mk-color": cfg.color }}
            label={cfg.glyph}
            data-dim={dim}
          >
            <HealthPop signal={signal} />
          </HoverMark>
        );
      })()}
      {waste && (
        <HoverMark className="waste" label="!" style={{}} data-sev={waste.severity}>
          <WastePop finding={waste} />
        </HoverMark>
      )}
    </span>
  );
}

function RunHealthStrip({ health, fidelity, activeKind, onKind }) {
  return (
    <div className="run-health" data-status={health.status} data-filtered={activeKind ? "true" : "false"}>
      <div className="rh-lead">
        <span className="rh-title">Run health</span>
        <span className="rh-status"><span className="dot" />{health.status}</span>
      </div>
      <div className="rh-pills">
        {HEALTH_ORDER.map(k => {
          const cfg = HEALTH_KINDS[k];
          const c = health.counts[k] || 0;
          const zero = c === 0;
          const on = activeKind === k;
          return (
            <button
              key={k}
              className="rh-pill"
              data-zero={zero ? "true" : "false"}
              data-on={on ? "true" : "false"}
              style={{ "--rhp-color": cfg.color }}
              onClick={() => { if (!zero) onKind(on ? null : k); }}
              title={zero ? `No ${cfg.label} signals` : `${c} ${cfg.label} ${c === 1 ? "signal" : "signals"} — click to focus`}
            >
              <span className="rhp-glyph" style={{ background: cfg.color }}>{cfg.glyph}</span>
              <span className="rhp-count">{c}</span>
              <span className="rhp-label">{cfg.label}</span>
            </button>
          );
        })}
      </div>
      <div className="rh-spacer" />
      {activeKind && <button className="rh-clear" onClick={() => onKind(null)}>clear focus</button>}
      <FidelityBadge fidelity={fidelity} />
    </div>
  );
}

function ContextWasteChip({ summary, on, onToggle }) {
  const n = summary?.context_waste_count || 0;
  if (!n) return null;
  return (
    <button
      className="cw-chip"
      data-on={on ? "true" : "false"}
      onClick={onToggle}
      title="Filter the Trail timeline to context-waste rows"
    >
      <span className="cwc-mark">!</span>
      <span className="cwc-label">context-waste: <span className="cwc-n">{n}</span></span>
    </button>
  );
}

function HealthCounts({ counts, tiny }) {
  const kinds = HEALTH_ORDER.filter(k => (counts[k] || 0) > 0);
  if (!kinds.length) return null;
  return (
    <span className={"health-counts" + (tiny ? " tiny" : "")}>
      {kinds.map(k => {
        const cfg = HEALTH_KINDS[k];
        return (
          <span className="hc-pill" key={k} title={`${counts[k]} ${cfg.label}`}>
            <span className="hc-glyph" style={{ background: cfg.color }}>{cfg.glyph}</span>
            <span className="hc-n">{counts[k]}</span>
          </span>
        );
      })}
    </span>
  );
}

window.FidelityBadge = FidelityBadge;
window.RowMarks = RowMarks;
window.RunHealthStrip = RunHealthStrip;
window.ContextWasteChip = ContextWasteChip;
window.HealthCounts = HealthCounts;
