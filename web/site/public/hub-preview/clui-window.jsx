// ─────────────────────────────────────────────────────────────
// CluiWindowFrame — when the Claude pane is open, the app frame
// becomes a controlled window: a chrome bar on top (refresh,
// session name, live activity) plus review tools (Mark up ink,
// Comment pins, Save snapshot). The window itself insets with a
// rounded accent border; a light sweeps it while Claude acts.
// Talks to the chat pane via "clui-meta" / "clui-action-line".
// ─────────────────────────────────────────────────────────────

// Map a point on the window to the source file that renders it.
// Checks structural containers first, then falls back to the
// current view / tab / repo child.
const CLUI_VIEW_FILES = {
  "traces-landing": "landing-traces.jsx",
  compare: "compare.jsx",
  intents: "pages-intents.jsx",
  evals: "pages-evals.jsx",
  spotlight: "pages-spotlight.jsx",
  capsules: "pages-capsules.jsx",
  alerts: "pages-alerts.jsx",
  improving: "pages-improving.jsx",
  settings: "landing-settings-global.jsx",
};
function cluiTargetAt(context, clientX, clientY) {
  let under = null;
  try {
    const stack = document.elementsFromPoint(clientX, clientY);
    under = stack.find(n => n.closest && !n.closest(".clui-annot-layer") && !n.closest(".clui-chrome"));
  } catch (e) {}
  let file;
  if (under && under.closest(".topbar")) file = "topbar.jsx";
  else if (under && under.closest(".trace-sticky")) file = "header.jsx";
  else if (under && (under.closest(".conv-side") || under.closest(".conv-main"))) file = "conversation.jsx";
  else {
    const v = context.view;
    if (v === "trace") file = context.activeTab === "trail" ? "trail.jsx" : "conversation.jsx";
    else if (v === "repo") file = "landing-repo-" + (context.activeRepoChild || "overview") + ".jsx";
    else if (v === "dataset") file = "landing-dataset.jsx";
    else file = CLUI_VIEW_FILES[v] || "app.css";
  }
  // climb to a component-sized element with a class — what the user
  // perceives as "the thing" they clicked
  let el = under;
  while (el && el !== document.body) {
    const r = el.getBoundingClientRect();
    const cls = (typeof el.className === "string" && el.className.trim()) ? el.className.trim().split(/\s+/)[0] : "";
    if (cls && r.width >= 24 && r.height >= 18) break;
    el = el.parentElement;
  }
  if (el === document.body) el = null;
  const label = el
    ? el.tagName.toLowerCase() + ((typeof el.className === "string" && el.className.trim()) ? "." + el.className.trim().split(/\s+/)[0] : "")
    : "";
  return { file, el, label };
}
function cluiSourceFile(context, clientX, clientY) {
  return cluiTargetAt(context, clientX, clientY).file;
}

function CluiWindowFrame({ open, context, children }) {
  const [meta, setMeta] = React.useState({ title: "New session", busy: false, activity: "" });
  const [mode, setMode] = React.useState(null); // null | "ink" | "comment"
  const [strokeCount, setStrokeCount] = React.useState(0);
  const [pins, setPins] = React.useState([]); // {id, kind:'point'|'el', x, y, w?, h?, label?, file, text, editing}
  const [hover, setHover] = React.useState(null); // hovered component in mark-up mode
  const [draft, setDraft] = React.useState(""); // markup popup text
  const [refreshKey, setRefreshKey] = React.useState(0);
  const [spin, setSpin] = React.useState(false);
  const [toast, setToast] = React.useState(null);
  const layerRef = React.useRef(null);
  const canvasRef = React.useRef(null);
  const strokesRef = React.useRef([]); // {pts: [[xFrac, yFrac]…], file}
  const liveStroke = React.useRef(null);
  const downRef = React.useRef(null); // pointer-down info: click vs drag
  const toastT = React.useRef(null);
  const pinSeq = React.useRef(1);
  const editingPin = pins.find(p => p.editing) || null;
  const editingRef = React.useRef(null);
  editingRef.current = editingPin ? editingPin.id : null;
  React.useEffect(() => { setDraft(""); }, [editingPin && editingPin.id]);

  // Session meta published by the chat pane
  React.useEffect(() => {
    const onMeta = (e) => setMeta(e.detail || {});
    window.addEventListener("clui-meta", onMeta);
    return () => window.removeEventListener("clui-meta", onMeta);
  }, []);

  // Flag annotation mode so Esc in the chat pane doesn't flip the sidebar
  React.useEffect(() => {
    window.__cluiAnnotMode = !!mode;
    return () => { window.__cluiAnnotMode = false; };
  }, [mode]);

  // Esc exits annotation mode (capture, so it wins over the pane toggle);
  // if the markup popup is open it just dismisses that first
  React.useEffect(() => {
    if (!mode) return;
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        if (editingRef.current) setPins(p => p.filter(x => x.id !== editingRef.current));
        else setMode(null);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [mode]);

  React.useEffect(() => { if (!open) setMode(null); }, [open]);
  React.useEffect(() => { if (mode !== "ink") setHover(null); }, [mode]);

  // ── Ink canvas ──
  const redraw = () => {
    const cv = canvasRef.current, layer = layerRef.current;
    if (!cv || !layer) return;
    const w = layer.clientWidth, h = layer.clientHeight;
    if (cv.width !== w) cv.width = w;
    if (cv.height !== h) cv.height = h;
    const ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = "rgba(217, 119, 87, 0.92)";
    ctx.lineWidth = 2.5;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (const s of strokesRef.current) {
      ctx.beginPath();
      s.pts.forEach(([x, y], i) => { i ? ctx.lineTo(x * w, y * h) : ctx.moveTo(x * w, y * h); });
      ctx.stroke();
    }
  };

  React.useEffect(() => {
    const layer = layerRef.current;
    if (!layer || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => redraw());
    ro.observe(layer);
    return () => ro.disconnect();
  }, []);

  const layerFrac = (e) => {
    const r = layerRef.current.getBoundingClientRect();
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
  };

  // ── Mark up: drag draws ink, a plain click selects the component
  // under the cursor (hover shows what would be picked) ──
  const onInkDown = (e) => {
    if (mode !== "ink" || e.button !== 0) return;
    if (editingPin || (e.target.closest && e.target.closest(".clui-markup-pop"))) return;
    e.preventDefault();
    e.target.setPointerCapture && e.target.setPointerCapture(e.pointerId);
    downRef.current = { pt: layerFrac(e), cx: e.clientX, cy: e.clientY, dragging: false, file: cluiSourceFile(context, e.clientX, e.clientY) };
  };
  const onInkMove = (e) => {
    if (mode !== "ink") return;
    const d = downRef.current;
    if (d) {
      if (!d.dragging && Math.hypot(e.clientX - d.cx, e.clientY - d.cy) > 4) {
        d.dragging = true;
        liveStroke.current = { pts: [d.pt], file: d.file };
        setHover(null);
      }
      if (!d.dragging || !liveStroke.current) return;
      const pt = layerFrac(e);
      const s = liveStroke.current.pts;
      const prev = s[s.length - 1];
      s.push(pt);
      // incremental segment for latency-free feel
      const cv = canvasRef.current, layer = layerRef.current;
      if (cv && layer) {
        const ctx = cv.getContext("2d");
        ctx.strokeStyle = "rgba(217, 119, 87, 0.92)";
        ctx.lineWidth = 2.5;
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(prev[0] * layer.clientWidth, prev[1] * layer.clientHeight);
        ctx.lineTo(pt[0] * layer.clientWidth, pt[1] * layer.clientHeight);
        ctx.stroke();
      }
      return;
    }
    // no button down — highlight the component under the cursor
    if (editingPin) return;
    const t = cluiTargetAt(context, e.clientX, e.clientY);
    const lr = layerRef.current && layerRef.current.getBoundingClientRect();
    if (t.el && lr) {
      const r = t.el.getBoundingClientRect();
      setHover({ left: r.left - lr.left, top: r.top - lr.top, width: r.width, height: r.height, label: t.label, file: t.file });
    } else setHover(null);
  };
  const onInkUp = (e) => {
    const d = downRef.current;
    downRef.current = null;
    if (liveStroke.current && liveStroke.current.pts.length > 1) {
      strokesRef.current.push(liveStroke.current);
      setStrokeCount(strokesRef.current.length);
      liveStroke.current = null;
      return;
    }
    liveStroke.current = null;
    // plain click → select the component and ask what to change
    if (mode === "ink" && d && !d.dragging && e && layerRef.current) {
      const t = cluiTargetAt(context, e.clientX, e.clientY);
      if (!t.el) return;
      const lr = layerRef.current.getBoundingClientRect();
      const r = t.el.getBoundingClientRect();
      setPins(p => [...p, {
        id: "p" + (pinSeq.current++),
        kind: "el",
        x: (r.left - lr.left) / lr.width,
        y: (r.top - lr.top) / lr.height,
        w: r.width / lr.width,
        h: r.height / lr.height,
        label: t.label,
        file: t.file,
        text: "",
        editing: true,
      }]);
      setHover(null);
    }
  };
  const onInkLeave = (e) => { setHover(null); onInkUp(e); };

  // ── (comment-pin mode removed — markup click-select covers it) ──
  const commitPin = (id, text) => {
    const t = String(text || "").trim();
    setPins(p => t
      ? p.map(x => x.id === id ? { ...x, text: t, editing: false } : x)
      : p.filter(x => x.id !== id));
  };
  const removePin = (id) => setPins(p => p.filter(x => x.id !== id));

  // ── Markup popup actions ──
  const popAddComment = () => {
    if (!editingPin) return;
    commitPin(editingPin.id, draft);
  };
  const popSendToClaude = () => {
    const t = draft.trim();
    if (!t || !editingPin) return;
    const item = {
      kind: "pin",
      n: pins.filter(p => !p.editing).length + 1,
      text: t,
      file: editingPin.file,
    };
    window.dispatchEvent(new CustomEvent("clui-annotations", { detail: { items: [item], ctx: ctxLabel } }));
    removePin(editingPin.id);
  };

  const annotCount = strokeCount + pins.filter(p => !p.editing).length;

  const clearAll = () => {
    strokesRef.current = [];
    setStrokeCount(0);
    setPins([]);
    redraw();
  };

  // ── Chrome actions ──
  const refresh = () => {
    setSpin(true);
    setRefreshKey(k => k + 1);
    setTimeout(() => setSpin(false), 650);
  };

  const ctxLabel =
    context.view === "trace" ? "trace " + (context.traceShortId || "") :
    context.view === "repo" ? (context.activeRepoId || "repo") :
    context.view === "dataset" ? (context.activeDatasetId || "dataset") :
    context.view;

  const save = () => {
    const suffix = annotCount > 0 ? " · " + annotCount + " annotation" + (annotCount > 1 ? "s" : "") : "";
    window.dispatchEvent(new CustomEvent("clui-action-line", { detail: "Saved snapshot · " + ctxLabel + suffix }));
    setToast("Snapshot saved to session");
    clearTimeout(toastT.current);
    toastT.current = setTimeout(() => setToast(null), 2200);
  };

  // Hand the annotations to the chat, each with its source-file pointer
  const addToChat = () => {
    const items = [];
    pins.filter(p => !p.editing).forEach((p, i) => items.push({ kind: "pin", n: i + 1, text: p.text, file: p.file }));
    const inkByFile = {};
    strokesRef.current.forEach(s => { inkByFile[s.file] = (inkByFile[s.file] || 0) + 1; });
    Object.keys(inkByFile).forEach(file => items.push({ kind: "ink", n: inkByFile[file], file }));
    if (!items.length) return;
    window.dispatchEvent(new CustomEvent("clui-annotations", { detail: { items, ctx: ctxLabel } }));
    clearAll();
    setMode(null);
    setToast("Added to chat");
    clearTimeout(toastT.current);
    toastT.current = setTimeout(() => setToast(null), 2200);
  };

  return (
    <div className="clui-window" data-annot={mode || "off"}>
      <div className="clui-chrome" aria-hidden={!open} data-comment-anchor="clui-window-chrome">
        <button className="clui-chrome-btn icon" title="Refresh this view" onClick={refresh} tabIndex={open ? 0 : -1}>
          <span className={"rf" + (spin ? " spin" : "")}><Icon name="refresh" size={13} /></span>
        </button>
        <span className="clui-spark" style={{ fontSize: 12 }}>✳</span>
        <span className="clui-chrome-title" title="Claude session controlling this window">{meta.title || "New session"}</span>
        <span className="clui-chrome-ctx">◉ {ctxLabel}</span>
        {meta.busy && (
          <span className="clui-chrome-live"><span className="clui-dot"></span>{(meta.activity || "Thinking") + "…"}</span>
        )}
        <div className="spring"></div>
        <button className="clui-chrome-btn" data-on={mode === "ink"} tabIndex={open ? 0 : -1}
          title="Click to select, drag to draw — Esc to exit"
          onClick={() => setMode(m => m === "ink" ? null : "ink")}>
          <Icon name="pen" size={13} /><span>Mark up</span>
        </button>
        {annotCount > 0 && (
          <button className="clui-chrome-btn ghost" onClick={clearAll} tabIndex={open ? 0 : -1} title="Clear all annotations">Clear</button>
        )}
        {annotCount > 0 && (
          <button className="clui-chrome-btn add" onClick={addToChat} tabIndex={open ? 0 : -1} title="Send annotations to the chat, each pointing at its source file">
            ↑ Add to chat ({annotCount})
          </button>
        )}
        <span className="clui-chrome-sep"></span>
        <button className="clui-chrome-btn save" onClick={save} tabIndex={open ? 0 : -1} title="Save this view + annotations into the session">
          <Icon name="save" size={13} /><span>Save</span>
        </button>
      </div>

      <div className="clui-window-view">
        <div className="clui-window-body" key={refreshKey}>{children}</div>
        <div
          className="clui-annot-layer"
          ref={layerRef}
          data-mode={mode || ""}
          onPointerDown={onInkDown}
          onPointerMove={onInkMove}
          onPointerUp={onInkUp}
          onPointerLeave={onInkLeave}
        >
          <canvas ref={canvasRef}></canvas>
          {mode && (
            <div className="clui-guide">Click and tell Claude what to change · drag to draw</div>
          )}
          {mode === "ink" && hover && (
            <div className="clui-el-hover" style={{ left: hover.left, top: hover.top, width: hover.width, height: hover.height }}></div>
          )}
          {pins.map((pin, i) => {
            const note = pin.editing ? null : (
              <span className="pin-bubble">
                {pin.text}
                <span className="pin-file">{pin.file}</span>
                <button className="pin-del" title="Remove" onClick={(e) => { e.stopPropagation(); removePin(pin.id); }}>✕</button>
              </span>
            );
            return (
              <div
                className="clui-elpin"
                key={pin.id}
                style={{ left: (pin.x * 100) + "%", top: (pin.y * 100) + "%", width: (pin.w * 100) + "%", height: (pin.h * 100) + "%" }}
              >
                <div className="el-box"></div>
                <div className="el-note">
                  <span className="pin-dot">{i + 1}</span>
                  {note}
                </div>
              </div>
            );
          })}
          {editingPin && (
            <div
              className="clui-markup-pop clui-glass"
              style={{
                left: "clamp(12px, " + (editingPin.x * 100) + "%, calc(100% - 352px))",
                top: "clamp(12px, calc(" + ((editingPin.y + (editingPin.h || 0)) * 100) + "% + 10px), calc(100% - 190px))",
              }}
              onPointerDown={(e) => e.stopPropagation()}
            >
              <div className="mp-head">
                <span className="mp-title">Mark up</span>
                <button className="mp-x" title="Dismiss (Esc)" onClick={() => removePin(editingPin.id)}>✕</button>
              </div>
              <textarea
                autoFocus
                rows={3}
                placeholder="Describe the issue or suggestion…"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); popSendToClaude(); }
                }}
              ></textarea>
              <div className="mp-foot">
                <button className="mp-btn" onClick={popAddComment} disabled={!draft.trim()}>Add comment</button>
                <button className="mp-btn primary" onClick={popSendToClaude} disabled={!draft.trim()}>Send to Claude</button>
              </div>
            </div>
          )}
        </div>
        {toast && <div className="clui-save-toast"><span className="tick">✓</span>{toast}</div>}
      </div>
    </div>
  );
}

Object.assign(window, { CluiWindowFrame });
