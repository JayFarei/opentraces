// Trace header card + minimap + tabs

function TraceHeader({ trace, meta, compact, contextWaste, runHealth, wasteFilter, onToggleWasteFilter }) {
  const start = new Date(trace.timestamp_start);
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const pad2 = n => String(n).padStart(2, "0");
  const when = `${months[start.getUTCMonth()]} ${start.getUTCDate()} · ${pad2(start.getUTCHours())}:${pad2(start.getUTCMinutes())}:${pad2(start.getUTCSeconds())} UTC`;
  const dur = trace.metrics?.total_duration_s;
  const durLabel = dur < 60 ? `${dur.toFixed(0)}s` : dur < 3600 ? `${(dur/60).toFixed(1)}m` : `${(dur/3600).toFixed(2)}h`;
  const cost = trace.metrics?.estimated_cost_usd ?? 0;
  const agentName = trace.agent?.name || "—";
  const agentVer = trace.agent?.version || "";
  const model = (trace.agent?.model || "").split("/").pop() || "—";
  const sha = (trace.outcome?.commit_sha || "").slice(0, 7);
  const traceShort = trace.trace_id.slice(0, 8);

  return (
    <div className="trace-card" data-compact={compact ? "true" : "false"}>
      <div className="left">
        <div className="trace-tag-icon">
          <Icon name="git-branch" size={16} />
        </div>
        <div className="trace-headline">
          <h2 className="trace-title">{meta?.title || "Trace"}</h2>
          <p className="trace-summary">{meta?.summary}</p>
          <div className="trace-intel-row">
            {contextWaste && <FidelityBadge fidelity={contextWaste.fidelity} />}
            {contextWaste && (
              <ContextWasteChip
                summary={contextWaste.summary}
                on={wasteFilter}
                onToggle={onToggleWasteFilter}
              />
            )}
            {runHealth && <HealthCounts counts={runHealth.counts} />}
          </div>
        </div>
        <div className="trace-compact-meta">
          <span className="cm-item mono"><span className="cm-k">{traceShort}</span></span>
          <span className="cm-sep" />
          <span className="cm-item mono">{durLabel}</span>
          <span className="cm-sep" />
          <span className="cm-item mono">${cost.toFixed(2)}</span>
          <span className="cm-sep" />
          <span className="cm-item">{agentName}{agentVer && <span className="ver"> {agentVer}</span>}</span>
          <span className="cm-sep" />
          <span className="cm-item">
            <span className="status-chip"><span className="dot" />Committed</span>
          </span>
          {contextWaste && (
            <>
              <span className="cm-sep" />
              <ContextWasteChip summary={contextWaste.summary} on={wasteFilter} onToggle={onToggleWasteFilter} />
            </>
          )}
        </div>
      </div>
      <div className="trace-meta">
        <div className="tm-item">
          <div className="k">Trace ID</div>
          <div className="v">{traceShort}</div>
        </div>
        <div className="tm-item">
          <div className="k">When</div>
          <div className="v body">{when}</div>
        </div>
        <div className="tm-item">
          <div className="k">Duration</div>
          <div className="v">{durLabel}</div>
        </div>
        <div className="tm-item">
          <div className="k">Cost</div>
          <div className="v">${cost.toFixed(2)}</div>
        </div>
        <div className="tm-item">
          <div className="k">Agent</div>
          <div className="v body">{agentName}{agentVer && <span className="ver"> {agentVer}</span>}</div>
        </div>
        <div className="tm-item">
          <div className="k">Model</div>
          <div className="v">{model}</div>
        </div>
        <div className="tm-item">
          <div className="k">Status</div>
          <div className="v body">
            <span className="status-chip">
              <span className="dot" />Committed
            </span>
          </div>
        </div>
        <div className="tm-item">
          <div className="k">Branch</div>
          <div className="v body">
            <span className="status-chip push">
              <span className="dot" />on main
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

function Minimap({ classes, focusedIdx, onSelect }) {
  const n = classes.length;
  // Center cursor on the active segment: each segment occupies (1/n) of width,
  // and its center is at ((i + 0.5) / n) * 100%
  const cursorPct = n > 0 ? ((focusedIdx + 0.5) / n) * 100 : 0;
  return (
    <div className="minimap-card">
      <div className="minimap" role="navigation" aria-label="Trace minimap">
        {classes.map((c, i) => (
          <div
            key={i}
            className={`seg ${c} ${i === focusedIdx ? "active" : ""}`}
            title={`Step ${String(i).padStart(3, "0")} · ${c}`}
            onClick={() => onSelect(i)}
          />
        ))}
        <div className="cursor" style={{ left: `${cursorPct}%` }} />
      </div>
    </div>
  );
}

function Tabs({ active, onChange }) {
  return (
    <div className="tab-row" role="tablist">
      <button className="tab" role="tab" aria-selected={active === "trail"} onClick={() => onChange("trail")}>
        <Icon name="trail" size={13} className="icon" />
        <span>Trail (Visual)</span>
      </button>
      <button className="tab" role="tab" aria-selected={active === "conversation"} onClick={() => onChange("conversation")}>
        <Icon name="conversation" size={13} className="icon" />
        <span>Conversation</span>
      </button>
    </div>
  );
}

window.TraceHeader = TraceHeader;
window.Minimap = Minimap;
window.Tabs = Tabs;
