// Trace Capsules — CLIENT WATCHLIST.
//
// The maintainer console (triage queue) is for the reader exploring a failure.
// This is the opposite reader: the client agent that filed a blocker and asks
// one question — "am I unblocked, and what's the one action to resume?"
//
// One row per intent · one status bit · one action (only when green).
// It is the human-glanceable render of `ot capsule inbox --json`.

function WatchRow({ w, onResume, onMute, onOpen }) {
  const resolved = w.state === "resolved";
  return (
    <div className={"watch-row " + (resolved ? "wr-green" : "wr-red")} role="button" tabIndex={0}
      onClick={() => onOpen(w.id)}
      onKeyDown={(e) => { if (e.key === "Enter") onOpen(w.id); }}>
      <span className={"wr-light " + (resolved ? "on" : "off")} />
      <div className="wr-main">
        <div className="wr-head">
          <span className="wr-state">{w.resumed ? "unblocked" : resolved ? "fix available" : "blocked"}</span>
          {w.watching && <span className="wr-watch"><Icon name="bell" size={10} /> watching</span>}
        </div>
        <div className="wr-goal">{w.goal}</div>
        <div className="wr-detail">
          {resolved
            ? <span className="wr-fix"><span className="mono">{w.error.split("\n")[0].slice(0, 52)}</span> — fixed in <span className="wr-pin mono">{w.resolvedIn}</span></span>
            : <span className="wr-block">
                <span className="mono">{w.error.split("\n")[0].slice(0, 52)}</span>
                {w.regressed ? " — regressed on latest" : " — no fix yet"}
                {w.issue && <span className="wr-issue"> · {w.issue} {w.issueState}</span>}
              </span>}
        </div>
        {!resolved && w.workaround && (
          <div className="wr-workaround"><Icon name="tool" size={11} /> workaround: {w.workaround}</div>
        )}
      </div>
      <div className="wr-action" onClick={(e) => e.stopPropagation()}>
        {resolved
          ? (w.resumed
              ? <span className="wr-resumed"><Icon name="check" size={13} /> resumed</span>
              : <button className="resume-btn" onClick={() => onResume(w.id)}><Icon name="play" size={13} /> Resume</button>)
          : <button className="mute-btn" onClick={() => onMute(w.id)}>mute</button>}
      </div>
    </div>
  );
}

function WatchlistView({ items, viewMode, setViewMode, onResume, onMute, onOpen, onGotoConsole }) {
  const greens = items.filter(w => w.state === "resolved");
  const reds = items.filter(w => w.state === "blocked");
  const json = JSON.stringify(inboxJSON(items), null, 2);

  return (
    <div className="watchlist">
      <div className="wl-head">
        <div className="wl-titles">
          <div className="wl-kicker mono">client agent · you</div>
          <h2 className="wl-title">Watchlist</h2>
          <p className="wl-sub">One row per thing you were trying to do. A single light tells you if it's unblocked; the only action is to resume. Backed by <span className="mono">ot capsule inbox</span> — this page is just its glanceable render.</p>
        </div>
        <div className="wl-modes">
          <button className={"wl-mode" + (viewMode === "glance" ? " on" : "")} onClick={() => setViewMode("glance")}><Icon name="rows" size={13} /> Glance</button>
          <button className={"wl-mode" + (viewMode === "json" ? " on" : "")} onClick={() => setViewMode("json")}><Icon name="tool" size={13} /> Agent inbox</button>
        </div>
      </div>

      <div className="wl-summary">
        <span className="wls-stat green"><span className="wls-dot" />{greens.length} ready to resume</span>
        <span className="wls-stat red"><span className="wls-dot" />{reds.length} still blocked</span>
        <span className="wls-push"><Icon name="bell" size={11} /> push on unblock — no polling</span>
      </div>

      {viewMode === "glance" ? (
        <div className="wl-list">
          {greens.length > 0 && <div className="wl-group-l">Unblocked — resume these</div>}
          {greens.map(w => <WatchRow key={w.id} w={w} onResume={onResume} onMute={onMute} onOpen={onOpen} />)}
          {reds.length > 0 && <div className="wl-group-l muted">Still waiting</div>}
          {reds.map(w => <WatchRow key={w.id} w={w} onResume={onResume} onMute={onMute} onOpen={onOpen} />)}
        </div>
      ) : (
        <div className="wl-json">
          <div className="wlj-cmd mono"><span className="sc-prompt">$</span> ot capsule inbox --json</div>
          <pre className="wlj-pre mono">{json}</pre>
          <div className="wlj-push">
            <div className="wlj-push-h"><Icon name="bell" size={13} /> Push, not poll</div>
            <p className="wlj-push-p">When the client gets blocked it registers a watch. When the verdict flips green it receives one message with an actionable payload — no browsing, no list.</p>
            <div className="wlj-term mono">
              <div><span className="sc-prompt">$</span> ot capsule watch pytorch/pytorch#142203</div>
              <div className="wlj-wait">… watching · sealed @d0c7e44 · push enabled</div>
              <div className="wlj-unblocked">UNBLOCKED — fixed in torch@2.7. Re-pose your original intent against new HEAD.</div>
            </div>
          </div>
        </div>
      )}

      <button className="wl-console-link" onClick={onGotoConsole}>
        You're the client here. Maintainers get the full triage console <Icon name="arrow-right" size={13} />
      </button>
    </div>
  );
}

Object.assign(window, { WatchRow, WatchlistView });

// ── CLIENT CAPSULE VIEW ─────────────────────────────────────────
// Opening a watched capsule from the client's side: it's a shared, replayable
// trace. Foreground the open URL, what's sealed inside (trails · context ·
// trace data · snapshot), how many times it's been replayed, and the linked
// issue state. One action — resume — when it's green. No maintainer chrome.
function ClientCapsule({ cap, w, onBack, onResume, onCopyLink, onOpenUrl, onOpenTrace }) {
  const resolved = w.state === "resolved";
  const m = cap.manifest;
  const replays = cap.replays;
  const passes = replays.filter(r => r.outcome === "pass").length;
  const fails = replays.filter(r => r.outcome === "fail").length;
  const last = replays[replays.length - 1];
  const url = `hub.opentraces.dev/c/${cap.cid}`;
  const contents = [
    { icon: "activity", key: "trails", label: "action trajectory", meta: `${m.trajectory.steps} steps · ${m.trajectory.bytes}`, view: true },
    { icon: "node", key: "context", label: "context tree", meta: `${m.ctx.nodes} nodes · ${m.ctx.tokens} tok` },
    { icon: "alert", key: "trace data", label: "fail step", meta: `step ${cap.fail.step} · ${m.failStep.bytes}` },
    { icon: "box", key: "snapshot", label: "git snapshot", meta: `${cap.git.sha} · ${m.git.bytes}` },
  ];

  return (
    <div className="landing client-cap">
      <button className="cd-back" onClick={onBack}><Icon name="back" size={14} /> Watchlist</button>

      <div className="ccap-head">
        <span className={"ccap-light " + (resolved ? "on" : "off")} />
        <div className="ccap-titles">
          <div className="ccap-state">{w.resumed ? "unblocked" : resolved ? "fix available" : "blocked"}{w.watching && <span className="ccap-watch"><Icon name="bell" size={10} /> watching</span>}</div>
          <h1 className="ccap-goal">{w.goal}</h1>
          <div className="ccap-meta">
            <span className="mono">{cap.cid}</span>
            <span className="dot-sep" />
            <span className="ccap-agent"><AgentGlyph agent={cap.agent} size={12} /> {cap.agent.name}</span>
            <span className="dot-sep" />
            <span className="mono">sealed @{cap.git.sha}</span>
          </div>
        </div>
      </div>

      {/* the open URL — a shared replayable trace */}
      <div className="ccap-url">
        <Icon name="link" size={14} />
        <span className="ccap-url-txt mono">{url}</span>
        <span className="ccap-url-actions">
          <button className="ccap-url-btn" onClick={onCopyLink}><Icon name="copy" size={13} /> Copy</button>
          <button className="ccap-url-btn primary" onClick={onOpenUrl}><Icon name="external" size={13} /> Open replay</button>
        </span>
      </div>

      {/* status + the one action */}
      <div className={"ccap-status " + (resolved ? "green" : "red")}>
        <div className="ccap-status-l">
          <Icon name={resolved ? "check" : "alert"} size={16} />
          <div>
            <div className="ccap-status-h">{resolved ? <>Fixed in <span className="mono">{w.resolvedIn}</span></> : w.regressed ? "Regressed on latest" : "No fix yet"}</div>
            <div className="ccap-status-s">{resolved ? `${w.pin}, then re-pose your intent against new HEAD` : w.workaround ? `workaround: ${w.workaround}` : "you'll get a push the moment it turns green"}</div>
          </div>
        </div>
        {resolved
          ? (w.resumed ? <span className="wr-resumed"><Icon name="check" size={13} /> resumed</span> : <button className="resume-btn" onClick={onResume}><Icon name="play" size={13} /> Resume</button>)
          : <span className="ccap-waiting"><Icon name="bell" size={12} /> push enabled</span>}
      </div>

      <div className="ccap-grid">
        {/* what's inside */}
        <div className="ccap-section">
          <div className="ccap-sec-h">What's in this capsule</div>
          <div className="ccap-contents">
            {contents.map(c => (
              <div className="ccap-row" key={c.key}>
                <span className="ccap-row-ico"><Icon name={c.icon} size={14} /></span>
                <span className="ccap-row-key">{c.key}</span>
                <span className="ccap-row-label">{c.label}</span>
                <span className="ccap-row-meta mono">{c.meta}</span>
                {c.view
                  ? <button className="ccap-row-view" onClick={() => onOpenTrace && onOpenTrace(RECENT_TRACES[0].id)}>view</button>
                  : <span className="ccap-row-view ghost">sealed</span>}
              </div>
            ))}
          </div>
        </div>

        {/* replays + issue */}
        <div className="ccap-aside">
          <div className="ccap-section">
            <div className="ccap-sec-h">Replays</div>
            <div className="ccap-replays">
              <div className="ccap-rcount"><span className="ccap-rn mono">{replays.length}</span><span className="ccap-rl">times replayed</span></div>
              <div className="ccap-rbreak">
                <span className="ccap-rb pass"><span className="vdot v-git" /> {passes} pass</span>
                <span className="ccap-rb fail"><span className="vdot v-error" /> {fails} fail</span>
              </div>
              <div className="ccap-rlast">last: <span className={"mono " + (last.outcome === "pass" ? "ok" : last.outcome === "fail" ? "bad" : "")}>{last.outcome}</span> @{last.sha} · {last.when}</div>
            </div>
          </div>

          <div className="ccap-section">
            <div className="ccap-sec-h">Linked issue</div>
            {cap.issue && cap.issue.number != null ? (
              <button className="ccap-issue" onClick={onOpenUrl}>
                <Icon name="issue" size={14} className={cap.issue.state === "closed" ? "ico-closed" : "ico-open"} />
                <span className="ccap-issue-repo mono">{cap.repo}</span>
                <span className="ccap-issue-num mono">#{cap.issue.number}</span>
                <span className={"ccap-issue-state " + (cap.issue.state === "closed" ? "closed" : "open")}>{cap.issue.state}</span>
              </button>
            ) : (
              <div className="ccap-issue none"><Icon name="file" size={14} /> no issue linked</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { WatchRow, WatchlistView, ClientCapsule });
