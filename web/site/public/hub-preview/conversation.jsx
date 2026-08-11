// Conversation tab — renders a back-and-forth view of a trace

const FILTER_TREE = [
  { id: "user", label: "Prompts", swatch: "var(--c-user)" },
  { id: "plan", label: "Responses", swatch: "var(--c-plan)" },
  { id: "think", label: "Intermediate steps", swatch: "var(--c-think)" },
  { id: "checkpoint", label: "Checkpoints", swatch: "var(--c-git)" },
  { id: "tools", label: "Tool calls", children: [
    { id: "file_edits", label: "File edits", swatch: "var(--c-write)" },
    { id: "bash", label: "Bash", swatch: "var(--c-exec)" },
    { id: "read", label: "Read", swatch: "var(--c-read)" },
    { id: "other", label: "Other", swatch: "var(--c-think)" },
  ]},
];

function FilterSidebar({ filters, counts, onToggle, onToggleGroup, expandedTools, onExpandTools, onExpandAllTools, onJumpLatest }) {
  const toolChildIds = ["file_edits","bash","read","other"];
  const toolChildStates = toolChildIds.map(id => filters[id]);
  const allTools = toolChildStates.every(Boolean);
  const anyTools = toolChildStates.some(Boolean);

  return (
    <>
      <div className="side-card">
        <div className="side-h">
          <span>Filters</span>
          <button className="edit">Edit</button>
        </div>
        <div className="filter-list">
          {FILTER_TREE.map(item => {
            if (item.children) {
              const checkbox = allTools ? "checked" : (anyTools ? "partial" : "");
              return (
                <div key={item.id}>
                  <button
                    className="filter-item"
                    data-expanded={expandedTools}
                    onClick={() => onExpandTools()}
                  >
                    <span
                      className={`checkbox ${checkbox}`}
                      onClick={(e) => { e.stopPropagation(); onToggleGroup(toolChildIds, !allTools); }}
                    />
                    <span className="label">{item.label}</span>
                    <span className="count">{counts.tools || 0}</span>
                    <Icon name="chevron-right" size={11} className="chev" />
                  </button>
                  {expandedTools && (
                    <div className="filter-children">
                      {item.children.map(c => (
                        <button key={c.id} className="filter-item" onClick={() => onToggle(c.id)}>
                          <span className={`checkbox ${filters[c.id] ? "checked" : ""}`} />
                          <span className="swatch" style={{ background: c.swatch }} />
                          <span className="label">{c.label}</span>
                          <span className="count">{counts[c.id] || 0}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              );
            }
            return (
              <button key={item.id} className="filter-item" onClick={() => onToggle(item.id)}>
                <span className={`checkbox ${filters[item.id] ? "checked" : ""}`} />
                <span className="label">{item.label}</span>
                <span className="count">{counts[item.id] || 0}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="side-card">
        <div className="side-h"><span>Views</span></div>
        <div className="views">
          <button className="view-btn" onClick={onExpandAllTools}>
            <Icon name="expand" size={12} className="icon" />
            <span>Expand all tool calls</span>
          </button>
          <button className="view-btn" onClick={onJumpLatest}>
            <Icon name="down-line" size={12} className="icon" />
            <span>Jump to latest</span>
          </button>
        </div>
      </div>

      <div className="privacy-card">
        <div className="privacy-h">
          <span>Privacy filtered out</span>
          <span className="info-i">i</span>
        </div>
        <div className="privacy-list">
          <div className="privacy-row"><span className="pname"><span className="pdot" />Private data</span><span className="pcount">18</span></div>
          <div className="privacy-row"><span className="pname"><span className="pdot" style={{ opacity: 0.7 }} />Private person</span><span className="pcount">9</span></div>
          <div className="privacy-row"><span className="pname"><span className="pdot" style={{ opacity: 0.55 }} />Private contact</span><span className="pcount">7</span></div>
          <div className="privacy-row"><span className="pname"><span className="pdot" style={{ opacity: 0.4 }} />Sensitive content</span><span className="pcount">4</span></div>
        </div>
        <a className="view-link" href="#privacy">View details →</a>
      </div>
    </>
  );
}

function ToolCall({ tc, expanded, onToggle }) {
  const cls = classifyTool(tc);
  const arg = summarizeToolCall(tc);
  return (
    <>
      <button
        className={`tool-call ${cls}`}
        aria-expanded={expanded}
        onClick={onToggle}
      >
        <span className="tcdot" />
        <span className="tcname">{tc.tool_name.toLowerCase()}</span>
        <span className="tcarg">{arg}</span>
        <Icon name="chevron-right" size={12} className="tcchev" />
      </button>
      {expanded && (
        <div className="tool-call-detail">
          <pre>{JSON.stringify(tc.input, null, 2)}</pre>
        </div>
      )}
    </>
  );
}

function ConversationView({ steps, filters, expandedTools, onOpenStep, expandAllTools, sliceRange }) {
  // Dim entries outside the active trace slice (slicer scoping).
  const outOfSlice = (i) => (sliceRange && (i < sliceRange[0] || i > sliceRange[1]) ? " out-of-slice" : "");
  // Determine which steps to render
  // Group consecutive think + tool steps into "segments" attached to nearest agent text.
  const [openTool, setOpenTool] = React.useState(() => new Set());

  React.useEffect(() => {
    if (expandAllTools === null) return;
    if (expandAllTools) {
      const all = new Set();
      steps.forEach((s, i) => (s.tool_calls || []).forEach((_, j) => all.add(`${i}-${j}`)));
      setOpenTool(all);
    } else {
      setOpenTool(new Set());
    }
  }, [expandAllTools, steps]);

  const toggleTool = (k) => {
    setOpenTool(prev => {
      const n = new Set(prev);
      n.has(k) ? n.delete(k) : n.add(k);
      return n;
    });
  };

  // Build display list
  const items = [];
  let lastCheckpointStep = -1;
  steps.forEach((step, i) => {
    if (step.role === "user") {
      if (filters.user) items.push({ kind: "user", step, i });
      return;
    }
    // agent
    const tcs = step.tool_calls || [];

    // detect checkpoint: any Bash with `git commit`
    const commitTc = tcs.find(t => t.tool_name === "Bash" && /git\s+commit/.test(t.input?.command || ""));
    if (commitTc && filters.checkpoint && i > lastCheckpointStep) {
      // synthesize a sha from step index
      const fakeSha = ("000000" + (0xa1b2c3 ^ (i*7919)).toString(16)).slice(-7);
      items.push({ kind: "checkpoint", step, i, sha: fakeSha, subject: (commitTc.input.description || "commit").replace(/^(commit:?\s*)/i, "") });
      lastCheckpointStep = i;
    }

    if (step.content && step.content.trim()) {
      if (filters.plan) items.push({ kind: "agent-text", step, i });
    } else if (step.reasoning_content && filters.think) {
      items.push({ kind: "agent-think", step, i });
    }

    // tool calls
    tcs.forEach((tc, j) => {
      const cat = toolCategory(tc);
      if (filters[cat]) items.push({ kind: "tool", step, i, tc, j });
    });
  });

  if (items.length === 0) {
    return (
      <div className="empty">
        <Icon name="conversation" size={28} className="ico" />
        <div>No messages match the active filters.</div>
        <div style={{ fontSize: 12, marginTop: 4 }}>Re-enable a filter or clear search to see content.</div>
      </div>
    );
  }

  return (
    <div className="conv-stream" id="conv-stream">
      {items.map((it, ix) => {
        if (it.kind === "user") {
          return (
            <div key={`u${it.i}`} className={"msg user" + outOfSlice(it.i)} data-step={it.i}>
              <div className="msg-gutter"><div className="msg-icon"><Icon name="user" size={13} /></div></div>
              <div>
                <div className="msg-body">{renderRichText(it.step.content, it.i)}</div>
              </div>
            </div>
          );
        }
        if (it.kind === "agent-text") {
          return (
            <div key={`a${it.i}`} className={"msg agent-text" + outOfSlice(it.i)} data-step={it.i}>
              <div className="msg-gutter"><div className="msg-icon"><Icon name="bot" size={13} /></div></div>
              <div>
                <div className="msg-body">{renderRichText(it.step.content, it.i)}</div>
              </div>
            </div>
          );
        }
        if (it.kind === "agent-think") {
          const txt = it.step.reasoning_content;
          const short = firstLine(txt);
          return (
            <div key={`t${it.i}`} className={"msg agent-think" + outOfSlice(it.i)} data-step={it.i}>
              <div className="msg-gutter"><div className="msg-icon"><Icon name="brain" size={13} /></div></div>
              <div>
                <div className="msg-body">{renderRichText(short, it.i)}</div>
              </div>
            </div>
          );
        }
        if (it.kind === "tool") {
          const k = `${it.i}-${it.j}`;
          return (
            <div key={`tc${k}`} className={"msg tool" + outOfSlice(it.i)} data-step={it.i}>
              <div className="msg-gutter"><div className="msg-icon"><Icon name="tool" size={12} /></div></div>
              <div>
                <ToolCall tc={it.tc} expanded={openTool.has(k)} onToggle={() => toggleTool(k)} />
              </div>
            </div>
          );
        }
        if (it.kind === "checkpoint") {
          return (
            <div key={`cp${it.i}`} className={"checkpoint" + outOfSlice(it.i)} data-step={it.i}>
              <div className="cp-icon"><Icon name="git-commit" size={13} /></div>
              <div className="cp-mid">
                <div className="cp-subject">{it.subject || "commit"}</div>
                <div className="cp-meta">checkpoint · step {String(it.i).padStart(3, "0")}</div>
              </div>
              <span className="cp-sha">{it.sha}</span>
            </div>
          );
        }
        return null;
      })}
    </div>
  );
}

window.ConversationView = React.memo(ConversationView);
window.FilterSidebar = FilterSidebar;
