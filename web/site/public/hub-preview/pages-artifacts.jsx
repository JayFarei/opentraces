// ─────────────────────────────────────────────────────────────
// Artifacts pages — index (grid of generated artifacts) and
// detail (themed sandbox render + share / pin).
// Also the sidebar flat-group section. Data: OtArtifacts store.
// ─────────────────────────────────────────────────────────────

function artTimeAgo(ts) {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
}

const ART_KIND_ICON = { dashboard: "grid", report: "conversation", chart: "activity", table: "trail", view: "swatch", note: "tag" };

// Renders artifact HTML in a sandboxed, theme-synced iframe that
// auto-sizes to its content.
function ArtifactFrame({ artifact, theme, maxH }) {
  const ref = React.useRef(null);
  const [h, setH] = React.useState(300);
  // srcdoc must be built AFTER the app commits the data-theme attribute —
  // building it during render would snapshot the previous theme's CSS vars.
  // The rAF defers past App's theme effect so getComputedStyle sees the
  // new theme; the initial state covers first mount.
  const [srcdoc, setSrcdoc] = React.useState(() => otArtifactSrcdoc(artifact.html));
  React.useEffect(() => {
    let done = false;
    const sync = () => {
      if (done) return;
      done = true;
      setSrcdoc(otArtifactSrcdoc(artifact.html));
      // Belt-and-braces: also patch the already-loaded iframe document in
      // place (srcdoc replacement can lag a paint) so the flip is instant.
      try {
        const d = ref.current && ref.current.contentDocument;
        if (d && d.documentElement) {
          const fresh = otArtifactSrcdoc("");
          const style = fresh.match(/<style>([\s\S]*?)<\/style>/);
          d.documentElement.setAttribute("data-theme", document.documentElement.getAttribute("data-theme") || "dark");
          const tag = d.querySelector("style");
          if (style && tag) tag.textContent = style[1];
        }
      } catch (e) {}
    };
    // rAF can be starved in background/hidden frames — timeout fallback
    // (same hazard App's theme effect documents).
    const raf = requestAnimationFrame(sync);
    const tid = setTimeout(sync, 80);
    return () => { cancelAnimationFrame(raf); clearTimeout(tid); };
  }, [artifact.html, theme]);
  const measure = () => {
    try {
      const d = ref.current && ref.current.contentDocument;
      if (d && d.body) setH(Math.max(160, Math.min(maxH || 1600, d.body.scrollHeight + 20)));
    } catch (e) {}
  };
  return (
    <iframe
      className="art-frame"
      ref={ref}
      sandbox="allow-same-origin"
      srcDoc={srcdoc}
      style={{ height: h }}
      onLoad={measure}
      title={artifact.name}
    ></iframe>
  );
}

function ArtShareButton({ artifact }) {
  const [open, setOpen] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const url = OtArtifacts.shareUrl(artifact.id);
  const copy = () => {
    try { navigator.clipboard.writeText(url); } catch (e) {}
    OtArtifacts.update(artifact.id, { shared: true });
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };
  return (
    <span className="art-share-wrap">
      <button className="art-btn" data-primary="true" onClick={() => setOpen(o => !o)}>
        <Icon name="share" size={13} />
        <span>Share</span>
      </button>
      {open && (
        <React.Fragment>
          <div className="art-pop-backdrop" onClick={() => setOpen(false)}></div>
          <div className="art-share-pop">
            <div className="sp-lbl">Anyone with the link can view</div>
            <div className="sp-row">
              <span className="sp-url mono">{url}</span>
              <button className="art-btn sp-copy" onClick={copy}>{copied ? "Copied ✓" : "Copy"}</button>
            </div>
          </div>
        </React.Fragment>
      )}
    </span>
  );
}

// ── Detail page ──
function ArtifactPage({ artifactId, theme, onBack }) {
  const [artifacts, setArtifacts] = React.useState(() => OtArtifacts.get());
  React.useEffect(() => OtArtifacts.subscribe(setArtifacts), []);
  const a = artifacts.find(x => x.id === artifactId);
  if (!a) {
    return (
      <div className="landing landing-page">
        <div className="art-missing">Artifact not found — it may have been removed. <button className="art-btn" onClick={onBack}>All artifacts</button></div>
      </div>
    );
  }
  const saveDraft = () => OtArtifacts.update(a.id, { draft: false });
  const editWithClaude = () => window.dispatchEvent(new CustomEvent("ot-edit-artifact", { detail: { id: a.id, name: a.name } }));
  const remove = () => {
    if (!window.confirm('Delete artifact “' + a.name + '”? This can\u2019t be undone.')) return;
    OtArtifacts.remove(a.id);
    onBack();
  };
  return (
    <div className="landing landing-page art-detail" data-screen-label={"Artifact · " + a.name}>
      <header className="art-head">
        <div className="art-head-main">
          <div className="art-kicker">
            <Icon name={ART_KIND_ICON[a.kind] || "swatch"} size={13} />
            <span>{a.kind}</span>
            {a.draft && <span className="art-badge draft">draft — not saved</span>}
            {a.shared && <span className="art-badge shared">shared</span>}
            {a.pinned && <span className="art-badge pinned">pinned</span>}
          </div>
          <h1 className="art-title">{a.name}</h1>
          <div className="art-byline">
            <span className="clui-spark">✳</span> Generated by Claude · {artTimeAgo(a.updatedAt)}
            {a.prompt ? <span className="art-prompt" title={a.prompt}>“{a.prompt}”</span> : null}
          </div>
        </div>
        <div className="art-actions">
          {a.draft && (
            <button className="art-btn art-btn-save" title="Save to the workspace — appears under Artifacts" data-agent-action="artifact-save" data-agent-desc="Save this draft artifact to the workspace" data-agent-mutates="true" onClick={saveDraft}>
              <Icon name="save" size={13} />
              <span>Save to workspace</span>
            </button>
          )}
          <button className="art-btn" title="Open Claude with this artifact as context" data-agent-action="artifact-edit" data-agent-desc="Edit this artifact with Claude" onClick={editWithClaude}>
            <span className="clui-spark" aria-hidden="true">✳</span>
            <span>Edit</span>
          </button>
          <button className="art-btn" data-active={a.pinned} title={a.pinned ? "Unpin from sidebar" : "Pin to sidebar"} data-agent-action="artifact-pin" data-agent-desc="Pin/unpin this artifact in the sidebar" onClick={() => OtArtifacts.update(a.id, { pinned: !a.pinned })}>
            <Icon name="panel" size={13} />
            <span>{a.pinned ? "Pinned" : "Pin"}</span>
          </button>
          <ArtShareButton artifact={a} />
          <button className="art-btn art-btn-danger" title="Delete this artifact" data-agent-action="artifact-delete" data-agent-desc="Delete this artifact" data-agent-mutates="true" onClick={remove}>
            <Icon name="trash" size={13} />
          </button>
        </div>
      </header>
      <div className="art-stage">
        <ArtifactFrame artifact={a} theme={theme} />
      </div>
    </div>
  );
}

// ── Index page ──
function ArtifactsIndexPage({ onSelectArtifact, theme }) {
  const [artifacts, setArtifacts] = React.useState(() => OtArtifacts.get());
  React.useEffect(() => OtArtifacts.subscribe(setArtifacts), []);
  const sorted = artifacts.filter(a => !a.draft).slice().sort((a, b) => (b.pinned - a.pinned) || (b.updatedAt - a.updatedAt));
  return (
    <div className="landing landing-page" data-screen-label="Artifacts index">
      <PageHero
        kicker="Generated with Claude"
        title="Artifacts"
        subtitle="Dashboards, reports and views Claude built from your workspace data. Share them by link, or pin them to the sidebar."
      />
      {sorted.length === 0 ? (
        <div className="art-empty">Nothing here yet — ask Claude for a dashboard (⌘J) and keep what it makes.</div>
      ) : (
        <div className="art-grid">
          {sorted.map(a => (
            <button className="art-card" key={a.id} onClick={() => onSelectArtifact(a.id)}>
              <div className="art-card-preview">
                <ArtifactFrame artifact={a} theme={theme} maxH={420} />
                <div className="art-card-veil"></div>
              </div>
              <div className="art-card-meta">
                <span className="art-card-icon"><Icon name={ART_KIND_ICON[a.kind] || "swatch"} size={14} /></span>
                <div className="art-card-txt">
                  <div className="art-card-name">{a.name}</div>
                  <div className="art-card-sub">{a.kind} · {artTimeAgo(a.updatedAt)}</div>
                </div>
                {a.pinned && <span className="art-badge pinned">pinned</span>}
                {a.shared && <span className="art-badge shared">shared</span>}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Sidebar flat group (same level as Datasets / Projects) ──
function ArtifactsSection({ collapsed, onViewAll, onSelectArtifact, activeArtifactId }) {
  const [artifacts, setArtifacts] = React.useState(() => OtArtifacts.get());
  React.useEffect(() => OtArtifacts.subscribe(setArtifacts), []);
  const [groupClosed, onToggleGroup] = useGroupCollapsed("artifacts");
  const recent = artifacts.filter(a => !a.draft).slice().sort((a, b) => (b.pinned - a.pinned) || (b.updatedAt - a.updatedAt)).slice(0, 3);
  if (collapsed) {
    return <RailGroupIcon icon="box" label="Artifacts" onClick={onViewAll} />;
  }
  return (
    <div className="sb-section flat">
      <FlatGroupLabel
        label="Artifacts"
        count={artifacts.length}
        open={!groupClosed}
        onToggle={onToggleGroup}
      />
      {!groupClosed && (
      <div className="sb-section-body flat">
        <div className="sb-list repos">
          {recent.map(a => (
            <div key={a.id} className="sb-repo">
              <button
                className="sb-repo-head"
                aria-current={activeArtifactId === a.id}
                onClick={() => onSelectArtifact(a.id)}
                title={a.name}
              >
                <Icon name={ART_KIND_ICON[a.kind] || "swatch"} size={13} className="icon" />
                {!collapsed && <span className="nm">{a.name}</span>}
                {!collapsed && a.pinned && <span className="sb-art-pin">●</span>}
              </button>
            </div>
          ))}
          {!collapsed && artifacts.length > recent.length && (
            <button className="sb-view-all" onClick={onViewAll} title={"Showing " + recent.length + " recent · " + artifacts.length + " total"}>
              View all <span className="mono">{artifacts.length}</span>
            </button>
          )}
        </div>
      </div>
      )}
    </div>
  );
}

Object.assign(window, { ArtifactsIndexPage, ArtifactPage, ArtifactsSection, ArtifactFrame });
