// ─────────────────────────────────────────────────────────────
// CLUI composer — the Hub agent's input box.
// [+] menu: Use/Edit mode, Hub context (traces, capsules, repo
// scope), session self-capture, artifacts, permissions · voice
// dictation · model picker · Send/interrupt.
// ─────────────────────────────────────────────────────────────

const CLUI_MODELS = [
  { id: "fable-5",   name: "Claude Fable 5",   short: "Fable 5",   desc: "For your toughest challenges" },
  { id: "opus-4-8",  name: "Claude Opus 4.8",  short: "Opus 4.8",  desc: "For complex tasks" },
  { id: "sonnet-5",  name: "Claude Sonnet 5",  short: "Sonnet 5",  desc: "Most efficient for everyday tasks" },
  { id: "haiku-4-5", name: "Claude Haiku 4.5", short: "Haiku 4.5", desc: "Fastest for quick answers" },
];
const CLUI_MORE_MODELS = [
  { id: "opus-4-5",   name: "Claude Opus 4.5",   short: "Opus 4.5",   desc: "Legacy" },
  { id: "sonnet-4-7", name: "Claude Sonnet 4.7", short: "Sonnet 4.7", desc: "Legacy" },
];
const CLUI_EFFORTS = ["High", "Medium", "Low"];

function cluiActionLine(label) {
  window.dispatchEvent(new CustomEvent("clui-action-line", { detail: label }));
}

function CluiComposer({ value, onChange, inputRef, busy, onSend, onInterrupt, permMode, onPermMode, mode, onMode, artifacts, onRemoveArtifact, captured, onCaptured, traceAddr }) {
  const [menu, setMenu] = React.useState(null); // "plus" | "model" | null
  const [modelId, setModelId] = React.useState(() => {
    try { return localStorage.getItem("ot-clui-model") || "fable-5"; } catch (e) { return "fable-5"; }
  });
  const [effort, setEffort] = React.useState(() => {
    try { return localStorage.getItem("ot-clui-effort") || "High"; } catch (e) { return "High"; }
  });
  const [effortOpen, setEffortOpen] = React.useState(false);
  const [moreOpen, setMoreOpen] = React.useState(false);
  const [sub, setSub] = React.useState(null); // "traces" | "capsules" | "repos" | null
  const [attachments, setAttachments] = React.useState([]);
  const [listening, setListening] = React.useState(false);
  const [hint, setHint] = React.useState("");
  const fileRef = React.useRef(null);
  const recRef = React.useRef(null);
  const hintTimer = React.useRef(null);
  const valueRef = React.useRef(value);
  valueRef.current = value;

  const model = [...CLUI_MODELS, ...CLUI_MORE_MODELS].find(m => m.id === modelId) || CLUI_MODELS[0];

  // let the sidebar's global Esc handler know a menu is open
  React.useEffect(() => {
    window.__cluiMenuOpen = !!menu;
    return () => { window.__cluiMenuOpen = false; };
  }, [menu]);
  React.useEffect(() => {
    if (!menu) return;
    const onKey = (e) => { if (e.key === "Escape") setMenu(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menu]);
  React.useEffect(() => () => {
    if (hintTimer.current) clearTimeout(hintTimer.current);
    try { recRef.current && recRef.current.stop(); } catch (e) {}
  }, []);

  const flashHint = (text) => {
    setHint(text);
    if (hintTimer.current) clearTimeout(hintTimer.current);
    hintTimer.current = setTimeout(() => setHint(""), 2200);
  };

  const closeMenu = () => { setMenu(null); setSub(null); };

  // Close the popover on any click outside the composer — the fixed
  // backdrop alone can lose to higher stacking contexts elsewhere.
  const composerRootRef = React.useRef(null);
  React.useEffect(() => {
    if (!menu) return;
    const onDown = (e) => {
      const root = composerRootRef.current;
      if (root && !root.contains(e.target)) closeMenu();
    };
    document.addEventListener("pointerdown", onDown, true);
    return () => document.removeEventListener("pointerdown", onDown, true);
  }, [menu]);

  // Hub context sources — pulled from the app's own data
  const ctxTraces = ((window.RECENT_TRACES || []).slice(0, 5));
  const ctxCapsules = ((window.CAPSULES || []).slice(0, 4));
  const ctxRepos = [...new Set((window.RECENT_TRACES || []).map(t => t.repo))].slice(0, 5);

  const pickModel = (id) => {
    setModelId(id);
    try { localStorage.setItem("ot-clui-model", id); } catch (e) {}
    setMenu(null);
  };
  const pickEffort = (ef) => {
    setEffort(ef);
    try { localStorage.setItem("ot-clui-effort", ef); } catch (e) {}
    setEffortOpen(false);
  };

  const addAttachments = (items) => setAttachments(a => [...a, ...items]);
  const removeAttachment = (i) => setAttachments(a => a.filter((_, j) => j !== i));

  const onFiles = (e, kind) => {
    const files = Array.from(e.target.files || []).map(f => ({ kind, name: f.name }));
    if (files.length) addAttachments(files);
    e.target.value = "";
    closeMenu();
  };

  const menuAction = (label) => {
    closeMenu();
    cluiActionLine(label);
  };

  // ── Voice dictation ──
  const toggleVoice = () => {
    if (listening) {
      try { recRef.current && recRef.current.stop(); } catch (e) {}
      setListening(false);
      return;
    }
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { flashHint("Voice input isn't available in this browser"); return; }
    const rec = new SR();
    rec.continuous = true;
    rec.interimResults = true;
    const base = valueRef.current ? valueRef.current.replace(/\s+$/, "") + " " : "";
    rec.onresult = (e) => {
      let txt = "";
      for (let i = 0; i < e.results.length; i++) txt += e.results[i][0].transcript;
      onChange(base + txt);
      autoGrow();
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => { setListening(false); flashHint("Microphone unavailable"); };
    recRef.current = rec;
    try { rec.start(); setListening(true); } catch (e) { flashHint("Couldn't start the microphone"); }
  };

  const autoGrow = () => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 140) + "px";
  };

  const canSend = !!(value.trim() || attachments.length);
  const doSend = () => {
    if (!canSend || busy) return;
    if (listening) { try { recRef.current && recRef.current.stop(); } catch (e) {} }
    const extra = attachments.length
      ? "\n[attached: " + attachments.map(a => a.name).join(", ") + "]"
      : "";
    onSend((value.trim() + extra).trim());
    setAttachments([]);
  };

  const Row = ({ icon, label, onClick, trailing, className }) => (
    <button className={"cc-row" + (className ? " " + className : "")} onClick={onClick}>
      {icon ? <span className="ic"><Icon name={icon} size={14} /></span> : null}
      <span className="lbl">{label}</span>
      {trailing || null}
    </button>
  );

  return (
    <div className="clui-side-composer clui-glass" data-comment-anchor="clui-composer" ref={composerRootRef}>
      <input type="file" multiple hidden ref={fileRef} onChange={(e) => onFiles(e, "file")} />

      {hint && <div className="cc-hint">{hint}</div>}
      {menu && <div className="cc-backdrop" onClick={closeMenu}></div>}

      {menu === "plus" && (
        <div className="cc-pop clui-glass">
          <div className="cc-mode" role="radiogroup" aria-label="Agent mode">
            <button data-on={mode !== "edit"} onClick={() => onMode("use")}>
              <span className="mt">Use the Hub</span>
              <span className="md">Capture, grade, replay, watch</span>
            </button>
            <button data-on={mode === "edit"} onClick={() => onMode("edit")}>
              <span className="mt">Edit the Hub</span>
              <span className="md">Change the app, on a branch</span>
            </button>
          </div>
          <div className="cc-div"></div>
          <div className="cc-sec">Context</div>
          <Row
            icon="trail"
            label="Add a trace…"
            onClick={() => setSub(s => s === "traces" ? null : "traces")}
            trailing={<span className="ic"><Icon name={sub === "traces" ? "chevron-down" : "chevron-right"} size={13} /></span>}
          />
          {sub === "traces" && ctxTraces.map(t => (
            <Row key={t.id} className="sub" label={String(t.id).slice(0, 7) + " · " + t.title}
              onClick={() => { addAttachments([{ kind: "trace", name: String(t.id).slice(0, 7) + " · " + t.title }]); closeMenu(); }} />
          ))}
          <Row
            icon="capsule"
            label="Add a capsule…"
            onClick={() => setSub(s => s === "capsules" ? null : "capsules")}
            trailing={<span className="ic"><Icon name={sub === "capsules" ? "chevron-down" : "chevron-right"} size={13} /></span>}
          />
          {sub === "capsules" && ctxCapsules.map(c => (
            <Row key={c.id} className="sub" label={c.cid + " · " + c.title}
              onClick={() => { addAttachments([{ kind: "capsule", name: c.cid }]); closeMenu(); }} />
          ))}
          <Row
            icon="repo"
            label="Scope to a repo…"
            onClick={() => setSub(s => s === "repos" ? null : "repos")}
            trailing={<span className="ic"><Icon name={sub === "repos" ? "chevron-down" : "chevron-right"} size={13} /></span>}
          />
          {sub === "repos" && ctxRepos.map(r => (
            <Row key={r} className="sub" label={r}
              onClick={() => { addAttachments([{ kind: "repo", name: r }]); closeMenu(); }} />
          ))}
          <Row icon="paperclip" label="Attach a file" onClick={() => fileRef.current && fileRef.current.click()} />
          <div className="cc-div"></div>
          <div className="cc-sec">This session</div>
          <Row
            icon="snapshot"
            label="Captured to your bucket"
            onClick={() => onCaptured(!captured)}
            trailing={
              <span className="cc-seg" onClick={(e) => e.stopPropagation()}>
                <button data-on={captured} onClick={() => onCaptured(true)}>On</button>
                <button data-on={!captured} onClick={() => onCaptured(false)}>Off</button>
              </span>
            }
          />
          <div className="cc-addr" data-off={!captured}>
            {captured ? traceAddr + " · Trace + Ctx recording" : "off — this conversation leaves no record"}
          </div>
          {(artifacts && artifacts.length > 0) && (
            <React.Fragment>
              <div className="cc-div"></div>
              <div className="cc-sec">Artifacts</div>
              {artifacts.map(a => (
                <Row
                  key={a.id}
                  icon="activity"
                  label={a.name}
                  onClick={() => {}}
                  trailing={
                    <React.Fragment>
                      <span className="cc-mini" role="button" title="Remove artifact"
                        onClick={(e) => { e.stopPropagation(); onRemoveArtifact(a.id); }}><Icon name="x" size={11} /></span>
                    </React.Fragment>
                  }
                />
              ))}
              <div className="cc-note">Made in this chat, saved to the Hub. Share by link or pin to the sidebar.</div>
            </React.Fragment>
          )}
          <div className="cc-div"></div>
          <Row
            icon="shield"
            label="Permissions"
            onClick={() => onPermMode(permMode === "ask" ? "auto" : "ask")}
            trailing={
              <span className="cc-seg" onClick={(e) => e.stopPropagation()}>
                <button data-on={permMode === "ask"} onClick={() => onPermMode("ask")}>Ask</button>
                <button data-on={permMode === "auto"} onClick={() => onPermMode("auto")}>Auto</button>
              </span>
            }
          />
        </div>
      )}

      {menu === "model" && (
        <div className="cc-pop clui-glass">
          {CLUI_MODELS.map(m => (
            <button key={m.id} className="cc-model-row" data-active={m.id === modelId} onClick={() => pickModel(m.id)}>
              <span>
                <div className="n">{m.name}</div>
                <div className="d">{m.desc}</div>
              </span>
              {m.id === modelId && <span className="ck"><Icon name="check" size={14} /></span>}
            </button>
          ))}
          {moreOpen && CLUI_MORE_MODELS.map(m => (
            <button key={m.id} className="cc-model-row" data-active={m.id === modelId} onClick={() => pickModel(m.id)}>
              <span>
                <div className="n">{m.name}</div>
                <div className="d">{m.desc}</div>
              </span>
              {m.id === modelId && <span className="ck"><Icon name="check" size={14} /></span>}
            </button>
          ))}
          <div className="cc-div"></div>
          <button className="cc-plain-row" onClick={() => setEffortOpen(o => !o)}>
            <span>Effort</span>
            <span className="spring"></span>
            <span className="v">{effort}</span>
            <span className="chev"><Icon name={effortOpen ? "chevron-down" : "chevron-right"} size={13} /></span>
          </button>
          {effortOpen && CLUI_EFFORTS.map(ef => (
            <button key={ef} className="cc-plain-row sub" data-on={ef === effort} onClick={() => pickEffort(ef)}>
              <span>{ef}</span>
              <span className="spring"></span>
              {ef === effort && <span className="ck"><Icon name="check" size={13} /></span>}
            </button>
          ))}
          <button className="cc-plain-row" onClick={() => setMoreOpen(o => !o)}>
            <span>More models</span>
            <span className="spring"></span>
            <span className="chev"><Icon name={moreOpen ? "chevron-down" : "chevron-right"} size={13} /></span>
          </button>
        </div>
      )}

      {attachments.length > 0 && (
        <div className="cc-chips">
          {attachments.map((a, i) => (
            <span className="cc-chip" key={i}>
              <span className="ic"><Icon name={a.kind === "trace" ? "trail" : a.kind === "capsule" ? "capsule" : a.kind === "repo" ? "repo" : "paperclip"} size={11} /></span>
              <span className="n">{a.name}</span>
              <button className="rm" title="Remove" onClick={() => removeAttachment(i)}><Icon name="x" size={10} /></button>
            </span>
          ))}
        </div>
      )}

      {mode === "edit" && (
        <button className="cc-modechip" onClick={() => onMode("use")} title="Back to using the Hub">
          <span className="gb">⎇</span>
          <span className="t">Editing the Hub — changes stage on a branch</span>
          <span className="x"><Icon name="x" size={10} /></span>
        </button>
      )}

      <textarea
        ref={inputRef}
        className="clui-input"
        rows={3}
        placeholder={listening ? "Listening…" : mode === "edit" ? "Describe a change to the Hub itself…" : "Ask or act on your traces, evals, capsules…"}
        value={value}
        onChange={(e) => { onChange(e.target.value); autoGrow(); }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); doSend(); }
        }}
      ></textarea>

      <div className="cc-foot">
        <button
          className="cc-sq"
          title="Mode, context and session capture"
          data-on={menu === "plus"}
          onClick={() => setMenu(m => m === "plus" ? null : "plus")}
        ><Icon name="plus" size={15} /></button>
        <button
          className="cc-sq cc-voice"
          title={listening ? "Stop dictating" : "Dictate"}
          data-on={listening}
          onClick={toggleVoice}
        ><span className="vb"></span><span className="vb"></span><span className="vb"></span><span className="vb"></span></button>
        <div className="spring"></div>
        <button
          className="cc-model"
          title="Choose model and effort"
          onClick={() => setMenu(m => m === "model" ? null : "model")}
        >
          <span className="m">{model.short}</span>
          <span className="e">{effort}</span>
          <span className="chev"><Icon name="chevron-down" size={12} /></span>
        </button>
        <button className="cc-send" disabled={busy || !canSend} onClick={doSend} title={busy ? "Claude is responding" : "Send"}>
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinejoin="round"><path d="M7 4.5l12 7.5-12 7.5z"></path></svg>
          Send
        </button>
        {busy && (
          <button className="cc-sq cc-stop" title="Interrupt" onClick={onInterrupt}><span className="stop-sq"></span></button>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { CluiComposer });
