// Trail (Visual) — port of hub-next/src/components/trajectory
// Timeline → GridHead + TimelineRow → ActionCell / GitLane / TelemetryCell / CtxCell

const GRID_TEMPLATE =
  "var(--col-rail) repeat(6, minmax(0, 1fr)) var(--col-git-sep) " +
  "var(--col-git-work) var(--col-git-local) var(--col-git-remote) " +
  "var(--col-git-sep) var(--col-telem) var(--col-telem) var(--col-telem) var(--col-telem)";

const PHASE_ORDER = ["user", "plan", "think", "read", "exec", "write"];

// ── ActionCell ────────────────────────────────────────────────
function ActionCell({ step, actionClass, cellClass, focused, hasError, onClick }) {
  const isActive = actionClass === cellClass;
  return (
    <div
      className={"lane" + (isActive ? " active" : "") + (cellClass === "user" ? " user-lane" : "")}
      data-cell-class={cellClass}
      data-active={isActive || undefined}
      onClick={isActive ? onClick : undefined}
    >
      {isActive ? <ActionNode step={step} actionClass={actionClass} focused={focused} hasError={hasError} /> : null}
    </div>
  );
}

function ActionNode({ step, actionClass, focused, hasError }) {
  // shape: dot for most, square for write (matches hub-next)
  const isSquare = actionClass === "write";
  const tcs = step.tool_calls || [];
  // optional inline summary on focused row
  const summary = focused && tcs.length ? summarizeToolCall(tcs[0]) : null;
  return (
    <span className={"node " + actionClass} data-shape={isSquare ? "sq" : "dot"} data-error={hasError || undefined}>
      <span className="glyph" />
      {summary ? <span className="node-label">{summary}</span> : null}
    </span>
  );
}

// ── GitLane ───────────────────────────────────────────────────
function GitLane({ variant, snapshot, trunk = "none", sha, onCommitClick }) {
  const isCommitEvent = snapshot.event === "commit" || snapshot.event === "commit+push";
  const isPushEvent = snapshot.event === "push" || snapshot.event === "commit+push" || snapshot.pushed;
  const isEditEvent = snapshot.event === "edit";
  const showEvent =
    (variant === "work" && isEditEvent) ||
    (variant === "local" && isCommitEvent) ||
    (variant === "remote" && isPushEvent);
  const workingCount = snapshot.working.size;
  const showCount = variant === "work" && !isEditEvent && workingCount > 0;
  const showBar =
    (variant === "work" && workingCount > 0) ||
    (variant !== "work" && trunk !== "none" && trunk !== "cap-single");
  const barClass = [
    "bar",
    trunk === "cap-top" ? "cap-top" : "",
    trunk === "cap-bottom" ? "cap-bottom" : "",
  ].filter(Boolean).join(" ");

  const subject =
    variant === "local" ? (snapshot.commitSubject || "commit") :
    variant === "remote" ? "pushed" : null;

  return (
    <div className={"git-lane " + (variant === "remote" ? "push" : variant)} data-git-lane={variant}>
      {showBar ? <span className={barClass} aria-hidden="true" /> : null}
      {showEvent ? (
        <button
          type="button"
          onClick={(e) => { if (onCommitClick) { e.stopPropagation(); onCommitClick(); } }}
          className="ev-btn"
        >
          <span className="ev" />
          {subject ? (
            <span className="subject">
              {sha ? <span className="sha">{sha.slice(0, 7)}</span> : null}
              <span className="msg">{truncate(subject, 60)}</span>
            </span>
          ) : null}
        </button>
      ) : null}
      {showCount ? <span className="count">{workingCount}</span> : null}
    </div>
  );
}

// ── TelemetryCell + CtxCell ──────────────────────────────────
function TelemetryCell({ kind, value, fillPct, title }) {
  const pct = typeof fillPct === "number" ? Math.max(0, Math.min(100, fillPct)) : null;
  const cls = kind === "tokOut" ? "telem tok out" : kind === "tokIn" ? "telem tok" : "telem time";
  const text = kind === "dur" ? formatShort(value) : fmtTok(value);
  return (
    <div className={cls} data-telem-kind={kind} title={title}>
      <div className="bar" aria-hidden="true">{pct !== null && <div className="fill" style={{ width: pct + "%" }} />}</div>
      <div className="val">{text}</div>
    </div>
  );
}

function CtxCell({ pct }) {
  return (
    <div className="telem ctx">
      <div className="bar" aria-hidden="true">{pct != null && <div className="fill" style={{ width: Math.min(100, pct) + "%" }} />}</div>
      <div className="val">{pct != null ? (pct >= 10 ? Math.round(pct) + "%" : pct.toFixed(1) + "%") : "—"}</div>
    </div>
  );
}

// ── GridHead ──────────────────────────────────────────────────
function GridHead({ trace, classes, stages, compact }) {
  const counts = { user: 0, plan: 0, think: 0, read: 0, exec: 0, write: 0 };
  classes.forEach(c => { if (counts[c] != null) counts[c]++; });

  const lastSnap = stages.snapshots[stages.snapshots.length - 1];
  const workingTouched = new Set();
  for (const sn of stages.snapshots) for (const f of sn.working.keys()) workingTouched.add(f);
  const workingCount = workingTouched.size;
  const localCount = lastSnap ? lastSnap.local.length : 0;
  const remoteCount = lastSnap ? lastSnap.remote.length : 0;

  let tokIn = 0, tokOut = 0;
  for (const s of trace.steps) {
    const tu = s.token_usage || {};
    tokIn += (tu.input_tokens || 0) + (tu.cache_write_tokens || 0);
    tokOut += (tu.output_tokens || 0);
  }
  const elapsedSec = trace.timestamp_start && trace.timestamp_end
    ? Math.max(0, (Date.parse(trace.timestamp_end) - Date.parse(trace.timestamp_start)) / 1000) : 0;

  const tag = (k) => ({ background: `color-mix(in oklch, var(--c-${k}) 18%, var(--bg))`, color: `var(--c-${k})` });
  const orDash = (n) => n > 0 ? String(n) : "—";

  const ACTION_COLS = [
    { k: "user", l: "USER" }, { k: "plan", l: "PLAN" }, { k: "think", l: "THINK" },
    { k: "read", l: "READ" }, { k: "exec", l: "EXEC" }, { k: "write", l: "WRITE" },
  ];
  const GIT_COLS = [
    { k: "working", l: "WORKING", c: workingCount },
    { k: "local", l: "LOCAL", c: localCount },
    { k: "remote", l: "REMOTE", c: remoteCount },
  ];
  const TEL_COLS = [
    { k: "tokIn", l: "TOK IN", t: tokIn > 0 ? fmtTok(tokIn) : "—" },
    { k: "tokOut", l: "TOK OUT", t: tokOut > 0 ? fmtTok(tokOut) : "—" },
    { k: "ctx", l: "CTX", t: "—" },
    { k: "elapsed", l: "ELAPSED", t: elapsedSec > 0 ? formatShort(elapsedSec) : "—" },
  ];

  const gridStyle = { gridTemplateColumns: GRID_TEMPLATE };

  return (
    <div className="grid-head" data-compact={compact ? "true" : "false"}>
      <div className="inner">
        <div className="trajectory-grid-cols group-row" style={gridStyle}>
          <div className="gh" />
          <div className="gh action" style={{ gridColumn: "span 6" }}>ACTIONS</div>
          <div className="gh" />
          <div className="gh git" style={{ gridColumn: "span 3" }}>GIT TREE</div>
          <div className="gh" />
          <div className="gh tel" style={{ gridColumn: "span 4" }}>TELEMETRY</div>
        </div>
        <div className="trajectory-grid-cols col-row" style={gridStyle}>
          <div className="hc rail">
            <span className="hc-count">{trace.steps.length}</span>
            <span className="lbl">STEP</span>
          </div>
          {ACTION_COLS.map(c => (
            <div key={c.k} className={"hc" + (counts[c.k] ? "" : " empty")}>
              <span className="hc-count">{orDash(counts[c.k])}</span>
              <span className="tag" style={tag(c.k)}>{c.l}</span>
            </div>
          ))}
          <div className="hc sep" aria-hidden="true" />
          {GIT_COLS.map(c => (
            <div key={c.k} className={"hc g-" + c.k + (c.c ? "" : " empty")}>
              <span className="hc-count">{orDash(c.c)}</span>
              <span className="tag">{c.l}</span>
            </div>
          ))}
          <div className="hc sep" aria-hidden="true" />
          {TEL_COLS.map(c => (
            <div key={c.k} className={"hc" + (c.t === "—" ? " empty" : "")}>
              <span className="hc-count">{c.t}</span>
              <span className="lbl">{c.l}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── TimelineRow ───────────────────────────────────────────────
function TimelineRow({ step, stepIndex, actionClass, snapshot, telemetry, maxima, focused, trunk, sha, onFocus, onOpen, onCommitOpen, wasteFindings, healthSignals, healthFilter }) {
  const hasError = (step.observations || []).some(o => o.error);
  const tokInPct = maxima.tokIn > 0 ? (telemetry.tokIn / maxima.tokIn) * 100 : 0;
  const tokOutPct = maxima.tokOut > 0 ? (telemetry.tokOut / maxima.tokOut) * 100 : 0;
  const durPct = maxima.elapsed > 0 ? (telemetry.elapsed / maxima.elapsed) * 100 : 0;

  const railNum = String(stepIndex).padStart(3, "0");
  const railTime = stepIndex === 0 ? "—" : formatShort(telemetry.elapsed);
  const railTitle = `step ${stepIndex} · took ${formatShort(telemetry.elapsed)}`;

  const wasteSev = (wasteFindings && wasteFindings.length)
    ? (wasteFindings.some(f => f.severity === "medium") ? "medium" : "low")
    : undefined;

  return (
    <div
      data-step={stepIndex}
      data-focused={focused || undefined}
      data-class={actionClass}
      data-waste={wasteSev}
      onClick={onOpen}
      onMouseEnter={onFocus}
      style={{ gridTemplateColumns: GRID_TEMPLATE }}
      className={"tj-row" + (focused ? " focused" : "")}
    >
      <RowMarks wasteFindings={wasteFindings} healthSignals={healthSignals} healthFilter={healthFilter} />

      <div className="rail" title={railTitle}>
        <span className="n">{railNum}</span>
        <span className="t">{railTime}</span>
      </div>

      {PHASE_ORDER.map(cls => (
        <ActionCell
          key={cls}
          step={step}
          actionClass={actionClass}
          cellClass={cls}
          focused={focused}
          hasError={hasError}
          onClick={onOpen}
        />
      ))}

      <div className="sep" aria-hidden="true" />

      <GitLane variant="work"   snapshot={snapshot} trunk={trunk.work}   />
      <GitLane variant="local"  snapshot={snapshot} trunk={trunk.local}  sha={sha} onCommitClick={onCommitOpen} />
      <GitLane variant="remote" snapshot={snapshot} trunk={trunk.remote} sha={sha} onCommitClick={onCommitOpen} />

      <div className="sep" aria-hidden="true" />

      <TelemetryCell kind="tokIn"  value={telemetry.tokIn}  fillPct={tokInPct} />
      <TelemetryCell kind="tokOut" value={telemetry.tokOut} fillPct={tokOutPct} />
      <CtxCell pct={telemetry.ctxPct} />
      <TelemetryCell kind="dur" value={telemetry.elapsed} fillPct={durPct} title={railTitle} />
    </div>
  );
}

// ── Timeline (TrailView) ──────────────────────────────────────
function TrailView({ steps, classes, focusedIdx, onSelect, onOpenStep, contextWaste, runHealth, wasteFilter, compact }) {
  // Build a synthetic trace shape for the helpers
  const trace = React.useMemo(() => ({ steps, timestamp_start: steps[0]?.timestamp, timestamp_end: steps[steps.length - 1]?.timestamp }), [steps]);

  // health-pill focus (local) — dims non-matching health glyphs
  const [healthFilter, setHealthFilter] = React.useState(null);

  // Map run-intel findings/signals onto array indices (step_index is 1-based).
  const overlays = React.useMemo(() => {
    const idxOf = (si) => steps.findIndex(s => s.step_index === si);
    const wasteByIdx = {}, healthByIdx = {};
    (contextWaste?.findings || []).forEach(f => {
      const i = idxOf(f.step_index);
      if (i >= 0) (wasteByIdx[i] = wasteByIdx[i] || []).push(f);
    });
    (runHealth?.signals || []).forEach(g => {
      const i = idxOf(g.step_index);
      if (i >= 0) (healthByIdx[i] = healthByIdx[i] || []).push(g);
    });
    return { wasteByIdx, healthByIdx };
  }, [steps, contextWaste, runHealth]);

  const derived = React.useMemo(() => {
    const stages = buildFileStages(trace);
    const tel = perStepTelemetry(trace);
    const maxima = {
      tokIn: Math.max(1, ...tel.deltaIn),
      tokOut: Math.max(1, ...tel.deltaOut),
      elapsed: Math.max(1, ...tel.deltaSec),
    };
    const shas = trace.steps.map((_, i) => {
      const ev = stages.snapshots[i].event;
      if (ev === "commit" || ev === "push" || ev === "commit+push" || stages.snapshots[i].pushed) {
        return extractSha(trace, i) || "abc1234";
      }
      return null;
    });
    return { stages, tel, maxima, shas };
  }, [trace]);

  return (
    <div className="trail-pane">
      {runHealth && (
        <RunHealthStrip
          health={runHealth}
          fidelity={runHealth.fidelity}
          activeKind={healthFilter}
          onKind={setHealthFilter}
        />
      )}
      <div
        className="timeline"
        style={{ "--grid-template": GRID_TEMPLATE }}
        data-waste-filter={wasteFilter ? "true" : undefined}
        data-health-filter={healthFilter || undefined}
      >
        <GridHead trace={trace} classes={classes} stages={derived.stages} compact={compact} />
        <div className="timeline-body">
          {trace.steps.map((step, i) => {
            const trunk = {
              work: "none",
              local: trunkFor(i, derived.stages.ranges.firstCommit, derived.stages.ranges.lastCommit),
              remote: trunkFor(i, derived.stages.ranges.firstPush, derived.stages.ranges.lastPush),
            };
            const telemetry = {
              tokIn: derived.tel.deltaIn[i] || 0,
              tokOut: derived.tel.deltaOut[i] || 0,
              elapsed: derived.tel.deltaSec[i] || 0,
              ctxPct: derived.tel.ctxPct[i],
            };
            return (
              <TimelineRow
                key={i}
                step={step}
                stepIndex={i}
                actionClass={classes[i]}
                snapshot={derived.stages.snapshots[i]}
                telemetry={telemetry}
                maxima={derived.maxima}
                focused={i === focusedIdx}
                trunk={trunk}
                sha={derived.shas[i]}
                wasteFindings={overlays.wasteByIdx[i]}
                healthSignals={overlays.healthByIdx[i]}
                healthFilter={healthFilter}
                onFocus={() => onSelect(i)}
                onOpen={() => { onSelect(i); onOpenStep && onOpenStep(i); }}
                onCommitOpen={() => onOpenStep && onOpenStep(i)}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

window.TrailView = TrailView;
