// Trace Capsules — detail view, reframed as a PRIVACY-BOUNDED USAGE EPISODE.
//
// The journey is the asset. The episode (intent → product/version → docs
// consulted → commands attempted → error → workarounds → outcome) is the hero.
// The replayable test, dependency matrix and sealed parts are kept as a strong
// secondary "evidence" section — optional proof, not the spine.
//
// Side rail leads with the value exchange (get unblocked faster — auto-resume on
// fix) and a layered, inspectable redaction report ("what leaves the machine").
//
// Perspective + density come from the shared CapView store (Tweaks panel + an
// inline "View as" control both write to it).

// ── preserved helpers (other views depend on CtxTreeView) ────────
function CtxTreeView({ nodes }) {
  const kindIcon = { root: "grid", files: "file", file: "file", tool: "tool", read: "eye", exec: "play", msg: "conversation" };
  return (
    <div className="ctx-tree">
      {nodes.map((n, i) => (
        <div key={i} className={"ctx-node depth-" + n.depth} style={{ paddingLeft: 8 + n.depth * 18 }}>
          {n.depth > 0 && <span className="ctx-guide" />}
          <span className={"ctx-ico k-" + n.kind}><Icon name={kindIcon[n.kind] || "dot"} size={12} /></span>
          <span className="ctx-label">{n.label}</span>
          <span className="ctx-meta mono">{n.meta}</span>
        </div>
      ))}
    </div>
  );
}

function ActorTag({ actor }) {
  const maint = actor === "maintainer";
  return (
    <span className={"actor-tag " + (maint ? "maint" : "client")}>
      <Icon name={maint ? "shield" : "send"} size={10} />
      {maint ? "maintainer" : "client"}
    </span>
  );
}

function ReplayRow({ r }) {
  const running = r.outcome === "running";
  const pass = r.outcome === "pass";
  return (
    <div className={"replay-row ro-" + r.outcome}>
      <div className="rr-left">
        <span className={"rr-outcome " + (running ? "running" : pass ? "pass" : "fail")}>
          {running ? <span className="rr-spin" /> : <Icon name={pass ? "check" : "alert"} size={13} />}
        </span>
        <div className="rr-body">
          <div className="rr-top">
            <ActorTag actor={r.actor} />
            {r.agent && <span className="rr-agent"><AgentGlyph agent={r.agent} size={12} /> {r.agent.name}</span>}
            <span className="rr-sha mono">@{r.sha}</span>
          </div>
          <div className="rr-note">{r.note}</div>
        </div>
      </div>
      <div className="rr-right">
        <span className={"rr-verdict " + (running ? "running" : pass ? "pass" : "fail")}>{running ? "running" : pass ? "PASS" : "FAIL"}</span>
        <span className="rr-when mono">{r.dur} · {r.when}</span>
      </div>
    </div>
  );
}

function LifecycleStepper({ cap }) {
  const idx = CAPSULE_LIFECYCLE.findIndex(s => s.key === cap.lifecycle);
  return (
    <div className="life-stepper compact">
      {CAPSULE_LIFECYCLE.map((s, i) => {
        const state = i < idx ? "done" : i === idx ? "active" : "todo";
        return (
          <div key={s.key} className={"ls-step ls-" + state} data-tone={LIFE_TONE[s.key]}>
            <span className="ls-node">{state === "done" ? <Icon name="check" size={10} /> : <span className="ls-num">{i + 1}</span>}</span>
            <div className="ls-body"><div className="ls-label">{s.label}</div></div>
            {i < CAPSULE_LIFECYCLE.length - 1 && <span className="ls-bar" />}
          </div>
        );
      })}
    </div>
  );
}

const SUBSCRIBER_POOL = [
  { name: "next-bot", icon: "gemini" }, { name: "openai-bot", icon: "codex" },
  { name: "Claude Code", icon: "claude" }, { name: "Cursor", icon: "cursor" },
  { name: "pytorch-bot", icon: "opencode" },
];

// ── episode trail (the hero) ─────────────────────────────────────
function ResultChip({ result }) {
  const ok = result === "ok";
  return <span className={"ep-result " + (ok ? "ok" : "fail")}><Icon name={ok ? "check" : "x"} size={9} /> {ok ? "ok" : "failed"}</span>;
}

function EpisodeStage({ stage }) {
  return (
    <div className={"ep-stage tone-" + stage.tone + (stage.accent ? " accent" : "")}>
      <div className="ep-rail">
        <span className="ep-node"><Icon name={stage.icon} size={13} /></span>
      </div>
      <div className="ep-content">
        <div className="ep-stage-head">
          <span className="ep-stage-label">{stage.label}</span>
          {stage.meta && <span className="ep-stage-meta mono">{stage.meta}</span>}
        </div>
        {stage.body}
      </div>
    </div>
  );
}

function EpisodeCard({ cap, ep }) {
  const t = cap.triage;
  const d = t.deps;
  const main = t.head.onMain;
  const outcomeTone = main === "pass" ? "git" : main === "inconclusive" ? "plan" : "error";
  const stages = [];

  stages.push({
    key: "intent", icon: "activity", tone: "user", label: "Intent",
    body: <p className="ep-text">{cap.intent}</p>,
  });

  stages.push({
    key: "product", icon: "box", tone: "read", label: "Product & version",
    body: (
      <div className="ep-product">
        <span className="ep-pkg mono">{d ? d.pkg : cap.repo.split("/")[1]}<span className="ep-at">@</span>{cap.git.base.replace(/^v/, "")}</span>
        <span className="ep-env"><Icon name="settings" size={11} /> {ep.env}</span>
      </div>
    ),
  });

  if (ep.docs && ep.docs.length) stages.push({
    key: "docs", icon: "file", tone: "read", label: "Docs & source consulted", meta: ep.docs.length + " sources",
    body: (
      <ul className="ep-list ep-docs">
        {ep.docs.map((doc, i) => (
          <li key={i}><span className="ep-doc-src mono">{doc.src}</span><span className="ep-doc-note">{doc.note}</span></li>
        ))}
      </ul>
    ),
  });

  if (ep.commands && ep.commands.length) stages.push({
    key: "commands", icon: "tool", tone: "exec", label: "Commands & API calls attempted",
    body: (
      <div className="ep-cmds">
        {ep.commands.map((c, i) => (
          <div className="ep-cmd" key={i}>
            <span className="ep-cmd-dot" data-kind={c.kind} />
            <code className="ep-cmd-txt">{c.cmd}</code>
            <ResultChip result={c.result} />
          </div>
        ))}
      </div>
    ),
  });

  stages.push({
    key: "error", icon: "alert", tone: "error", accent: true, label: "Error / mismatch", meta: "step " + cap.fail.step + " · " + cap.fail.lane,
    body: <pre className="ep-error mono">{cap.fail.error}</pre>,
  });

  if (ep.workarounds && ep.workarounds.length) stages.push({
    key: "workarounds", icon: "replay", tone: "plan", label: "Workaround attempts",
    body: (
      <ul className="ep-list ep-work">
        {ep.workarounds.map((w, i) => (
          <li key={i} className={"ep-wk " + w.kind}>
            <span className="ep-wk-dot" />
            <span className="ep-wk-text">{w.text}</span>
            <span className="ep-wk-arrow"><Icon name="arrow-right" size={11} /></span>
            <span className="ep-wk-result">{w.result}</span>
          </li>
        ))}
      </ul>
    ),
  });

  stages.push({
    key: "outcome", icon: main === "pass" ? "check" : "dot", tone: outcomeTone, label: "Outcome",
    body: <p className="ep-text ep-outcome">{ep.outcome}</p>,
  });

  return (
    <div className="ep-card">
      <div className="ep-card-head">
        <div className="ep-card-titles">
          <span className="ep-kicker mono">Usage episode</span>
          <p className="ep-card-sub">The journey one agent took using <span className="mono">{cap.triage.deps?.pkg || cap.repo.split("/")[1]}</span> — and where it derailed. This is the shared asset; a test is optional evidence below.</p>
        </div>
      </div>
      <div className="ep-trail">
        {stages.map(s => <EpisodeStage key={s.key} stage={s} />)}
      </div>
    </div>
  );
}

// ── value exchange: get unblocked faster ─────────────────────────
function AutoResumeCard({ cap, autoResume, onToggleAuto, onResume, onSubscribe }) {
  const t = cap.triage;
  const main = t.head.onMain;
  const solver = (t.deps?.matrix || []).find(m => m.s === "pass");
  const resolved = main === "pass";
  const im = t.impact || { blocked: 0, pulling: 0 };
  return (
    <div className={"vx-card " + (resolved ? "resolved" : "watching")}>
      <div className="vx-head"><Icon name="bell" size={13} /> Get unblocked faster</div>

      <div className="vx-verdict"><LiveVerdict cap={cap} /></div>

      {resolved ? (
        <div className="vx-state ok">
          <div className="vx-state-h"><Icon name="check" size={14} /> Fix is live{solver && <> in <span className="mono">{t.deps.pkg}@{solver.ver}</span></>}</div>
          <div className="vx-state-s">Your agent can re-pose the original intent against the new HEAD right now.</div>
          <button className="vx-primary" onClick={onResume}><Icon name="play" size={13} /> Resume the task</button>
        </div>
      ) : (
        <div className="vx-state wait">
          <div className="vx-toggle-row">
            <div className="vx-toggle-l">
              <div className="vx-state-h">Auto-resume on fix</div>
              <div className="vx-state-s">When this is fixed upstream, your agent is pushed a message and re-runs the intent — no polling.</div>
            </div>
            <button className="vx-switch" data-on={autoResume ? "1" : "0"} role="switch" aria-checked={autoResume} onClick={onToggleAuto}><i /></button>
          </div>
          <button className="vx-primary ghost" onClick={onSubscribe}><Icon name="bell" size={13} /> {autoResume ? "Watching main · push enabled" : "Subscribe for the fix"}</button>
        </div>
      )}

      <div className="vx-flow">
        <span className="vx-step"><span className="vx-num">1</span> subscribe</span>
        <Icon name="arrow-right" size={11} className="vx-sep" />
        <span className="vx-step"><span className="vx-num">2</span> fix lands</span>
        <Icon name="arrow-right" size={11} className="vx-sep" />
        <span className="vx-step"><span className="vx-num">3</span> auto re-pose & resume</span>
      </div>

      <div className="vx-impact">
        <Icon name="node" size={11} />
        <span><strong>{im.pulling}</strong> agents pulling this episode{im.blocked > 0 && <> · <span className="vx-blocked">{im.blocked} blocked like you</span></>}</span>
      </div>
    </div>
  );
}

// ── what leaves the machine: layered, inspectable redaction ──────
function RedactionLayer({ layer, open, onToggle }) {
  const meta = REDACT_META[layer.key];
  const tn = REDACT_TONE[layer.action] || REDACT_TONE.clean;
  const has = layer.samples && layer.samples.length > 0;
  return (
    <div className={"rd-layer rd-" + tn.tone + (layer.action === "pending" ? " rd-pending" : "")}>
      <button className="rd-row" onClick={onToggle} aria-expanded={open}>
        <span className="rd-ico"><Icon name={meta.icon} size={13} /></span>
        <span className="rd-label">{meta.label}</span>
        <span className="rd-count mono">{layer.count > 0 ? layer.count : "—"}</span>
        <span className={"rd-action rd-a-" + tn.tone}>{tn.word}</span>
        <Icon name="chevron-right" size={12} className="rd-chev" />
      </button>
      {open && (
        <div className="rd-detail">
          <p className="rd-note">{layer.note}</p>
          {has && (
            <div className="rd-samples">
              {layer.samples.map((s, i) => <div className="rd-sample mono" key={i}>{s}</div>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function RedactionPanel({ cap, perspective }) {
  const red = React.useMemo(() => buildRedaction(cap), [cap]);
  const [open, setOpen] = React.useState(null);
  const preshare = perspective === "preshare";
  const scanning = red.state !== "clean";
  return (
    <div className={"rd-card " + (scanning ? "scanning" : "clean") + (preshare ? " review" : "")}>
      <div className="rd-head">
        <Icon name="shield" size={13} />
        <span>What leaves the machine</span>
        {scanning && <span className="rd-scan-tag"><span className="rd-spin" /> scanning</span>}
      </div>

      <div className="rd-summary">
        <span className="rd-sum-n mono">{red.totalHandled}</span>
        <span className="rd-sum-l">items redacted across {red.layers.length} layers · scanned {red.scanned}</span>
      </div>

      {preshare && red.pendingCount > 0 && (
        <div className="rd-review-strip">
          <Icon name="alert" size={12} />
          <span><strong>{red.pendingCount}</strong> {red.pendingCount === 1 ? "layer needs" : "layers need"} your review before this can be shared.</span>
        </div>
      )}

      <div className="rd-layers">
        {red.layers.map(l => (
          <RedactionLayer key={l.key} layer={l} open={open === l.key} onToggle={() => setOpen(open === l.key ? null : l.key)} />
        ))}
      </div>

      <div className="rd-foot">
        <Icon name="lock" size={11} />
        <span>Narrow by default — only the failing episode leaves, never the full session.</span>
      </div>
    </div>
  );
}

// ── evidence: optional, replayable, secondary ────────────────────
function SealedParts({ cap }) {
  const m = cap.manifest;
  const parts = [
    { icon: "activity", key: "action_trajectory", meta: m.trajectory.bytes },
    { icon: "node", key: "ctx_tree", meta: m.ctx.bytes },
    { icon: "alert", key: "trace_step", meta: m.failStep.bytes },
    { icon: "box", key: "git_snapshot", meta: m.git.sha },
  ];
  return (
    <div className="ev-sealed">
      {parts.map(p => (
        <div className="ev-part" key={p.key}>
          <Icon name={p.icon} size={13} />
          <span className="ev-part-k mono">{p.key}</span>
          <span className="ev-part-m mono">{p.meta}</span>
        </div>
      ))}
    </div>
  );
}

function EvidenceSection({ cap, open, onToggle, onRunReplay, onOpenTrace }) {
  const t = cap.triage;
  const d = t.deps;
  const running = cap.replays.some(r => r.outcome === "running");
  const solver = (d?.matrix || []).find(m => m.s === "pass");
  return (
    <div className="ev-card">
      <button className="ev-head" onClick={onToggle} aria-expanded={open}>
        <div className="ev-head-l">
          <Icon name="check" size={14} className="ev-head-ico" />
          <div>
            <div className="ev-title">Evidence — replayable test <span className="ev-optional">optional</span></div>
            <div className="ev-sub">The journey is the asset. A replayable test is attached as proof; the capsule doesn’t depend on one existing.</div>
          </div>
        </div>
        <Icon name="chevron-right" size={15} className={"ev-chev" + (open ? " open" : "")} />
      </button>

      {open && (
        <div className="ev-body">
          {/* replay */}
          <div className="ev-block">
            <div className="ev-block-head">
              <span className="ev-block-t mono">Replays</span>
              <button className="cap-btn play" onClick={onRunReplay} disabled={running}>
                <Icon name={running ? "replay" : "play"} size={13} /> {running ? "Replaying…" : "Run replay"}
              </button>
            </div>
            <div className="replay-list">
              {cap.replays.map((r, i) => <ReplayRow key={r._id || i} r={r} />)}
            </div>
          </div>

          {/* dependency matrix */}
          {d && (
            <div className="ev-block">
              <div className="ev-block-head">
                <span className="ev-block-t mono">Which version resolves it</span>
                <span className="ev-block-meta">verdict = oracle(intent, code@version)</span>
              </div>
              <div className="dep-matrix-lg">
                <span className="dml-runtime"><Icon name="box" size={12} /> {d.runtime}</span>
                <div className="dml-grid">
                  {d.matrix.map((mx, i) => (
                    <div key={i} className={"dml-cell dm-" + (mx.s === "pass" ? "git" : mx.s === "incon" ? "plan" : "error")}>
                      <span className="dml-vdot" />
                      <span className="dml-ver mono">{d.pkg}@{mx.ver}</span>
                      <span className="dml-state">{mx.s === "pass" ? "passes" : mx.s === "incon" ? "inconclusive" : "fails"}</span>
                    </div>
                  ))}
                </div>
              </div>
              {solver && <div className="ev-solver"><Icon name="check" size={12} /> <span className="mono">{d.pkg}@{solver.ver}</span> resolves the episode</div>}
            </div>
          )}

          {/* sealed parts + inspect */}
          <div className="ev-block">
            <div className="ev-block-head">
              <span className="ev-block-t mono">What’s sealed inside</span>
              <button className="part-link" onClick={() => onOpenTrace && onOpenTrace(RECENT_TRACES[0].id)}><Icon name="external" size={12} /> Inspect in viewer</button>
            </div>
            <SealedParts cap={cap} />
          </div>
        </div>
      )}
    </div>
  );
}

// ── perspective bar (state-specific) ─────────────────────────────
function PerspectiveBar({ perspective, cap, red, onPublish, onUnpublish, onCopyLink, onPull, onSubscribe }) {
  if (perspective === "published") {
    return (
      <div className="pb pb-published">
        <div className="pb-l">
          <span className="pb-ico"><Icon name="globe" size={16} /></span>
          <div>
            <div className="pb-h">Published · discoverable in the registry</div>
            <div className="pb-s">{cap.stats.views} views · {cap.stats.pulls} agent pulls · {cap.stats.subscribers} subscribers waiting on the fix</div>
          </div>
        </div>
        <div className="pb-actions">
          <button className="cap-btn" onClick={onCopyLink}><Icon name="link" size={13} /> Copy link</button>
          <button className="cap-btn danger" onClick={onUnpublish}><Icon name="lock" size={13} /> Unpublish</button>
        </div>
      </div>
    );
  }
  if (perspective === "consumer") {
    return (
      <div className="pb pb-consumer">
        <div className="pb-l">
          <span className="pb-ico"><Icon name="box" size={16} /></span>
          <div>
            <div className="pb-h">Shared with you — a replayable usage episode</div>
            <div className="pb-s">Pull it to reproduce locally, or subscribe to auto-unblock when it’s fixed.</div>
          </div>
        </div>
        <div className="pb-actions">
          <button className="cap-btn" onClick={onSubscribe}><Icon name="bell" size={13} /> Subscribe</button>
          <button className="cap-btn primary" onClick={onPull}><Icon name="box" size={13} /> Pull capsule</button>
        </div>
      </div>
    );
  }
  // preshare
  const pending = red.pendingCount > 0;
  return (
    <div className={"pb pb-preshare" + (pending ? " has-pending" : "")}>
      <div className="pb-l">
        <span className="pb-ico"><Icon name="shield" size={16} /></span>
        <div>
          <div className="pb-h">Review before it leaves your machine</div>
          <div className="pb-s">{pending
            ? <>{red.pendingCount} redaction {red.pendingCount === 1 ? "layer needs" : "layers need"} review. Confirm what’s shared, then publish.</>
            : <>{red.totalHandled} items redacted across {red.layers.length} layers. Confirm and publish when you’re ready.</>}</div>
        </div>
      </div>
      <div className="pb-actions">
        <button className="cap-btn"><Icon name="file" size={13} /> Save draft</button>
        <button className="cap-btn primary" onClick={onPublish} disabled={pending}>
          <Icon name="check" size={13} /> {pending ? "Resolve reviews first" : "Approve & publish"}
        </button>
      </div>
    </div>
  );
}

// ── inline view-as + density control ─────────────────────────────
function ViewAsControl({ perspective, density, onPerspective, onDensity }) {
  const persp = [
    { id: "preshare", label: "Pre-share" },
    { id: "published", label: "Published" },
    { id: "consumer", label: "Consumer" },
  ];
  return (
    <div className="va-control">
      <span className="va-label">View as</span>
      <div className="va-seg" role="tablist">
        {persp.map(p => (
          <button key={p.id} className={"va-opt" + (perspective === p.id ? " on" : "")} role="tab" aria-selected={perspective === p.id} onClick={() => onPerspective(p.id)}>{p.label}</button>
        ))}
      </div>
      <button className="va-density" title={"Density: " + density} onClick={() => onDensity(density === "compact" ? "comfortable" : "compact")}>
        <Icon name="rows" size={14} />
        <span>{density === "compact" ? "Compact" : "Comfortable"}</span>
      </button>
    </div>
  );
}

// ── the detail view ──────────────────────────────────────────────
function CapsuleDetail({ cap, isOwner, onBack, onToggleVis, onCopyLink, onCopyPull, onShare, onRunReplay, onRecheck, onPost, onToggleIssue, busy, onOpenTrace }) {
  const view = useCapView();
  const perspective = view.perspective;
  const density = view.density;
  const setPerspective = (p) => window.CapView.set({ perspective: p });
  const setDensity = (d) => window.CapView.set({ density: d });

  const [autoResume, setAutoResume] = React.useState(true);
  const [evOpen, setEvOpen] = React.useState(true);

  const ep = React.useMemo(() => deriveEpisode(cap), [cap]);
  const red = React.useMemo(() => buildRedaction(cap), [cap]);
  const t = cap.triage;
  const clean = cap.scrub.state === "clean";
  const main = t.head.onMain;
  const issueClosed = cap.issue && cap.issue.state === "closed";
  const subs = SUBSCRIBER_POOL.slice(0, Math.min(cap.stats.subscribers, SUBSCRIBER_POOL.length));
  const showLoop = perspective !== "consumer" && cap.issue && cap.issue.number != null;

  return (
    <div className="landing cap-detail cap-episode" data-density={density} data-perspective={perspective}>
      <div className="cd-head">
        <div className="cd-head-bar">
          <button className="cd-back" onClick={onBack}><Icon name="back" size={14} /> All capsules</button>
          <ViewAsControl perspective={perspective} density={density} onPerspective={setPerspective} onDensity={setDensity} />
        </div>

        <div className="cd-head-main">
          <CapsuleSeal lane={cap.fail.lane} size={48} sealed={clean} />
          <div className="cd-head-titles">
            <div className="cd-version mono"><Icon name={clean ? "lock" : "shield"} size={12} /> {cap.v} <span className="cd-vtag">usage episode</span></div>
            <h1 className="cd-title">{cap.title}</h1>
            <div className="cd-sub">
              <span className="cd-cid mono">{cap.cid}</span>
              <span className="dot-sep" />
              <RepoLabel repo={cap.repo} />
              <span className="dot-sep" />
              <span className="cd-by"><AgentGlyph agent={cap.agent} size={12} /> {cap.agent.name}</span>
              <span className="dot-sep" />
              <span className="cd-author">by {cap.owner ? "you" : cap.author}</span>
            </div>
          </div>
        </div>

        <PerspectiveBar
          perspective={perspective}
          cap={cap}
          red={red}
          onPublish={onToggleVis}
          onUnpublish={onToggleVis}
          onCopyLink={onCopyLink}
          onPull={onShare}
          onSubscribe={onShare}
        />

        {t.incoherent && (
          <div className="cd-incoherent">
            <Icon name="alert" size={14} />
            <span>Marked <strong>{cap.lifecycle}</strong>{issueClosed ? " & issue #" + cap.issue.number + " closed" : ""}, but the replay still <strong>fails on main</strong>{t.head.survival === "reverted" ? " — the fix was reverted." : "."}</span>
            {cap.issue && <button className="cap-btn sm loop" onClick={onToggleIssue}><Icon name="issue" size={12} /> Reopen #{cap.issue.number}</button>}
          </div>
        )}
      </div>

      <div className="ep-grid">
        {/* MAIN — the episode + evidence */}
        <div className="ep-main">
          <EpisodeCard cap={cap} ep={ep} />
          <EvidenceSection cap={cap} open={evOpen} onToggle={() => setEvOpen(o => !o)} onRunReplay={onRunReplay} onOpenTrace={onOpenTrace} />
        </div>

        {/* SIDE — value exchange + redaction + loop */}
        <aside className="ep-side">
          <AutoResumeCard
            cap={cap}
            autoResume={autoResume}
            onToggleAuto={() => setAutoResume(v => !v)}
            onResume={onShare}
            onSubscribe={onShare}
          />

          <RedactionPanel cap={cap} perspective={perspective} />

          {showLoop && (
            <div className={"side-card loop-card" + (t.issueDrift ? " drift" : "")}>
              <div className="sc-head"><Icon name="issue" size={13} /> Loop status</div>
              <LifecycleStepper cap={cap} />
              <div className="loop-issue">
                <span className={"loop-verdict v-" + VERDICT_TONE[main]}><VerdictDot state={main} /> {VERDICT_LABEL[main]} @main</span>
                <Icon name="link" size={11} className="loop-link" />
                <span className={"loop-iss " + (issueClosed ? "closed" : "open")}><Icon name="issue" size={11} /> #{cap.issue.number} {issueClosed ? "closed" : "open"}</span>
              </div>
              {t.issueDrift && (
                <div className="loop-drift"><Icon name="alert" size={11} /> {main === "pass" ? "Passes on main but the issue is still open." : "Issue closed but the replay fails on main."}</div>
              )}
              <div className="loop-actions">
                <button className="cap-btn sm" onClick={onPost}><Icon name="send" size={12} /> Post verdict</button>
                <button className="cap-btn sm" onClick={onToggleIssue}><Icon name="issue" size={12} /> {issueClosed ? "Reopen" : "Close"}</button>
              </div>
            </div>
          )}

          {perspective === "published" && cap.stats.subscribers > 0 && (
            <div className="side-card">
              <div className="sc-head"><Icon name="bell" size={13} /> Subscribers · auto-unblock</div>
              <div className="subs-list">
                {subs.map((s, i) => (
                  <div className="subs-row" key={i}>
                    <AgentGlyph agent={{ icon: s.icon }} size={16} />
                    <span className="subs-name">{s.name}</span>
                    <span className="subs-tag mono">auto-resume</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

window.CapsuleDetail = CapsuleDetail;
window.CtxTreeView = CtxTreeView;
