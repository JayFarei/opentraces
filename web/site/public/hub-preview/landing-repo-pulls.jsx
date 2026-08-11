// RepoPulls — pull requests for a repository. List + detail view.

// Inline SVG icons for icons not in the shared icon set.
function FlaskIcon({ size = 12 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 2v7.31L6 16h12l-4-6.69V2"/><path d="M8.5 2h7"/><path d="M7 16h10"/>
    </svg>
  );
}
function InfoIcon({ size = 12 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="8.5"/><line x1="12" y1="11" x2="12" y2="16"/>
    </svg>
  );
}
function ExternalIcon({ size = 12 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
    </svg>
  );
}

// Global helpers used by inline onclick attributes inside the embedded PR HTML.
window.copyCmd = function (el, cmd) {
  navigator.clipboard.writeText(cmd).then(() => {
    el.classList.add("copied");
    setTimeout(() => el.classList.remove("copied"), 1500);
  });
};
window.copyContextBlob = function (btn) {
  const ta = btn.closest(".pr-actions")?.querySelector("textarea") || document.getElementById("__ctx");
  if (!ta) return;
  navigator.clipboard.writeText(ta.value).then(() => {
    btn.classList.add("copied");
    const label = btn.querySelector("span:not(.kbd-hint)");
    const orig = label ? label.textContent : null;
    if (label) label.textContent = "Copied — paste in your reviewer";
    setTimeout(() => {
      btn.classList.remove("copied");
      if (label && orig) label.textContent = orig;
    }, 2000);
  });
};
window.toggleTests = function (btn) {
  const card = btn.closest(".intent-card");
  if (!card) return;
  const det = card.querySelector(".tests-detail");
  if (det) det.classList.toggle("open");
};
window.toggleTurns = function (btn) {
  const card = btn.closest(".intent-card");
  if (!card) return;
  const det = card.querySelector("details.turns");
  if (det) det.open = !det.open;
};

// ── Slice expander: surface the trail/conversation of THIS commit's slice
// inline, built at runtime from the card's own data (bar segments, turns)
// so every intent card gets it for free.
window.toggleSliceView = function (btn, mode) {
  const card = btn.closest(".intent-card");
  if (!card) return;
  const bar = card.querySelector(".slice-expand");
  const pane = card.querySelector(".slice-detail-pane");
  if (!pane) return;
  const wasOpen = !pane.hidden && pane.dataset.mode === mode;
  bar.querySelectorAll(".se-btn").forEach((b) => b.classList.remove("on"));
  if (wasOpen) { pane.hidden = true; pane.dataset.mode = ""; return; }
  btn.classList.add("on");
  pane.dataset.mode = mode;
  pane.hidden = false;

  const head = card.querySelector(".widget.trajectory .w-head");
  const sliceRef = head ? (head.textContent.match(/SLICE T\d+ · STEPS [\d–]+ \/ \d+/) || [""])[0] : "";

  if (mode === "trail") {
    const segs = [...card.querySelectorAll(".bar.session .lit .seg")];
    const rows = segs.map((s) => {
      const tip = s.getAttribute("data-tip") || "";
      const kind = [...s.classList].find((c) => c !== "seg") || "";
      const m = tip.match(/step (\d+) · (.+)/) || [null, "", tip];
      return `<div class="sd-row"><span class="sd-num">${m[1] || ""}</span><span class="sd-dot ${kind}"></span><span class="sd-kind">${m[2] || ""}</span></div>`;
    }).join("");
    pane.innerHTML = `<div class="sd-head">${sliceRef.toLowerCase()} — step-by-step</div><div class="sd-grid">${rows}</div>`;
  } else {
    const turns = [...card.querySelectorAll(".turns-body ol li")].map((li) => li.textContent.trim());
    const subject = (card.querySelector(".intent-tagbar .subject") || {}).textContent || "";
    const sha = (card.querySelector(".intent-tagbar .sha") || {}).textContent || "";
    const turnHtml = turns.map((t) => `<div class="sd-msg user"><span class="sd-role">user</span><span class="sd-text">${t}</span></div>`).join("");
    pane.innerHTML = `<div class="sd-head">${sliceRef.toLowerCase()} — conversation</div>${turnHtml}<div class="sd-msg agent"><span class="sd-role">agent</span><span class="sd-text">Landed <b>${subject}</b> <span class="mono">${sha}</span> — full exchange in the session viewer.</span></div>`;
  }
};
window.openFullSession = function (btn) {
  const bar = btn.closest(".slice-expand");
  const sid = bar ? bar.getAttribute("data-session") : null;
  if (window.__otOpenTrace) window.__otOpenTrace(sid || (window.RECENT_TRACES || [{}])[0].id);
};

// Pull request list per repo
const REPO_PULLS = {
  "jayfarei/opentraces": [
    {
      id: "pr-128",
      number: 128,
      title: "Pulse — weekly project momentum CLI",
      summary: "Adds a `pulse` CLI tool for tracking project momentum. Includes `pulse summary` and `pulse streak` commands.",
      branch: "demo-pulse",
      base: "main",
      author: "Jayfarei",
      status: "open",
      verdict: "attention",
      verdictLabel: "Review with care",
      commits: 5,
      traced: 4,
      additions: 223,
      deletions: 18,
      filesChanged: 8,
      intents: { aligned: 4, total: 5 },
      tests: 4,
      stepCount: 60,
      wallTime: "4m 50s",
      cost: "$1.31",
      agents: [{ name: "claude-code" }],
      updated: "12 min ago",
      detail: "pulse", // key for which detail to load
    },
    {
      id: "pr-127",
      number: 127,
      title: "Fix retry loop in fetcher when server returns 429",
      summary: "Backs off exponentially and respects Retry-After. Adds tests for the rate-limit path.",
      branch: "fix/fetcher-429",
      base: "main",
      author: "Jayfarei",
      status: "open",
      verdict: "ready",
      verdictLabel: "Aligned",
      commits: 3,
      traced: 3,
      additions: 86,
      deletions: 24,
      filesChanged: 4,
      intents: { aligned: 2, total: 2 },
      tests: 3,
      stepCount: 38,
      wallTime: "2m 11s",
      cost: "$0.74",
      agents: [{ name: "claude-code" }],
      updated: "2h ago",
    },
    {
      id: "pr-126",
      number: 126,
      title: "Trace upload stalls on large ctx_tree payloads",
      summary: "Chunks the Hugging Face Hub upload and streams ctx_tree spans. Still investigating a hang on traces over 50MB.",
      branch: "fix/hub-upload-stall",
      base: "main",
      author: "Jayfarei",
      status: "open",
      verdict: "blocked",
      verdictLabel: "Failing checks",
      commits: 7,
      traced: 5,
      additions: 312,
      deletions: 41,
      filesChanged: 11,
      intents: { aligned: 3, total: 5 },
      tests: 1,
      stepCount: 142,
      wallTime: "12m 04s",
      cost: "$3.48",
      agents: [{ name: "claude-code" }, { name: "codex" }],
      updated: "yesterday",
    },
    {
      id: "pr-125",
      number: 125,
      title: "Refactor trace minimap — vertical orientation",
      summary: "Vertical density bars + scrubber. Closes #112.",
      branch: "feat/minimap-vertical",
      base: "main",
      author: "Jayfarei",
      status: "merged",
      verdict: "ready",
      verdictLabel: "Merged",
      commits: 4,
      traced: 4,
      additions: 198,
      deletions: 84,
      filesChanged: 6,
      intents: { aligned: 3, total: 3 },
      tests: 2,
      stepCount: 52,
      wallTime: "3m 22s",
      cost: "$0.92",
      agents: [{ name: "claude-code" }],
      updated: "3 days ago",
      merged: true,
    },
  ],
};

const PULL_STATUS_META = {
  open:   { label: "Open",   color: "var(--c-git)" },
  merged: { label: "Merged", color: "var(--c-push)" },
  closed: { label: "Closed", color: "var(--fg-mute)" },
  draft:  { label: "Draft",  color: "var(--fg-mute)" },
};

function PullStatusPill({ status }) {
  const m = PULL_STATUS_META[status] || PULL_STATUS_META.open;
  return (
    <span className="pull-status-pill" style={{ "--pull-color": m.color }}>
      <span className="dot" />
      <span>{m.label}</span>
    </span>
  );
}

function VerdictDot({ kind }) {
  const map = {
    ready:     { color: "var(--c-git)",   label: "aligned" },
    attention: { color: "var(--c-user)",  label: "review" },
    blocked:   { color: "var(--c-error)", label: "blocked" },
  };
  const v = map[kind] || map.ready;
  return (
    <span className="pull-verdict" style={{ "--v-color": v.color }}>
      <span className="pulse-ring" />
      <span className="ring-dot" />
    </span>
  );
}

function PullCard({ pr, onOpen }) {
  const clickable = !!pr.detail;
  return (
    <button
      className="pull-card"
      data-clickable={clickable}
      onClick={() => clickable && onOpen(pr.id)}
      disabled={!clickable}
    >
      <VerdictDot kind={pr.verdict} />
      <div className="pc-main">
        <div className="pc-title-row">
          <span className="pc-num mono">#{pr.number}</span>
          <span className="pc-title">{pr.title}</span>
          <PullStatusPill status={pr.status} />
        </div>
        <div className="pc-summary">{pr.summary}</div>
        <div className="pc-meta">
          <span className="pc-branch mono">
            <Icon name="git-branch" size={11} />
            <span>{pr.branch}</span>
            <span className="arrow">→</span>
            <span>{pr.base}</span>
          </span>
          <span className="dot-sep" />
          <span className="pc-pill">
            <span className="mono">{pr.commits}</span>
            <span>commits</span>
            {pr.traced < pr.commits && (
              <span className="pc-traced-warn" title={`${pr.commits - pr.traced} untraced`}>
                · {pr.commits - pr.traced} untraced
              </span>
            )}
          </span>
          <span className="pc-pill">
            <span className="mono ins">+{pr.additions}</span>
            <span className="mono del">−{pr.deletions}</span>
            <span>· {pr.filesChanged} files</span>
          </span>
          <span className="pc-pill">
            <span className="mono">{pr.intents.aligned}/{pr.intents.total}</span>
            <span>intents</span>
          </span>
          {pr.tests > 0 && (
            <span className="pc-pill tests">
              <FlaskIcon size={10} />
              <span className="mono">{pr.tests}</span>
              <span>tests</span>
            </span>
          )}
          <span className="dot-sep" />
          <span className="pc-time">{pr.updated}</span>
        </div>
      </div>
      <div className="pc-side">
        <div className="pc-cost mono">{pr.cost}</div>
        <div className="pc-wall">{pr.wallTime} · {pr.stepCount} steps</div>
        <div className="pc-agents">
          {pr.agents.map((a, i) => (
            <span key={i} className="pc-agent mono">{a.name}</span>
          ))}
        </div>
      </div>
    </button>
  );
}

function RepoPullsList({ repoId, onOpenPull }) {
  const def = REPO_DEFS[repoId];
  const prs = REPO_PULLS[repoId] || [];
  const open = prs.filter(p => p.status === "open").length;
  const merged = prs.filter(p => p.status === "merged").length;
  const [filter, setFilter] = React.useState("open");
  const filtered = prs.filter(p => filter === "all" || p.status === filter);

  return (
    <div className="landing landing-repo">
      <header className="repo-hero">
        <div className="rh-icon"><Icon name="git-branch" size={18} /></div>
        <div className="rh-title">
          <h1 className="rh-name"><span className="ns">{def?.ns}/</span><span className="nm">{def?.nm}</span></h1>
          <p className="rh-desc">Pull requests with intent alignment from agent trails.</p>
        </div>
        <div className="rh-meta">
          <div className="rh-stat"><div className="k">Open</div><div className="v mono">{open}</div></div>
          <div className="rh-stat"><div className="k">Merged</div><div className="v mono">{merged}</div></div>
          <div className="rh-stat"><div className="k">Total</div><div className="v mono">{prs.length}</div></div>
        </div>
      </header>

      <div className="pulls-toolbar">
        <div className="pulls-tabs">
          <button
            className="pulls-tab"
            aria-current={filter === "open"}
            onClick={() => setFilter("open")}
          >
            <span className="dot" style={{ background: "var(--c-git)" }} />
            <span>Open</span>
            <span className="mono c">{open}</span>
          </button>
          <button
            className="pulls-tab"
            aria-current={filter === "merged"}
            onClick={() => setFilter("merged")}
          >
            <Icon name="git-commit" size={11} />
            <span>Merged</span>
            <span className="mono c">{merged}</span>
          </button>
          <button
            className="pulls-tab"
            aria-current={filter === "all"}
            onClick={() => setFilter("all")}
          >
            <span>All</span>
            <span className="mono c">{prs.length}</span>
          </button>
        </div>
        <div className="pulls-tip mono">
          <InfoIcon size={11} />
          <span>Tap a PR to see commit-level intent alignment</span>
        </div>
      </div>

      <div className="pulls-list">
        {filtered.map(pr => (
          <PullCard key={pr.id} pr={pr} onOpen={onOpenPull} />
        ))}
        {filtered.length === 0 && (
          <div className="pulls-empty">No pull requests in this view.</div>
        )}
      </div>
    </div>
  );
}

// Detail view — fetches the embedded PR HTML and renders it.
function RepoPullDetail({ repoId, pullId, onBack }) {
  const def = REPO_DEFS[repoId];
  const pr = (REPO_PULLS[repoId] || []).find(p => p.id === pullId);
  const [mainHtml, setMainHtml] = React.useState(null);
  const [railHtml, setRailHtml] = React.useState(null);
  const [err, setErr] = React.useState(null);

  React.useEffect(() => {
    if (!pr || !pr.detail) return;
    // The preview server may prepend instrumentation (<style>/<script>) to
    // served .html files; strip those so they don't leak into the page and
    // blow away global styles (e.g. html/body background → transparent).
    const clean = (html) => html.replace(/<style\b[\s\S]*?<\/style>/gi, "").replace(/<script\b[\s\S]*?<\/script>/gi, "");
    Promise.all([
      fetch(`data/pr-pulse-main.html`).then(r => r.text()),
      fetch(`data/pr-pulse-rail.html`).then(r => r.text()),
    ])
      .then(([m, r]) => { setMainHtml(clean(m)); setRailHtml(clean(r)); })
      .catch(e => setErr(String(e)));
  }, [pullId]);

  if (!pr) return <div style={{padding: 40, color: "var(--fg-mute)"}}>Pull request not found.</div>;

  return (
    <div className="landing pulls-detail-wrap">
      <div className="pr-detail">
        {err && <div style={{padding: 20, color: "var(--c-error)"}}>load error: {err}</div>}
        {!mainHtml && !err && <div style={{padding: 40, color: "var(--fg-mute)"}}>Loading PR…</div>}
        {mainHtml && (
          <main className="app-main" dangerouslySetInnerHTML={{ __html: mainHtml }} />
        )}
        <aside className="app-rail">
          {/* PR meta — status, branch, GitHub link. Lives with the other
              rail boxes instead of a second breadcrumb bar. */}
          <div className="rail-section rail-meta">
            <div className="rail-head"><span>Pull request</span><span className="right mono">#{pr.number}</span></div>
            <div className="rm-top">
              <PullStatusPill status={pr.status} />
              <span className="rm-branch mono">
                <Icon name="git-branch" size={11} />
                <span>{pr.branch}</span>
                <span className="arrow">→</span>
                <span>{pr.base}</span>
              </span>
            </div>
            <div className="rail-prov rm-rows">
              <div className="row"><span className="k">author</span><span className="v">{pr.author}</span></div>
              <div className="row"><span className="k">updated</span><span className="v">{pr.updated}</span></div>
            </div>
            <button className="rm-gh-btn">
              <ExternalIcon size={12} />
              <span>Open on GitHub</span>
            </button>
          </div>
          {railHtml && <div className="rail-embed" dangerouslySetInnerHTML={{ __html: railHtml }} />}
        </aside>
      </div>
    </div>
  );
}

// Outer router for the repo "Pull requests" child. The open PR lives in the
// app route (pullId prop) so the topbar breadcrumb is the single source of
// navigation — no local detail bar.
function RepoPullsPage({ repoId, pullId, onOpenPull, onClosePull }) {
  if (pullId) {
    return (
      <RepoPullDetail
        repoId={repoId}
        pullId={pullId}
        onBack={onClosePull}
      />
    );
  }
  return <RepoPullsList repoId={repoId} onOpenPull={onOpenPull} />;
}

window.RepoPullsPage = RepoPullsPage;
window.RepoPullsList = RepoPullsList;
window.RepoPullDetail = RepoPullDetail;
window.REPO_PULLS = REPO_PULLS;
