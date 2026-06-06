// Trace Capsules — registry (index) + shared primitives + page container.
// Detail view lives in pages-capsules-detail.jsx (CapsuleDetail).
//
// Reframe (v2): the list is a LIVE TRIAGE QUEUE, not a static bug feed.
// A verdict = oracle(intent, code@version). The card's primary fact is the
// verdict against *current main*, not the frozen snapshot it was sealed at.

// ── derive helpers ──────────────────────────────────────────────
function latestReplay(cap) { return cap.replays[cap.replays.length - 1]; }
function replayStatus(cap) { return latestReplay(cap).outcome; }

// Merge session overrides onto a capsule, then layer triage.
function effCap(cap, overrides) {
  const o = overrides[cap.id] || {};
  const merged = {
    ...cap,
    visibility: o.visibility ?? cap.visibility,
    lifecycle: o.lifecycle ?? cap.lifecycle,
    replays: o.replays ?? cap.replays,
    stats: o.stats ?? cap.stats,
    issue: o.issueState ? { ...cap.issue, state: o.issueState } : cap.issue,
  };
  const wt = withTriage(merged);
  if (o.recheck) wt.triage.head = { ...wt.triage.head, recheck: o.recheck };
  if (o.posted != null) wt.triage.issue = { ...wt.triage.issue, posted: o.posted, stale: false };
  return wt;
}

const LIFE_TONE = {
  reported: "user", triaged: "read", fixing: "push", resolved: "git", unblocked: "git",
};
const VERDICT_TONE = { pass: "git", fail: "error", inconclusive: "plan", running: "user" };
const VERDICT_LABEL = { pass: "passes", fail: "fails", inconclusive: "can't tell", running: "running" };

// ── small primitives ────────────────────────────────────────────
function VerdictDot({ state, pulse }) {
  return <span className={"vdot v-" + (VERDICT_TONE[state] || "error") + (pulse ? " pulse" : "")} />;
}

function CapsuleSeal({ lane = "exec", size = 34, sealed = true }) {
  return (
    <span className="cap-seal" data-lane={lane} style={{ width: size, height: size }}>
      <Icon name="capsule" size={Math.round(size * 0.5)} />
      {sealed && <span className="cap-seal-dot" />}
    </span>
  );
}

// Workflow is the PRIMARY status chip now (one of reported→unblocked).
function WorkflowChip({ stage, incoherent }) {
  const def = CAPSULE_LIFECYCLE.find(s => s.key === stage);
  return (
    <span className={"wf-chip wf-" + (LIFE_TONE[stage] || "read") + (incoherent ? " wf-bad" : "")}>
      <span className="wf-dot" />
      {def ? def.label : stage}
      {incoherent && <Icon name="alert" size={11} />}
    </span>
  );
}

// THE reframe: live verdict against main, with the snapshot delta.
function LiveVerdict({ cap, big }) {
  const t = cap.triage;
  const snap = t.snapshotVerdict;
  const main = t.head.onMain;
  const changed = snap !== main;
  return (
    <div className={"live-verdict" + (big ? " big" : "")}>
      <span className="lv-side lv-snap">
        <VerdictDot state={snap} />
        <span className="lv-txt">{VERDICT_LABEL[snap]}</span>
        <span className="lv-at mono">@{cap.git.sha}</span>
      </span>
      <Icon name={changed ? "arrow-right" : "arrow-right"} size={13} className="lv-arrow" />
      <span className={"lv-side lv-main v-" + VERDICT_TONE[main]}>
        <VerdictDot state={main} pulse={main === "fail"} />
        <span className="lv-txt strong">{VERDICT_LABEL[main]}</span>
        <span className="lv-at mono">@main</span>
      </span>
      <span className="lv-recheck"><Icon name="replay" size={11} /> rechecked {t.head.recheck}</span>
    </div>
  );
}

// Snapshot survival / drift against main.
function SurvivalTag({ cap }) {
  const t = cap.triage.head;
  if (t.survival === "lost")
    return <span className="surv surv-lost"><Icon name="alert" size={11} /> commit force-pushed · running from bundle</span>;
  if (t.survival === "reverted")
    return <span className="surv surv-reverted"><Icon name="alert" size={11} /> fix at {cap.git.sha} was reverted</span>;
  return <span className="surv surv-alive"><Icon name="git" size={11} /> {t.behind === 0 ? "tip of " + cap.git.branch : t.behind + " commits behind main"}</span>;
}

// Dependency-version matrix — which version resolves the story.
function DepMatrix({ cap, compact }) {
  const d = cap.triage.deps;
  if (!d) return null;
  const solver = d.matrix.find(m => m.s === "pass");
  return (
    <div className={"dep-matrix" + (compact ? " compact" : "")}>
      <span className="dm-runtime"><Icon name="box" size={11} /> {d.runtime}</span>
      <span className="dm-pkg mono">{d.pkg}</span>
      <span className="dm-vers">
        {d.matrix.map((m, i) => (
          <span key={i} className={"dm-ver dm-" + (m.s === "pass" ? "git" : m.s === "incon" ? "plan" : "error")} title={m.s === "incon" ? "inconclusive — couldn't run" : m.s}>
            <span className="dm-vdot" />{m.ver}
          </span>
        ))}
      </span>
      {solver && !compact && <span className="dm-solver mono">{solver.ver} resolves it</span>}
    </div>
  );
}

// Capsule verdict ↔ linked issue, with post-back state. Half the loop.
function IssueBinding({ cap }) {
  const t = cap.triage;
  if (!cap.issue || cap.issue.number == null)
    return <span className="ibind draft"><Icon name="file" size={11} /> no issue linked</span>;
  const closed = cap.issue.state === "closed";
  return (
    <span className={"ibind" + (t.issueDrift ? " drift" : "")}>
      <span className={"ib-issue " + (closed ? "closed" : "open")}><Icon name="issue" size={11} /> #{cap.issue.number} {closed ? "closed" : "open"}</span>
      <Icon name="link" size={10} className="ib-link" />
      {t.issue && t.issue.posted
        ? <span className={"ib-post " + (t.issue.stale ? "stale" : "ok")}><Icon name="check" size={10} /> verdict posted{t.issue.stale ? " · stale" : ""}</span>
        : <span className="ib-post none"><Icon name="dot" size={8} /> not posted</span>}
    </span>
  );
}

// Compact trajectory: encodes activity density + fail position (replaces 142 dots).
function MicroTraj({ cap, width = 132, height = 16 }) {
  const fp = cap.fingerprint;
  const n = fp.length;
  const failX = cap.failFrac;
  const tone = { user: "var(--c-user)", plan: "var(--c-plan)", think: "var(--c-think)", read: "var(--c-read)", exec: "var(--c-exec)", write: "var(--c-write)" };
  const bw = width / n;
  return (
    <svg className="micro-traj" width={width} height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
      {fp.map((c, i) => {
        const isFailZone = i / n > failX - 0.04 && i / n <= failX;
        const h = isFailZone ? height : (c === "think" ? height * 0.5 : c === "read" || c === "exec" ? height * 0.8 : height * 0.65);
        return <rect key={i} x={i * bw} y={height - h} width={Math.max(bw - 0.4, 0.6)} height={h} fill={tone[c]} opacity={i / n > failX ? 0.28 : 0.85} />;
      })}
      <rect x={Math.min(failX * width, width - 1.5)} y={0} width={1.5} height={height} fill="var(--c-error)" />
    </svg>
  );
}

// Optionally bucket a fingerprint down to `max` pills so they stay legible &
// clearly rounded at small sizes — preserving the dominant lane per bucket.
function downsampleFP(fp, max) {
  if (!max || fp.length <= max) return fp.slice();
  const bucket = fp.length / max, out = [];
  for (let i = 0; i < max; i++) {
    const slice = fp.slice(Math.floor(i * bucket), Math.max(Math.floor((i + 1) * bucket), Math.floor(i * bucket) + 1));
    const counts = {};
    slice.forEach(c => { counts[c] = (counts[c] || 0) + 1; });
    let best = slice[0], bestW = -1;
    Object.keys(counts).forEach(c => { const w = counts[c] * (c === "think" ? 0.6 : 1); if (w > bestW) { bestW = w; best = c; } });
    out.push(best);
  }
  return out;
}

// Resample a fingerprint to exactly `target` pills (nearest-neighbour). Used to
// make the wide expanded band read as thin LINES, not fat circles, when the
// underlying trace has only a few dozen steps.
function resampleFP(fp, target) {
  if (!target || target === fp.length) return fp.slice();
  const out = [];
  for (let i = 0; i < target; i++) out.push(fp[Math.min(Math.floor(i * fp.length / target), fp.length - 1)]);
  return out;
}

// Rounded-pill trajectory strip — the SAME vocabulary as the trace-viewer
// minimap (header.jsx <Minimap/>). Steps after the failure dim out; the fail
// point gets a cursor. Used at table-cell scale and as the expanded band.
function TrajMap({ cap, height = 22, gap = 2, max = 0, fill = 0, cursor = true, className = "" }) {
  const fp = fill ? resampleFP(cap.fingerprint, fill) : downsampleFP(cap.fingerprint, max);
  const n = fp.length;
  const failX = cap.failFrac;
  return (
    <span className={"trajmap " + className} style={{ height, gap: gap + "px" }} aria-hidden="true"
      title={cap.manifest.trajectory.steps + " steps · fails at step " + cap.fail.step}>
      {fp.map((c, i) => {
        const frac = (i + 1) / n;
        const isFail = i === Math.min(Math.round(failX * n), n - 1);
        return <span key={i} className={"tm-seg " + c + (isFail ? " fail" : "") + (frac > failX + 0.001 ? " dim" : "")} />;
      })}
      {cursor && <span className="tm-cursor" style={{ left: `${Math.min(failX * 100, 99.5)}%` }} />}
    </span>
  );
}

function ScrubMini({ cap }) {
  const clean = cap.scrub.state === "clean";
  const n = cap.scrub.pii + cap.scrub.secrets;
  return (
    <span className={"scrub-mini " + (clean ? "ok" : "run")} title={clean ? "Scrubbed — safe to share" : "Security scan running"}>
      <Icon name={clean ? "shield" : "replay"} size={11} />
      {clean ? (n === 0 ? "scrubbed" : "scrubbed · " + n + " redacted") : "scanning"}
    </span>
  );
}

function ImpactTag({ cap }) {
  const im = cap.triage.impact || { blocked: 0, pulling: 0 };
  return (
    <span className="impact-tag" title="Downstream agents blocked / still pulling this capsule">
      <Icon name="node" size={11} />
      <strong>{im.pulling}</strong> pulling{im.blocked > 0 && <><span className="it-sep">·</span><span className="it-blocked">{im.blocked} blocked</span></>}
    </span>
  );
}

// ── share provenance ────────────────────────────────────────────
// A capsule is a shared, replayable trace first. Some get attached to a GitHub
// issue (when mentioned there); the rest are shared point-to-point by link.
function shareMode(cap) {
  if (cap.issue && cap.issue.number != null) return { kind: "issue", state: cap.issue.state };
  return { kind: "link", private: cap.visibility !== "published" };
}

// ── index card (shared capsule) ─────────────────────────────────
// Leads with how it's shared — an issue binding or a plain link — plus who
// shared it and its reach. The verdict is a small secondary signal; the rest
// (deps, trajectory, blast radius, every action) lives in the detail.
function CapsuleCard({ cap, onOpen, onCopyLink }) {
  const t = cap.triage;
  const main = t.head.onMain;
  const tone = VERDICT_TONE[main];
  const statusWord = main === "pass" ? "passes on main" : main === "inconclusive" ? "can’t tell yet" : "fails on main";
  const sm = shareMode(cap);
  const solver = (t.deps?.matrix || []).find(m => m.s === "pass");
  const lead = main === "pass" && solver
    ? <span className="cc-fixed">fixed in <span className="mono">{t.deps.pkg}@{solver.ver}</span></span>
    : <span className="mono cc-err">{cap.fail.summary.split("\n")[0]}</span>;

  return (
    <div className={"cap-card shared share-" + sm.kind + (sm.private ? " private" : "")}
      data-vis={cap.visibility} role="button" tabIndex={0}
      onClick={() => onOpen(cap.id)}
      onKeyDown={(e) => { if (e.key === "Enter") onOpen(cap.id); }}>
      <div className="sh-seal"><CapsuleSeal lane={cap.fail.lane} size={40} sealed={cap.scrub.state === "clean"} /></div>

      <div className="sh-body">
        <div className="sh-top">
          <div className="sh-title">{cap.title}</div>
          <div className="sh-reach">
            <span className="sh-stat" title="views"><Icon name="eye" size={13} />{cap.stats.views}</span>
            <span className="sh-stat" title="agent pulls"><Icon name="box" size={13} />{cap.stats.pulls}</span>
          </div>
        </div>

        <div className="sh-share">
          {sm.kind === "issue"
            ? <span className={"sh-issue " + (sm.state === "closed" ? "closed" : "open")}>
                <Icon name="issue" size={12} /><span className="mono">{cap.repo}</span>
                <span className="sh-issue-num mono">#{cap.issue.number}</span>
                <span className="sh-issue-state">{sm.state}</span>
              </span>
            : <span className="sh-linkchip"><Icon name="link" size={12} /> Shared via link{sm.private && <span className="sh-private"><Icon name="lock" size={10} /> private</span>}</span>}
          <span className="dot-sep" />
          <span className="sh-by"><AgentGlyph agent={cap.agent} size={13} /> {cap.owner ? "you" : cap.author}</span>
          <span className="dot-sep" />
          <span className="sh-when">{cap.visibility === "published" ? "shared " + cap.publishedAt : "draft"}</span>
        </div>

        <div className="sh-essence">
          <span className={"cc-status v-" + tone}><VerdictDot state={main} pulse={main === "fail"} /> {statusWord}</span>
          <span className="dot-sep" />
          {lead}
        </div>
      </div>

      <div className="sh-side" onClick={(e) => e.stopPropagation()}>
        <button className="sh-copy" onClick={() => onCopyLink(cap.id)} title="Copy capsule link"><Icon name="link" size={14} /></button>
        <Icon name="arrow-right" size={15} className="cc-chev" />
      </div>
    </div>
  );
}

// ── filters ─────────────────────────────────────────────────────
const TRIAGE_FILTERS = [
  { id: "attention", label: "Needs attention", hint: "incoherent + still failing on main" },
  { id: "all", label: "All" },
  { id: "failing", label: "Failing on main" },
  { id: "passing", label: "Passing on main" },
  { id: "inconclusive", label: "Inconclusive" },
  { id: "mine", label: "Mine" },
  { id: "drafts", label: "Drafts" },
];

// Share-oriented filters for the registry of shared capsules.
// Direction (who shared it) is its own segmented control; these are the
// "attached to an issue" facets — the way you filter the loop.
const SHARE_FILTERS = [
  { id: "all", label: "Everything" },
  { id: "issue", label: "Attached to an issue", hint: "mentioned in a GitHub issue" },
  { id: "link", label: "Shared via link", hint: "point-to-point, no issue" },
  { id: "open", label: "Open issues" },
  { id: "closed", label: "Resolved issues" },
];

// Direction relative to the viewer — combines what you shared + what was shared with you.
const DIR_SEGMENTS = [
  { id: "all", label: "All shared", icon: "share" },
  { id: "with-me", label: "Shared with me", icon: "down-line" },
  { id: "by-me", label: "Shared by me", icon: "up-line" },
];

function matchFilter(c, filter) {
  const sm = shareMode(c);
  switch (filter) {
    case "issue": return sm.kind === "issue";
    case "link": return sm.kind === "link";
    case "open": return sm.kind === "issue" && c.issue.state === "open";
    case "closed": return sm.kind === "issue" && c.issue.state === "closed";
    case "mine": return c.owner;
    default: return true;
  }
}

// ── page container (index ↔ detail) ─────────────────────────────
function CapsulesPage({ onSelectTrace }) {
  const [overrides, setOverrides] = React.useState({});
  const [audience, setAudience] = React.useState("console"); // console (maintainer) | watch (client)
  const [watchMode, setWatchMode] = React.useState("glance"); // glance | json
  const [view, setView] = React.useState("index");
  const [selId, setSelId] = React.useState(null);
  const [query, setQuery] = React.useState("");
  const [filter, setFilter] = React.useState("all");
  const [direction, setDirection] = React.useState("all"); // all | with-me | by-me
  const [busyId, setBusyId] = React.useState(null);
  const [toast, setToast] = React.useState(null);

  const caps = CAPSULES.map(c => effCap(c, overrides));
  const flash = (msg) => { setToast(msg); window.clearTimeout(flash._t); flash._t = window.setTimeout(() => setToast(null), 2400); };
  const update = (id, patch) => setOverrides(o => ({ ...o, [id]: { ...o[id], ...patch } }));

  const toggleVis = (id) => {
    const cur = caps.find(c => c.id === id);
    const next = cur.visibility === "published" ? "unpublished" : "published";
    update(id, { visibility: next });
    flash(next === "published" ? "Capsule published — discoverable & pullable" : "Capsule unpublished — link & pulls disabled");
  };
  const copyPull = (id) => {
    const c = caps.find(x => x.id === id);
    try { navigator.clipboard && navigator.clipboard.writeText(`ot capsule pull ${c.cid}`); } catch (e) {}
    flash(`Copied — ot capsule pull ${c.cid}`);
  };
  const copyLink = (cap) => {
    try { navigator.clipboard && navigator.clipboard.writeText(`https://hub.opentraces.dev/c/${cap.cid}`); } catch (e) {}
    flash("Capsule link copied to clipboard");
  };
  const recheck = (id) => {
    setBusyId(id);
    window.setTimeout(() => {
      setBusyId(null);
      update(id, { recheck: "just now" });
      const c = caps.find(x => x.id === id);
      const v = c.triage.head.onMain;
      flash(v === "pass" ? "Re-checked — passes on main now" : v === "inconclusive" ? "Re-check inconclusive — couldn't run in sandbox" : "Re-checked — still fails on main");
    }, 1500);
  };
  const post = (id) => { update(id, { posted: true }); const c = caps.find(x => x.id === id); flash(`Verdict posted to #${c.issue.number}`); };
  const toggleIssue = (id) => {
    const c = caps.find(x => x.id === id);
    const next = c.issue.state === "closed" ? "open" : "closed";
    update(id, { issueState: next });
    flash(next === "closed" ? `Closed #${c.issue.number} — replay passes` : `Reopened #${c.issue.number} — replay fails on main`);
  };

  const runReplay = (id) => {
    const cap = caps.find(c => c.id === id);
    const pendingId = "rp-" + Date.now();
    const base = cap.replays;
    update(id, { replays: [...base, { actor: "maintainer", agent: cap.agent, when: "now", sha: "main", outcome: "running", dur: "…", note: "Re-posing intent against current main", _id: pendingId }] });
    window.setTimeout(() => {
      setOverrides(o => {
        const cur = (o[id]?.replays) || cap.replays;
        const target = cap.triage.head.onMain;
        const resolved = cur.map(r => r._id === pendingId
          ? { ...r, outcome: target === "pass" ? "pass" : target === "inconclusive" ? "inconclusive" : "fail", dur: "12s", when: "just now", note: target === "pass" ? "Intent satisfied against main" : target === "inconclusive" ? "Could not run — missing creds in sandbox" : "Failure reproduced on main" }
          : r);
        return { ...o, [id]: { ...o[id], replays: resolved } };
      });
      flash("Replay complete");
    }, 1700);
  };

  const open = (id) => { setSelId(id); setView("detail"); };
  const back = () => setView("index");

  // client watchlist actions
  const resume = (id) => { const c = caps.find(x => x.id === id); const w = deriveWatch(c); flash(`Re-posing “${w.goal}” against new HEAD…`); };
  const mute = (id) => flash("Muted — you'll still get the push when it unblocks");

  if (view === "detail" && selId) {
    const cap = caps.find(c => c.id === selId);
    const w = deriveWatch(cap);
    if (audience === "watch" && w) {
      return (
        <>
          <ClientCapsule
            cap={cap} w={w}
            onBack={back}
            onResume={() => resume(cap.id)}
            onCopyLink={() => copyLink(cap)}
            onOpenUrl={() => flash("Opening replay — re-poses the sealed intent in the viewer")}
            onOpenTrace={onSelectTrace}
          />
          {toast && <div className="cap-toast"><Icon name="check" size={13} />{toast}</div>}
        </>
      );
    }
    return (
      <>
        <CapsuleDetail
          cap={cap} isOwner={cap.owner}
          onBack={back}
          onToggleVis={() => toggleVis(cap.id)}
          onCopyLink={() => copyLink(cap)}
          onCopyPull={() => copyPull(cap.id)}
          onShare={() => copyLink(cap)}
          onRunReplay={() => runReplay(cap.id)}
          onRecheck={() => recheck(cap.id)}
          onPost={() => post(cap.id)}
          onToggleIssue={() => toggleIssue(cap.id)}
          busy={busyId === cap.id}
          onOpenTrace={onSelectTrace}
        />
        {toast && <div className="cap-toast"><Icon name="check" size={13} />{toast}</div>}
      </>
    );
  }

  let list = caps.filter(c => {
    if (!matchFilter(c, filter)) return false;
    if (direction === "by-me" && !c.owner) return false;
    if (direction === "with-me" && c.owner) return false;
    if (query) {
      const q = query.toLowerCase();
      const hay = (c.title + " " + c.cid + " " + c.repo + " " + c.fail.summary + " " + c.agent.name + " " + (c.triage.deps?.pkg || "")).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  const withIssue = caps.filter(c => shareMode(c).kind === "issue").length;
  const linkOnly = caps.filter(c => shareMode(c).kind === "link").length;
  const totalViews = caps.reduce((a, c) => a + c.stats.views, 0);
  const byMe = caps.filter(c => c.owner).length;
  const withMe = caps.length - byMe;
  const dirCount = (id) => id === "by-me" ? byMe : id === "with-me" ? withMe : caps.length;

  const watchItems = caps.map(deriveWatch).filter(Boolean);
  const watchGreen = watchItems.filter(w => w.state === "resolved").length;
  const watchRed = watchItems.length - watchGreen;
  const isWatch = audience === "watch";

  return (
    <div className="landing landing-page">
      <PageHero
        kicker={isWatch ? "Agent-to-agent issue reporting · client view" : "Shared, replayable traces"}
        title="Capsules"
        subtitle={isWatch
          ? "The other side of the loop. You filed these blockers; this is the one-question view — am I unblocked, and what's the single action to resume?"
          : "Everything you’ve shared and everything shared with you, in one place. Each capsule is a sealed, replayable trace — handed off by link or attached to a GitHub issue when it’s mentioned there."}
        scope={isWatch
          ? `${watchGreen} ready to resume · ${watchRed} still blocked`
          : `${withMe} shared with you · ${byMe} shared by you · ${withIssue} attached to issues · ${totalViews.toLocaleString()} views`}
        actions={
          <div className="ph-action-group">
            <ToolBtn icon="link" label="Share a capsule" />
            <ToolBtn icon="capsule" label="New capsule" primary />
          </div>
        }
      />

      <div className="aud-toggle" role="tablist" aria-label="Audience">
        <button className={"aud-tab" + (!isWatch ? " on" : "")} role="tab" aria-selected={!isWatch} onClick={() => setAudience("console")}>
          <Icon name="share" size={14} />
          <span className="aud-l"><span className="aud-name">Shared capsules</span><span className="aud-who">browse &amp; open by link</span></span>
        </button>
        <button className={"aud-tab" + (isWatch ? " on" : "")} role="tab" aria-selected={isWatch} onClick={() => setAudience("watch")}>
          <Icon name="bell" size={14} />
          <span className="aud-l"><span className="aud-name">Watchlist</span><span className="aud-who">client · waiting to resume</span></span>
        </button>
      </div>

      {isWatch ? (
        <WatchlistView
          items={watchItems}
          viewMode={watchMode}
          setViewMode={setWatchMode}
          onResume={resume}
          onMute={mute}
          onOpen={open}
          onGotoConsole={() => setAudience("console")}
        />
      ) : (
      <>
      <div className="cap-dir-bar" role="tablist" aria-label="Share direction">
        {DIR_SEGMENTS.map(s => (
          <button key={s.id} className={"cap-dir" + (direction === s.id ? " on" : "")} role="tab" aria-selected={direction === s.id} onClick={() => setDirection(s.id)}>
            <Icon name={s.icon} size={13} />
            <span>{s.label}</span>
            <span className="cap-dir-n">{dirCount(s.id)}</span>
          </button>
        ))}
      </div>

      <div className="cap-toolbar">
        <div className="cap-toolbar-top">
          <label className="cap-search">
            <Icon name="search" size={14} className="cs-ico" />
            <input type="text" placeholder="Search shared capsules — title, repo, issue, agent…" value={query} onChange={(e) => setQuery(e.target.value)} spellCheck={false} />
            {query && <button className="cs-clear" onClick={() => setQuery("")} aria-label="Clear"><Icon name="x" size={12} /></button>}
          </label>
        </div>
        <div className="cap-toolbar-row">
          <div className="cap-filter-chips">
            {SHARE_FILTERS.map(f => (
              <button key={f.id} className="cap-chip" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)} title={f.hint || ""}>{f.label}</button>
            ))}
          </div>
        </div>
      </div>

      <CapsuleTable
        caps={list}
        busyId={busyId}
        onOpen={open}
        onCopyLink={(id) => copyLink(caps.find(x => x.id === id))}
        onCopyPull={copyPull}
        onRecheck={recheck}
        onPost={post}
        onToggleIssue={toggleIssue}
        onOpenTrace={onSelectTrace}
      />
      </>
      )}

      {toast && <div className="cap-toast"><Icon name="check" size={13} />{toast}</div>}
    </div>
  );
}

Object.assign(window, {
  CapsulesPage, shareMode, latestReplay, replayStatus, effCap, LIFE_TONE, VERDICT_TONE, VERDICT_LABEL,
  VerdictDot, CapsuleSeal, WorkflowChip, LiveVerdict, SurvivalTag, DepMatrix, IssueBinding, MicroTraj, TrajMap, downsampleFP, resampleFP, ScrubMini, ImpactTag,
});
