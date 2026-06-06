// Shared visual primitives for landing pages: minimap fingerprint + agent glyph

// Tiny minimap fingerprint — same color system as the trace minimap, scalable size.
function Fingerprint({ classes, height = 18, gap = 1, className = "", title }) {
  return (
    <div
      className={"fingerprint " + className}
      style={{ height, gap }}
      title={title}
      aria-label={title}
    >
      {classes.map((c, i) => (
        <span key={i} className={"fp-seg " + c} />
      ))}
    </div>
  );
}

// Stylized agent glyph — small monogram-style logos. We avoid recreating
// brand marks; these are abstract OT-style placeholders coded by agent id.
function AgentGlyph({ agent, size = 14 }) {
  const k = agent?.icon || "claude";
  const map = {
    claude:   { bg: "var(--c-write)",  ch: "✦" },
    codex:    { bg: "var(--c-think)",  ch: "◉" },
    cursor:   { bg: "var(--c-read)",   ch: "▸" },
    gemini:   { bg: "var(--c-plan)",   ch: "◆" },
    opencode: { bg: "var(--c-exec)",   ch: "◐" },
  };
  const v = map[k] || map.claude;
  return (
    <span
      className="agent-glyph"
      style={{
        width: size, height: size,
        background: `color-mix(in oklch, ${v.bg} 22%, var(--surface-2))`,
        border: `1px solid color-mix(in oklch, ${v.bg} 40%, transparent)`,
        color: v.bg,
        fontSize: Math.round(size * 0.7),
      }}
      aria-hidden="true"
    >{v.ch}</span>
  );
}

// Status pill, used across landings
function StatusPill({ status }) {
  const labels = {
    committed: "committed", pushed: "pushed", working: "working", failed: "failed",
    synced: "synced", ahead: "ahead", behind: "behind", local: "local",
    ok: "ok", warn: "warn",
  };
  return <span className={"status-pill st-" + status}>{labels[status] || status}</span>;
}

// Format relative time
function relTime(d) {
  const now = new Date(Date.UTC(2026, 3, 29, 14, 30));
  const diff = (now - d) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  if (diff < 86400 * 7) return Math.floor(diff / 86400) + "d ago";
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${months[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

// Day-group key like "Today", "Yesterday", "Tue, Mar 17"
function dayKey(d) {
  const now = new Date(Date.UTC(2026, 3, 29));
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const that = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  const days = (today - that) / 86400000;
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  const wk = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
  const mo = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${wk[d.getUTCDay()]}, ${mo[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

// Short repo display: "ns/nm" with ns dim
function RepoLabel({ repo }) {
  const parts = repo.split("/");
  return <span className="repo-label"><span className="ns">{parts[0]}/</span><span className="nm">{parts[1]}</span></span>;
}

// Hugging Face mark — recognizable yellow rounded chip with the 🤗 glyph.
// We don't recreate the official logo; this is a brand-evocative placeholder.
function HuggingFaceLogo({ size = 14, withWordmark = false }) {
  const chip = (
    <span
      className="hf-logo"
      style={{
        width: size, height: size,
        fontSize: Math.round(size * 0.78),
      }}
      aria-hidden="true"
    >🤗</span>
  );
  if (!withWordmark) return chip;
  return (
    <span className="hf-mark" aria-label="Hugging Face">
      {chip}
      <span className="hf-wm">Hugging Face</span>
    </span>
  );
}

window.HuggingFaceLogo = HuggingFaceLogo;
window.Fingerprint = Fingerprint;
window.AgentGlyph = AgentGlyph;
window.StatusPill = StatusPill;
window.relTime = relTime;
window.dayKey = dayKey;
window.RepoLabel = RepoLabel;
