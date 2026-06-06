// Trace Capsules — shared registry as a TABLE (Drive / Dropbox "Shared" lineage).
//
// The list of shared capsules is a combination of traces *you shared* and traces
// *shared with you*. Each row reads like a file row in a shared-drive: a name, who
// shared it & which direction, when, and reach — but the "thumbnail" is the thing
// that makes a capsule a capsule: a live preview of its action trajectory (the
// lines), plus its verdict against main and the GitHub issue it's attached to.
//
// Click a row to expand it in place into a tabbed reader:
//   Trajectory · Context · Git anchor · Issue
// — the trail, the ctx_tree, the git snapshot, and the issue binding.

// Parse a relative "publishedAt" / "when" string into minutes-ago (for sorting).
function ageMinutes(cap) {
  let s = cap.publishedAt;
  if (!s || s === "draft") s = (cap.replays[0] && cap.replays[0].when) || "1h ago";
  if (/just now|^now$/i.test(s)) return 0;
  const m = s.match(/(\d+)\s*(m|h|d)/i);
  if (!m) return 99999;
  const n = +m[1];
  return m[2] === "m" ? n : m[2] === "h" ? n * 60 : n * 1440;
}

// Direction of a share, relative to the viewer ("you").
function shareDir(cap) { return cap.owner ? "out" : "in"; }

const PHASE_KEYS = [
  { k: "user",  l: "user" },  { k: "plan",  l: "plan" },  { k: "think", l: "think" },
  { k: "read",  l: "read" },  { k: "exec",  l: "exec" },  { k: "write", l: "write" },
];

function phaseCounts(fp) {
  const c = { user: 0, plan: 0, think: 0, read: 0, exec: 0, write: 0 };
  fp.forEach(x => { if (c[x] != null) c[x]++; });
  return c;
}

// ── verdict (compact, table cell) ───────────────────────────────
function VerdictCell({ cap }) {
  const main = cap.triage.head.onMain;
  const tone = VERDICT_TONE[main];
  return (
    <span className={"ctv v-" + tone}>
      <VerdictDot state={main} pulse={main === "fail"} />
      <span className="ctv-w">{VERDICT_LABEL[main]}</span>
      <span className="ctv-at mono">main</span>
    </span>
  );
}

// ── share target: issue chip or link chip ───────────────────────
function ShareTargetCell({ cap }) {
  const sm = shareMode(cap);
  if (sm.kind === "issue") {
    const closed = sm.state === "closed";
    return (
      <span className={"ct-issue " + (closed ? "closed" : "open")} title={cap.repo + " #" + cap.issue.number}>
        <Icon name="issue" size={12} />
        <span className="ct-issue-repo mono">{cap.repo.split("/")[1]}</span>
        <span className="ct-issue-num mono">#{cap.issue.number}</span>
        <span className="ct-issue-state">{sm.state}</span>
      </span>
    );
  }
  return (
    <span className="ct-link">
      <Icon name="link" size={12} />
      <span>via link</span>
      {sm.private && <span className="ct-priv"><Icon name="lock" size={9} /> private</span>}
    </span>
  );
}

// ── who shared it + which direction ─────────────────────────────
function SharedByCell({ cap }) {
  const out = cap.owner;
  return (
    <span className="ct-by">
      <span className={"ct-dir " + (out ? "out" : "in")} title={out ? "You shared this" : "Shared with you"}>
        <Icon name={out ? "up-line" : "down-line"} size={12} />
      </span>
      <AgentGlyph agent={cap.agent} size={18} />
      <span className="ct-by-l">
        <span className="ct-by-name">{out ? "You" : cap.author}</span>
        <span className="ct-by-sub">{out ? "shared by you" : "shared with you"}</span>
      </span>
    </span>
  );
}

// ── trajectory preview ("thumbnail") ────────────────────────────
function TrajThumb({ cap }) {
  return (
    <span className="ct-traj" title={cap.manifest.trajectory.steps + " steps · fails at step " + cap.fail.step}>
      <TrajMap cap={cap} height={24} gap={1.5} max={38} className="ct-trajmap" />
      <span className="ct-traj-n mono">{cap.manifest.trajectory.steps}</span>
    </span>
  );
}

// ── expansion tabs ──────────────────────────────────────────────
// "Trajectory" is a mini version of the trace viewer: a compact meta strip,
// the rounded-pill minimap band, run-health lanes, and the failing step.
function ExpTrajectory({ cap, onOpenTrace }) {
  const counts = phaseCounts(cap.fingerprint);
  const main = cap.triage.head.onMain;
  const statusWord = cap.visibility === "published"
    ? (main === "pass" ? "Passes @main" : main === "inconclusive" ? "Inconclusive" : "Fails @main")
    : "Draft";
  const lifeLabel = { reported: "Reported", triaged: "Triaged", fixing: "Fixing", resolved: "Resolved", unblocked: "Unblocked" }[cap.lifecycle] || cap.lifecycle;
  return (
    <div className="cx-pane mini-viewer">
      {/* mini header — intent + run meta (title already shows on the row above) */}
      <div className="mv-head">
        <div className="mv-head-l">
          <span className="mv-intent-l">Failing intent</span>
          <p className="mv-intent">{cap.intent}</p>
        </div>
        <div className="mv-meta">
          <span className="mv-m"><span className="mv-mk">trace</span><span className="mono mv-mv">{cap.cid}</span></span>
          <span className="mv-m"><span className="mv-mk">agent</span><span className="mv-mv"><AgentGlyph agent={cap.agent} size={13} /> {cap.agent.name}</span></span>
          <span className="mv-m"><span className="mv-mk">model</span><span className="mv-mv">{cap.agent.model}</span></span>
          <span className="mv-m"><span className="mv-mk">status</span><span className={"mv-mv v-" + VERDICT_TONE[main]}><VerdictDot state={main} /> {statusWord}</span></span>
        </div>
      </div>

      {/* the rounded-pill minimap band — thin lines, like the trace-viewer minimap */}
      <div className="mv-minimap-card">
        <TrajMap cap={cap} height={34} gap={2.5} fill={Math.max(cap.manifest.trajectory.steps, 110)} className="mv-minimap" />
      </div>
      <div className="cx-band-legend">
        <span className="cx-bl-start">session start</span>
        <span className="cx-bl-mid mono">{lifeLabel}</span>
        <span className="cx-bl-fail"><Icon name="alert" size={10} /> fails at step {cap.fail.step}</span>
      </div>

      {/* run-health lanes — mirrors the Trail's action columns */}
      <div className="mv-lanes">
        {PHASE_KEYS.map(p => (
          <span key={p.k} className={"mv-lane ph-" + p.k}>
            <span className="mv-lane-dot" />
            <span className="mv-lane-n mono">{counts[p.k]}</span>
            <span className="mv-lane-l">{p.l}</span>
          </span>
        ))}
      </div>

      {/* failing step — the focused trace_step */}
      <div className="cx-fail">
        <div className="cx-fail-head"><Icon name="alert" size={11} /> trace_step · step {cap.fail.step} · <span className={"cx-lane lane-" + cap.fail.lane}>{cap.fail.lane}</span></div>
        <pre className="cx-fail-pre">{cap.fail.error}</pre>
      </div>

      <button className="cx-viewer" onClick={() => onOpenTrace && onOpenTrace(RECENT_TRACES[0].id)}>
        <Icon name="trail" size={13} /> Open the full Trail in the viewer <Icon name="arrow-right" size={12} />
      </button>
    </div>
  );
}

function ExpContext({ cap }) {
  const m = cap.manifest;
  const chips = [
    { icon: "activity", k: "action_trajectory", v: m.trajectory.steps + " steps" },
    { icon: "node", k: "ctx_tree", v: m.ctx.nodes + " nodes · " + m.ctx.tokens + " tok" },
    { icon: "alert", k: "trace_step", v: "step " + cap.fail.step },
    { icon: "box", k: "git_snapshot", v: cap.git.sha },
  ];
  return (
    <div className="cx-pane">
      <div className="cx-manifest">
        {chips.map(c => (
          <span className="cx-mchip" key={c.k}>
            <Icon name={c.icon} size={12} />
            <span className="cx-mchip-k mono">{c.k}</span>
            <span className="cx-mchip-v">{c.v}</span>
          </span>
        ))}
      </div>
      <div className="cx-ctx-head"><Icon name="node" size={12} /> ctx_tree <span className="cx-ctx-meta mono">{m.ctx.nodes} nodes · {m.ctx.tokens} tokens · {m.ctx.bytes}</span></div>
      <CtxTreeView nodes={cap.ctx} />
    </div>
  );
}

function ExpGit({ cap, onCopyLink }) {
  return (
    <div className="cx-pane">
      <div className="cx-git">
        <div className="cx-git-rows">
          <div className="cx-git-row"><span className="cx-git-k">repo</span><RepoLabel repo={cap.repo} /></div>
          <div className="cx-git-row"><span className="cx-git-k">branch</span><span className="mono cx-git-v">{cap.git.branch}</span></div>
          <div className="cx-git-row"><span className="cx-git-k">commit</span><span className="mono cx-git-v hl">{cap.git.sha}</span></div>
          <div className="cx-git-row"><span className="cx-git-k">base</span><span className="mono cx-git-v">{cap.git.base}</span></div>
          <div className="cx-git-row"><span className="cx-git-k">tree</span><span className={"cx-git-tree " + (cap.git.dirty ? "dirty" : "clean")}>{cap.git.dirty ? "dirty · " + cap.manifest.git.files + " files" : "clean"}</span></div>
        </div>
        <div className="cx-git-side">
          <SurvivalTag cap={cap} />
          <div className="cx-git-cmd mono"><span className="cx-prompt">$</span> git checkout {cap.git.sha}<button className="cx-cmd-copy" onClick={onCopyLink} aria-label="Copy"><Icon name="copy" size={12} /></button></div>
        </div>
      </div>
    </div>
  );
}

function ExpIssue({ cap, busy, onRecheck, onPost, onToggleIssue }) {
  const t = cap.triage;
  const main = t.head.onMain;
  const hasIssue = cap.issue && cap.issue.number != null;
  const closed = hasIssue && cap.issue.state === "closed";
  if (!hasIssue) {
    const sm = shareMode(cap);
    return (
      <div className="cx-pane">
        <div className="cx-noissue">
          <Icon name="link" size={15} />
          <div>
            <div className="cx-noissue-h">Shared point-to-point{sm.private ? " · private link" : ""}</div>
            <div className="cx-noissue-s">This capsule isn’t attached to a GitHub issue. Mention its link in an issue to bind the verdict to a thread.</div>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="cx-pane">
      <div className={"cx-loop" + (t.issueDrift ? " drift" : "")}>
        <div className="cx-loop-side">
          <span className="cx-loop-l">capsule verdict</span>
          <span className={"cx-loop-v v-" + VERDICT_TONE[main]}><VerdictDot state={main} /> {VERDICT_LABEL[main]} @main</span>
        </div>
        <Icon name="link" size={14} className="cx-loop-link" />
        <div className="cx-loop-side">
          <span className="cx-loop-l">{cap.repo}</span>
          <span className={"cx-loop-v issue-" + (closed ? "closed" : "open")}><Icon name="issue" size={12} /> #{cap.issue.number} {closed ? "closed" : "open"}</span>
        </div>
      </div>
      {t.issueDrift && (
        <div className="cx-drift"><Icon name="alert" size={11} /> {main === "pass" ? "Passes on main but the issue is still open." : "Issue is closed but the replay fails on main."}</div>
      )}
      <div className="cx-post">
        {t.issue && t.issue.posted
          ? <span className={"cx-post-s " + (t.issue.stale ? "stale" : "ok")}><Icon name="check" size={12} /> verdict posted to thread{t.issue.stale ? " · now stale" : ""}</span>
          : <span className="cx-post-s none"><Icon name="dot" size={8} /> verdict not posted yet</span>}
      </div>
      <div className="cx-issue-actions">
        <button className="cap-btn sm" onClick={onRecheck} disabled={busy}><Icon name="replay" size={12} className={busy ? "spin" : ""} /> {busy ? "Re-checking…" : "Re-check on main"}</button>
        <button className="cap-btn sm" onClick={onPost}><Icon name="send" size={12} /> Post verdict</button>
        <button className="cap-btn sm" onClick={onToggleIssue}><Icon name="issue" size={12} /> {closed ? "Reopen" : "Close"} #{cap.issue.number}</button>
      </div>
    </div>
  );
}

const EXP_TABS = [
  { id: "trajectory", label: "Trajectory", icon: "activity" },
  { id: "context", label: "Context", icon: "node" },
  { id: "git", label: "Git anchor", icon: "box" },
  { id: "issue", label: "Issue", icon: "issue" },
];

function CapsuleExpansion({ cap, busy, onOpen, onCopyLink, onCopyPull, onRecheck, onPost, onToggleIssue, onOpenTrace }) {
  const [tab, setTab] = React.useState("trajectory");
  return (
    <div className="ct-exp" onClick={(e) => e.stopPropagation()}>
      <div className="cx-tabs" role="tablist">
        {EXP_TABS.map(t => (
          <button key={t.id} className={"cx-tab" + (tab === t.id ? " on" : "")} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}>
            <Icon name={t.icon} size={12} /> {t.label}
            {t.id === "issue" && cap.triage.issueDrift && <span className="cx-tab-dot" />}
          </button>
        ))}
        <div className="cx-tabs-spacer" />
        <button className="cx-open" onClick={() => onOpen(cap.id)}>Open full capsule <Icon name="arrow-right" size={13} /></button>
      </div>
      <div className="cx-body">
        {tab === "trajectory" && <ExpTrajectory cap={cap} onOpenTrace={onOpenTrace} />}
        {tab === "context" && <ExpContext cap={cap} />}
        {tab === "git" && <ExpGit cap={cap} onCopyLink={onCopyLink} />}
        {tab === "issue" && <ExpIssue cap={cap} busy={busy} onRecheck={onRecheck} onPost={onPost} onToggleIssue={onToggleIssue} />}
      </div>
      <div className="cx-foot">
        <span className="cx-foot-cid mono">{cap.cid}</span>
        <span className="cx-foot-actions">
          <button className="cap-btn sm" onClick={() => onCopyLink(cap.id)}><Icon name="link" size={12} /> Copy link</button>
          <button className="cap-btn sm" onClick={() => onCopyPull(cap.id)}><Icon name="box" size={12} /> Copy pull</button>
        </span>
      </div>
    </div>
  );
}

// ── sortable header ─────────────────────────────────────────────
function HeadCell({ id, label, sortKey, sortDir, onSort, sortable, className }) {
  const active = sortable && sortKey === id;
  return (
    <button
      className={"cth" + (className ? " " + className : "") + (sortable ? " sortable" : "") + (active ? " active" : "")}
      onClick={sortable ? () => onSort(id) : undefined}
      type="button"
    >
      <span>{label}</span>
      {sortable && <Icon name={active && sortDir === "asc" ? "up-line" : "down-line"} size={12} className="cth-arrow" />}
    </button>
  );
}

// ── the table ───────────────────────────────────────────────────
function CapsuleTable({ caps, busyId, onOpen, onCopyLink, onCopyPull, onRecheck, onPost, onToggleIssue, onOpenTrace }) {
  const [sortKey, setSortKey] = React.useState("shared");
  const [sortDir, setSortDir] = React.useState("desc");
  const [expandedId, setExpandedId] = React.useState(null);

  const onSort = (k) => {
    if (k === sortKey) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir(k === "capsule" ? "asc" : "desc"); }
  };

  const sorted = React.useMemo(() => {
    const arr = [...caps];
    const dir = sortDir === "asc" ? 1 : -1;
    arr.sort((a, b) => {
      let av, bv;
      if (sortKey === "capsule") { av = a.title.toLowerCase(); bv = b.title.toLowerCase(); return av < bv ? -dir : av > bv ? dir : 0; }
      if (sortKey === "views") { av = a.stats.views; bv = b.stats.views; return (av - bv) * dir; }
      // shared (recency): smaller ageMinutes = more recent
      av = ageMinutes(a); bv = ageMinutes(b);
      return (bv - av) * dir; // desc => most recent first
    });
    return arr;
  }, [caps, sortKey, sortDir]);

  if (caps.length === 0) {
    return (
      <div className="cap-empty">
        <Icon name="capsule" size={26} />
        <div className="cap-empty-h">No shared capsules here</div>
        <div className="cap-empty-s">Try a different filter or clear the search.</div>
      </div>
    );
  }

  return (
    <div className="ct-scroll">
      <div className="ct-table" role="table">
        <div className="ct-head-row" role="row">
          <span className="cth cth-caret" aria-hidden="true" />
          <HeadCell id="capsule" label="Capsule" sortKey={sortKey} sortDir={sortDir} onSort={onSort} sortable className="cth-name" />
          <span className="cth cth-traj">Trajectory</span>
          <span className="cth cth-verdict">Verdict</span>
          <span className="cth cth-target">Attached to</span>
          <HeadCell id="by" label="Shared by" sortKey={sortKey} sortDir={sortDir} onSort={onSort} className="cth-by" />
          <HeadCell id="shared" label="Shared" sortKey={sortKey} sortDir={sortDir} onSort={onSort} sortable className="cth-when" />
          <HeadCell id="views" label="Views" sortKey={sortKey} sortDir={sortDir} onSort={onSort} sortable className="cth-views" />
          <span className="cth cth-act" aria-hidden="true" />
        </div>

        <div className="ct-body" role="rowgroup">
          {sorted.map(cap => {
            const open = expandedId === cap.id;
            const main = cap.triage.head.onMain;
            return (
              <div className={"ct-rowgroup" + (open ? " open" : "")} key={cap.id}>
                <div
                  className={"ct-row on-" + VERDICT_TONE[main] + (cap.triage.incoherent ? " incoherent" : "") + (cap.visibility !== "published" ? " draft" : "")}
                  role="row" tabIndex={0}
                  onClick={() => setExpandedId(open ? null : cap.id)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setExpandedId(open ? null : cap.id); } }}
                >
                  <span className="ct-caret"><Icon name={open ? "chevron-down" : "chevron-right"} size={14} /></span>

                  <div className="ct-name">
                    <CapsuleSeal lane={cap.fail.lane} size={30} sealed={cap.scrub.state === "clean"} />
                    <div className="ct-name-l">
                      <div className="ct-titlerow">
                        <span className="ct-title">{cap.title}</span>
                        {cap.visibility !== "published" && <span className="ct-draft-tag">draft</span>}
                      </div>
                      <div className="ct-sub">
                        <span className="ct-cid mono">{cap.cid}</span>
                        <span className="ct-sub-sep" />
                        <RepoLabel repo={cap.repo} />
                      </div>
                    </div>
                  </div>

                  <div className="ct-cell ct-cell-traj"><TrajThumb cap={cap} /></div>
                  <div className="ct-cell ct-cell-verdict"><VerdictCell cap={cap} /></div>
                  <div className="ct-cell ct-cell-target"><ShareTargetCell cap={cap} /></div>
                  <div className="ct-cell ct-cell-by"><SharedByCell cap={cap} /></div>
                  <div className="ct-cell ct-cell-when">
                    <span className="ct-when">{cap.visibility === "published" ? cap.publishedAt : "Draft"}</span>
                  </div>
                  <div className="ct-cell ct-cell-views">
                    <span className="ct-views mono"><Icon name="eye" size={12} />{cap.stats.views.toLocaleString()}</span>
                  </div>
                  <div className="ct-cell ct-cell-act" onClick={(e) => e.stopPropagation()}>
                    <button className="ct-iconbtn" title="Copy capsule link" onClick={() => onCopyLink(cap.id)}><Icon name="link" size={14} /></button>
                  </div>
                </div>

                {open && (
                  <CapsuleExpansion
                    cap={cap}
                    busy={busyId === cap.id}
                    onOpen={onOpen}
                    onCopyLink={onCopyLink}
                    onCopyPull={onCopyPull}
                    onRecheck={() => onRecheck(cap.id)}
                    onPost={() => onPost(cap.id)}
                    onToggleIssue={() => onToggleIssue(cap.id)}
                    onOpenTrace={onOpenTrace}
                  />
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { CapsuleTable, ageMinutes, shareDir });
