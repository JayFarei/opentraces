// ─────────────────────────────────────────────────────────────
// Artifacts — generative pages produced with Claude.
// OtArtifacts is the store (localStorage-backed, subscribable).
// An artifact is raw HTML rendered in a themed sandbox frame;
// otArtifactSrcdoc() wraps it with the Hub's live theme variables
// so artifacts follow light/dark automatically.
// Seeds are computed from REAL workspace data (traces, pulls,
// datasets) the first time the store loads.
// ─────────────────────────────────────────────────────────────

// Wrap an artifact HTML fragment in a full document carrying the
// Hub's current theme. Called at render time so theme flips re-skin
// every artifact.
// Wrap an artifact HTML fragment in a full document carrying the
// Hub's current theme. Called at render time so theme flips re-skin
// every artifact. Also ships a tiny class library (ot-card, ot-kpi,
// ot-label, ot-bar…) so generated artifacts share the Hub's design
// system instead of re-inventing surfaces inline.
function otArtifactSrcdoc(html) {
  const cs = getComputedStyle(document.documentElement);
  const vars = [
    "--bg", "--surface", "--surface-2", "--surface-3", "--border", "--border-strong",
    "--fg", "--fg-dim", "--fg-mute", "--fg-sub", "--radius", "--shadow-card",
    "--font-body", "--font-mono", "--font-display",
    "--c-user", "--c-plan", "--c-read", "--c-exec", "--c-write", "--c-error", "--c-git", "--c-push",
  ];
  const decl = vars.map(v => v + ":" + cs.getPropertyValue(v).trim() + ";").join("");
  const theme = document.documentElement.getAttribute("data-theme") || "dark";
  const base =
    ":root{" + decl + "}*{box-sizing:border-box}" +
    "body{margin:0;padding:2px;background:transparent;color:var(--fg);font-family:var(--font-body);font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}" +
    "h1,h2,h3{font-family:var(--font-display)}code,pre{font-family:var(--font-mono)}" +
    /* shared component classes — keep in sync with the create_artifact styleguide */
    ".ot-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:16px 18px}" +
    ".ot-label{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--fg-mute)}" +
    ".ot-kpi{font-size:24px;font-weight:600;font-family:var(--font-display)}" +
    ".ot-sub{font-size:11px;color:var(--fg-mute)}" +
    ".ot-mono{font-family:var(--font-mono);font-size:12px}" +
    ".ot-track{height:8px;border-radius:4px;background:var(--surface-3);overflow:hidden}" +
    ".ot-fill{height:100%;border-radius:4px;background:var(--c-git)}" +
    ".ot-grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(180px,1fr))}";
  return "<!doctype html><html data-theme='" + theme + "'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>" +
    "<link href='https://fonts.googleapis.com/css2?family=Geist+Mono:wght@400;500&family=Geist:wght@300;400;500;600;700&family=Space+Grotesk:wght@300;400;600;700&display=swap' rel='stylesheet'>" +
    "<style>" + base + "</style>" +
    "</head><body>" + html + "</body></html>";
}

// ── Seed artifacts, grounded in the workspace's real data ──
function otSeedFleetHtml() {
  let traces = [];
  try { traces = RECENT_TRACES.slice(0, 14); } catch (e) {}
  const byAgent = {};
  traces.forEach(t => {
    const k = (t.agent && t.agent.name) || "unknown";
    byAgent[k] = byAgent[k] || { total: 0, ok: 0 };
    byAgent[k].total++;
    if (t.status !== "failed") byAgent[k].ok++;
  });
  const rows = Object.entries(byAgent).map(([name, s]) => {
    const pct = Math.round((s.ok / s.total) * 100);
    return "<div style='display:grid;grid-template-columns:150px 1fr 52px;gap:12px;align-items:center'>" +
      "<div style='font-size:13px;color:var(--fg-dim)'>" + name + "</div>" +
      "<div style='height:8px;border-radius:4px;background:var(--surface-3);overflow:hidden'><div style='height:100%;width:" + pct + "%;border-radius:4px;background:var(--c-git)'></div></div>" +
      "<div style='font-family:var(--font-mono);font-size:12px;text-align:right'>" + pct + "%</div></div>";
  }).join("");
  const failed = traces.filter(t => t.status === "failed").length;
  return "<div style='display:flex;flex-direction:column;gap:20px'>" +
    "<div style='display:flex;gap:12px'>" +
    ["<b>" + traces.length + "</b> recent traces", "<b>" + (traces.length - failed) + "</b> healthy", "<b>" + failed + "</b> failed"].map(s =>
      "<div style='flex:1;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px;font-size:13px;color:var(--fg-dim)'>" + s + "</div>").join("") +
    "</div>" +
    "<div style='background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:18px;display:flex;flex-direction:column;gap:12px'>" +
    "<div style='font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--fg-mute)'>Survival by agent</div>" + rows + "</div></div>";
}

function otSeedPrHtml() {
  let pulls = [];
  try { pulls = (window.REPO_PULLS && window.REPO_PULLS["jayfarei/opentraces"]) || []; } catch (e) {}
  const rows = pulls.slice(0, 5).map(p =>
    "<div style='display:grid;grid-template-columns:44px 1fr auto;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid var(--border)'>" +
    "<div style='font-family:var(--font-mono);font-size:12px;color:var(--fg-mute)'>#" + p.number + "</div>" +
    "<div><div style='font-size:13px'>" + p.title + "</div>" +
    "<div style='font-size:11px;color:var(--fg-mute)'>" + (p.intents ? p.intents.aligned + "/" + p.intents.total + " intents aligned · " : "") + p.commits + " commits · " + p.updated + "</div></div>" +
    "<div style='font-size:11px;font-family:var(--font-mono);color:" + (p.verdict === "attention" ? "var(--c-user)" : "var(--c-git)") + "'>" + (p.verdictLabel || p.status) + "</div></div>"
  ).join("");
  return "<div style='background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:6px 18px 2px'>" + rows + "</div>";
}

function otSeedDatasetHtml() {
  let ds = [];
  try { ds = DATASETS; } catch (e) {}
  const tone = { synced: "var(--c-git)", ahead: "var(--c-push)", behind: "var(--c-user)", local: "var(--fg-mute)" };
  const cards = ds.map(d =>
    "<div style='background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px;display:flex;flex-direction:column;gap:6px'>" +
    "<div style='font-family:var(--font-mono);font-size:12px'>" + d.name + "</div>" +
    "<div style='font-size:20px;font-family:var(--font-display);font-weight:600'>" + d.rows + "<span style='font-size:11px;color:var(--fg-mute);font-family:var(--font-body);font-weight:400'> rows</span></div>" +
    "<div style='font-size:11px;color:" + (tone[d.remote] || "var(--fg-mute)") + "'>● " + d.remote + (d.inbox ? " · " + d.inbox + " in inbox" : "") + "</div></div>"
  ).join("");
  return "<div style='display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px'>" + cards + "</div>";
}

const OtArtifacts = (() => {
  const KEY = "ot-artifacts-v1";
  const subs = new Set();
  let items = null; // lazy — seeds need the data scripts loaded first

  function seeds() {
    const now = Date.now();
    const mk = (name, kind, html, ago) => ({
      id: "art-seed-" + name.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 20),
      name, kind, html,
      prompt: "Seeded example — ask Claude for your own",
      author: "Claude", createdAt: now - ago, updatedAt: now - ago,
      pinned: false, shared: false, seed: true,
    });
    return [
      mk("Fleet survival", "dashboard", otSeedFleetHtml(), 26 * 3600e3),
      mk("PR review load — opentraces", "report", otSeedPrHtml(), 2 * 86400e3),
      mk("Dataset sync health", "dashboard", otSeedDatasetHtml(), 4 * 86400e3),
    ];
  }

  function ensure() {
    if (items) return;
    try {
      const s = JSON.parse(localStorage.getItem(KEY) || "null");
      items = Array.isArray(s) ? s : seeds();
    } catch (e) { items = seeds(); }
  }
  function persist() { try { localStorage.setItem(KEY, JSON.stringify(items.slice(0, 30))); } catch (e) {} }
  function get() { ensure(); return items.map(a => ({ ...a })); }
  function emit() { persist(); const snap = get(); subs.forEach(f => { try { f(snap); } catch (e) {} }); }

  return {
    get,
    find(id) { ensure(); const a = items.find(x => x.id === id); return a ? { ...a } : null; },
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
    add({ name, kind, html, prompt, draft }) {
      ensure();
      const a = {
        id: "art-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 5),
        name: String(name || "Untitled artifact"),
        kind: String(kind || "view"),
        html: String(html || ""),
        prompt: prompt ? String(prompt) : "",
        author: "Claude",
        createdAt: Date.now(), updatedAt: Date.now(),
        pinned: false, shared: false,
        draft: !!draft, // session-scoped chat card until the user saves it
      };
      items.unshift(a);
      emit();
      return { ...a };
    },
    update(id, patch) {
      ensure();
      const i = items.findIndex(x => x.id === id);
      if (i < 0) return null;
      items[i] = { ...items[i], ...patch, updatedAt: Date.now() };
      emit();
      return { ...items[i] };
    },
    remove(id) { ensure(); items = items.filter(x => x.id !== id); emit(); },
    shareUrl(id) { return "https://hub.opentraces.dev/a/" + id; },
  };
})();

Object.assign(window, { OtArtifacts, otArtifactSrcdoc });
