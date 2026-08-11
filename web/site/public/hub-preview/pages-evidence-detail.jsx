// pages-evidence-detail.jsx — the Evidence entity, two renderings of one
// frozen run record:
//   Page (people): rewatchable terminal cast, the red proof shown before
//   the green is believed, scorecard, skips said out loud, annotations.
//   Feed (agents): verdict digest, skip list, score breakdown, re-pose packet.
// Renderings format, never add.

// ── Cast player — rewatchable terminal film ─────────────────────
// Film is never an input to a verdict; it is for people. Playback position
// persists per record so a refresh lands where you left off.
function CastPlayer({ ev }) {
  const cast = ev.cast;
  const storeKey = "ot-ev-cast-" + ev.id;
  const [t, setT] = React.useState(() => {
    try { const v = parseFloat(localStorage.getItem(storeKey)); return isNaN(v) ? 0 : Math.min(v, cast ? cast.dur : 0); } catch (e) { return 0; }
  });
  const [playing, setPlaying] = React.useState(false);
  const scrollRef = React.useRef(null);
  const raf = React.useRef(0);
  const last = React.useRef(0);

  React.useEffect(() => {
    try { localStorage.setItem(storeKey, String(t)); } catch (e) {}
  }, [t]);

  React.useEffect(() => {
    if (!playing || !cast) return;
    last.current = performance.now();
    const tick = (now) => {
      const dt = (now - last.current) / 1000;
      last.current = now;
      setT(prev => {
        const next = prev + dt;
        if (next >= cast.dur) { setPlaying(false); return cast.dur; }
        return next;
      });
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [playing]);

  const visible = React.useMemo(
    () => cast ? cast.frames.filter(f => f.t <= t).flatMap(f => f.lines) : [],
    [cast, t]
  );
  React.useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [visible.length]);

  if (!cast) {
    return (
      <div className="cast-shell cast-missing">
        <div className="cast-missing-inner">
          <span className="mono">rewatchable: false</span>
          <p>No terminal cast exists for this run. The record says so out loud — the verdict below stands on the ledger, not on film.</p>
        </div>
      </div>
    );
  }

  // Track markers: red-proof span, verdict moment.
  const redFrames = cast.frames.filter(f => f.lines.some(l => l.tone === "red"));
  const greenFrame = cast.frames.find(f => f.lines.some(l => l.tone === "green"));
  const toggle = () => {
    if (!playing && t >= cast.dur) setT(0);
    setPlaying(p => !p);
  };
  const fmt = (s) => Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0");

  return (
    <div className="cast-shell">
      <div className="cast-term" ref={scrollRef}>
        {visible.map((l, i) => (
          <div key={i} className={"cast-line cl-" + l.tone}>{l.text}</div>
        ))}
        {playing && <span className="cast-cursor" />}
      </div>
      <div className="cast-bar">
        <button className="cast-btn" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
          {playing
            ? <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="4" width="5" height="16" rx="1"/><rect x="14" y="4" width="5" height="16" rx="1"/></svg>
            : <Icon name="play" size={12} />}
        </button>
        <div className="cast-track-wrap">
          <input
            className="cast-track" type="range" min="0" max={cast.dur} step="0.1" value={t}
            onChange={(e) => { setT(parseFloat(e.target.value)); setPlaying(false); }}
            aria-label="Scrub the cast"
          />
          <div className="cast-marks">
            {redFrames.length > 0 && (
              <span
                className="cast-mark red"
                style={{ left: (redFrames[0].t / cast.dur * 100) + "%", width: (((redFrames[redFrames.length - 1].t - redFrames[0].t) || 1) / cast.dur * 100) + "%" }}
                title="red proof — the verifier shown failing"
              />
            )}
            {greenFrame && (
              <span className="cast-mark green" style={{ left: (greenFrame.t / cast.dur * 100) + "%" }} title="verdict" />
            )}
          </div>
        </div>
        <span className="cast-time mono">{fmt(t)} / {fmt(cast.dur)}</span>
      </div>
    </div>
  );
}

// ── Feed rendering — JSON viewer for agents ─────────────────────
function FeedView({ ev }) {
  const feed = v2Feed(ev);
  const json = JSON.stringify(feed, null, 2);
  const html = json
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/"([^"]+)":/g, '<span class="jk">"$1"</span>:')
    .replace(/: "([^"]*)"/g, ': <span class="js">"$1"</span>')
    .replace(/: (\d+|true|false|null)/g, ': <span class="jn">$1</span>');
  return (
    <div className="feed-view">
      <div className="feed-head">
        <span className="mono">opentraces.evidence_feed.v0</span>
        <span className="feed-note">what an agent polls — the CLUI's diet for verification tasks</span>
      </div>
      <pre className="feed-json mono" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}

// ── Scorecard + skips + red proof ───────────────────────────────
function Scorecard({ ev }) {
  return (
    <div className="ev-card">
      <div className="ev-card-head">Scorecard</div>
      {ev.scorecard.map((s, i) => (
        <div key={i} className="sc-row">
          <VerdictPill v={s.result === "pass" ? "pass" : s.result === "fail" ? "fail" : s.result} />
          <div className="sc-body">
            <div className="sc-check">{s.check}</div>
            <div className="sc-detail">{s.detail}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

function RedProofCard({ run }) {
  return (
    <div className={"ev-card red-proof" + (run.redProof ? "" : " none")}>
      <div className="ev-card-head">Red proof</div>
      {run.redProof ? (
        <div className="rp-body">
          <VerdictPill v="fail" />
          <div>
            <div className="rp-note">{run.redProof.note}</div>
            <div className="rp-ref mono">{run.redProof.run}</div>
          </div>
        </div>
      ) : (
        <div className="rp-body">
          <span className="mono rp-null">red_proof_ref: null</span>
          <div className="rp-note">The verifier was never shown failing. Until it is, this green is not believed — the record says so honestly.</div>
        </div>
      )}
    </div>
  );
}

function SkipsCard({ run }) {
  const skips = run.skips || [];
  return (
    <div className="ev-card">
      <div className="ev-card-head">Skips — said out loud</div>
      {skips.length === 0
        ? <div className="ev-card-empty">No skips. Nothing was silently passed over.</div>
        : skips.map((s, i) => (
          <div key={i} className="skip-row">
            <span className="skip-name mono">{s.name}</span>
            <span className="skip-reason">{s.reason}</span>
          </div>
        ))}
    </div>
  );
}

function AnnotationsCard({ ev }) {
  const [notes, setNotes] = React.useState(ev.annotations || []);
  const [draft, setDraft] = React.useState("");
  const add = () => {
    if (!draft.trim()) return;
    setNotes(n => [...n, { who: "you", when: "now", anchor: null, text: draft.trim() }]);
    setDraft("");
  };
  return (
    <div className="ev-card">
      <div className="ev-card-head">Annotations <span className="ev-card-hint">a channel beside the record — never inside it</span></div>
      {notes.map((n, i) => (
        <div key={i} className="anno-row">
          <div className="anno-meta">
            <span className="anno-who">{n.who}</span>
            {n.anchor && <span className="anno-anchor mono">{n.anchor}</span>}
            <span className="anno-when">{n.when}</span>
          </div>
          <div className="anno-text">{n.text}</div>
        </div>
      ))}
      <div className="anno-compose">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") add(); }}
          placeholder="Annotate this record…"
          spellCheck={false}
        />
        <button className="anno-send" onClick={add} disabled={!draft.trim()}><Icon name="send" size={13} /></button>
      </div>
    </div>
  );
}

// ── Hand to an agent — the feed is the agent's diet; this packages it ──
function SendToAgentCard({ ev }) {
  const [target, setTarget] = React.useState("local");
  const [copied, setCopied] = React.useState(false);
  const feed = v2Feed(ev);
  const prompt = [
    "> Fetch this run's frozen evidence feed (opentraces.evidence_feed.v0):",
    "  ot evidence feed " + ev.run + " --json",
    "",
    "  Verdict, named skips, score breakdown and the re-pose packet are in",
    "  the feed — act on those, never on the film.",
    "",
    "> Re-pose the run yourself:",
    "  " + feed.re_pose.cmd,
  ].join(String.fromCharCode(10));
  const copy = () => {
    try { navigator.clipboard.writeText(prompt); } catch (e) {}
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };
  return (
    <div className="ev-card sta-card">
      <div className="ev-card-head">Hand to an agent <span className="ev-card-hint">the feed is the diet — point it at this record</span></div>
      <div className="sta-seg" role="tablist">
        <button className="sta-seg-btn" aria-current={target === "local"} onClick={() => setTarget("local")}>Send to local coding agent</button>
        <button className="sta-seg-btn" aria-current={target === "web"} onClick={() => setTarget("web")}>Send to Claude Code Web</button>
      </div>
      <div className="sta-term">
        <div className="sta-term-bar">
          <span className="sta-dots"><i></i><i></i><i></i></span>
          <span className="sta-term-title">{target === "local" ? "Claude Code" : "Claude Code Web"}</span>
        </div>
        <pre className="sta-prompt mono">{prompt}</pre>
      </div>
      <button className="sta-copy" onClick={copy}>
        <Icon name="copy" size={13} /> {copied ? "Copied" : "Copy prompt"}
      </button>
    </div>
  );
}

// ── The detail page ─────────────────────────────────────────────
function EvidenceDetail({ evidenceId, onBack, onOpenTrace, onOpenChecks, sealedBanner }) {
  const ev = v2Evidence(evidenceId);
  const [rendering, setRendering] = React.useState("page");
  if (!ev) return <div className="v2-empty" style={{ padding: 40 }}>Evidence record not found.</div>;
  const run = v2Run(ev.run) || {};
  const scn = v2Scenario(run.scenario);
  const vf = v2Verifier(run.verifier);
  const g = V2_GUARANTEES.find(x => x.id === ev.guarantee);

  return (
    <div className="landing landing-page v2-page ev-detail" data-screen-label={"evidence-" + ev.id}>
      {sealedBanner}
      <div className="ev-d-top">
        <button className="v2-back" onClick={onBack}><Icon name="back" size={13} /> Evidence</button>
        <div className="ev-d-render" role="tablist" aria-label="Rendering">
          <button className="ev-d-tab" aria-current={rendering === "page"} onClick={() => setRendering("page")}>Page <span className="hint">for people</span></button>
          <button className="ev-d-tab" aria-current={rendering === "feed"} onClick={() => setRendering("feed")}>Feed <span className="hint">for agents</span></button>
        </div>
      </div>

      <header className="ev-d-head">
        <VerdictPill v={run.verdict} big detail={run.failNote} />
        <div className="ev-d-titles">
          <h1>{ev.title}</h1>
          <div className="ev-d-meta">
            <span className="mono">{ev.run}</span>
            {vf && <VerifierTag name={vf.name} ver={vf.ver} method={vf.method} current={vf.current} />}
            {scn && <span className="mono ev-d-dim">scn@{scn.digest}</span>}
            {run.appState && <span className="mono ev-d-dim">app_state@{run.appState}</span>}
            <SandboxTier tier={run.tier} />
            <FacetChip facet={run.facet || "manufactured"} />
            {run.joined ? <AddressLink addr={run.joined} onOpen={onOpenTrace} /> : <span className="addr-none mono" title="This run has no join stamp back into a trace.">no join</span>}
            {g && (
              <button className="ev-d-guar" onClick={() => onOpenChecks && onOpenChecks(g.id)} title="The guarantee row this run answers to, on the atlas.">
                <Icon name="shield" size={12} /> {g.plain}
              </button>
            )}
          </div>
        </div>
        <span className="ev-d-when mono">{run.when}</span>
      </header>

      {rendering === "feed" ? (
        <FeedView ev={ev} />
      ) : (
        <div className="ev-d-grid">
          <div className="ev-d-main">
            <CastPlayer ev={ev} />
            <AnnotationsCard ev={ev} />
          </div>
          <div className="ev-d-side">
            <SendToAgentCard ev={ev} />
            <RedProofCard run={run} />
            <Scorecard ev={ev} />
            <SkipsCard run={run} />
            <div className="ev-card ev-frozen">
              <div className="ev-card-head">Frozen record</div>
              <div className="ev-frozen-rows mono">
                <div><span>envelope</span><span>opentraces.arena.run_record.v0</span></div>
                <div><span>kind</span><span>{run.kind}</span></div>
                <div><span>rc</span><span>{run.rc}</span></div>
                <div><span>duration</span><span>{run.dur}</span></div>
                <div><span>box</span><span>{run.box}</span></div>
              </div>
              <div className="ev-frozen-note">Nothing sealed ever changes. A re-run is a new run_id.</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

window.EvidenceDetail = EvidenceDetail;
window.CastPlayer = CastPlayer;
