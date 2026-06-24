#!/usr/bin/env node
// sync-hub — re-apply the marketing-site embed seam to a freshly imported
// OpenTraces Hub design export, and verify the /hub view contract still holds.
//
// THE PROACTIVE SYNC JOB (run whenever the Claude design changes):
//   1. Pull the design's runtime files into public/hub-preview/ via the
//      claude_design MCP (DesignSync get_file), overwriting in place. The entry
//      file "OpenTraces Hub.html" is written as index.html. This step is driven
//      by the agent (the MCP is not a CLI); see scripts/SYNC-HUB.md.
//   2. node scripts/sync-hub.mjs apply     ← re-applies the embed seam (this file)
//   3. node scripts/sync-hub.mjs check     ← verifies the /hub view contract
//   4. Re-shoot the two preview posters (public/hub-poster*.png) if visuals moved.
//
// WHY A TRANSFORM INSTEAD OF HAND-PATCHING: the design is the single source of
// truth. The site needs three things the standalone design doesn't ship —
// chromeless embed mode, URL deep-linking, and a parent-frame theme bridge.
// Rather than re-hand-edit index.html on every import (and risk losing it), this
// script re-applies that seam deterministically. Every injected region is
// marked `@ot-embed-seam` so it is idempotent here and copy-paste-able into the
// design source later (at which point `apply` becomes a verified no-op).
//
// The transform is intentionally STRICT: if an expected anchor in the raw export
// is missing (because the design's App structure changed), it throws with the
// anchor name instead of silently shipping a broken embed. That failure IS the
// drift alarm — re-review the seam against the new structure.

import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const SITE = join(HERE, "..");
const HUB = join(SITE, "public", "hub-preview");
const INDEX = join(HUB, "index.html");
const EMBED_CSS = join(HUB, "_embed.css");
const HUB_FEATURES = join(SITE, "src", "lib", "hub-features.ts");

const EMBED_CSS_BODY = `/* @ot-embed-seam — site-owned, NOT part of the Claude design export.
   Chromeless embed layout for the marketing-site /hub iframes (?embed=1).
   The Claude design's own app.css is left untouched; this overlay is preserved
   across design re-imports (the sync job never overwrites it). */
.app-shell[data-embed="1"] { grid-template-columns: 1fr; }
.app-shell[data-embed="1"] .main { height: 100vh; }
`;

// ── The seam, as ordered transforms over the raw export's index.html ──────────
// Each entry: { name, marker (present => already applied, skip), find, insert|replace }.
// `insertBefore`: insert `payload` immediately before `find`.
// `replace`: swap exact `find` for `payload`.

const PARSER_BLOCK = `<script>
/* @ot-embed-seam:start — site-owned host seam (deep-link + chromeless embed).
   Inert when opened standalone: with no ?embed/?view params it yields the exact
   full-chrome defaults the Claude design ships with, so the artifact renders as
   authored. The marketing site passes ?embed=1&view=… to boot one chromeless
   panel. This block is re-applied verbatim by scripts/sync-hub on every design
   re-import, and is structured to lift straight into the design source later. */
(function () {
  var p;
  try { p = new URLSearchParams(window.location.search); } catch (e) { p = new URLSearchParams(""); }
  var v = p.get("view");
  var repo = p.get("repo");
  var dataset = p.get("dataset");
  var child = p.get("child");
  var traceId = p.get("trace");
  var tab = p.get("tab");
  var pr = p.get("pr");
  var init = {
    embed: p.get("embed") === "1",
    view: "traces-landing",
    activeNav: "traces",
    activeRepoId: null, activeRepoChild: null, expandedRepo: "jayfarei/opentraces",
    activeDatasetId: null, activeDatasetChild: null, expandedDataset: null,
    activeTraceId: null,
    activeTab: tab === "trail" ? "trail" : "conversation",
    pullId: pr ? (pr.indexOf("pr-") === 0 ? pr : "pr-" + pr) : null,
  };
  if (v === "repo") {
    var r = repo || "jayfarei/opentraces";
    init.view = "repo"; init.activeNav = ""; init.activeRepoId = r; init.expandedRepo = r; init.activeRepoChild = child || "overview";
  } else if (v === "dataset") {
    var d = dataset || "ds-edge-traces";
    init.view = "dataset"; init.activeNav = ""; init.activeDatasetId = d; init.expandedDataset = d; init.activeDatasetChild = child || "overview";
  } else if (v === "trace") {
    init.view = "trace"; init.activeNav = ""; init.activeTraceId = traceId || null;
  } else if (v) {
    var navFor = { "traces-landing": "traces", spotlight: "spotlight", intents: "intents", evals: "evals", capsules: "capsules", alerts: "alerts", improving: "improving", settings: "settings", compare: "" };
    init.view = v; init.activeNav = navFor[v] != null ? navFor[v] : "";
  }
  window.__HUB_INIT__ = init;
})();
/* @ot-embed-seam:end */
</script>

`;

const RAW_USESTATE = `  // App nav state
  // view: 'trace' (existing trace viewer), 'traces-landing', 'repo', 'dataset'
  const [view, setView] = React.useState("traces-landing");
  const [activeNav, setActiveNav] = React.useState("traces");
  const [expandedSections, setExpandedSections] = React.useState(["datasets", "repositories"]);
  const [searchQuery, setSearchQuery] = React.useState("");
  const [activeTraceId, setActiveTraceId] = React.useState(null);
  const [activeDatasetId, setActiveDatasetId] = React.useState(null);
  const [activeDatasetChild, setActiveDatasetChild] = React.useState(null);
  const [expandedDataset, setExpandedDataset] = React.useState(null);
  const [activeRepoId, setActiveRepoId] = React.useState(null);
  const [expandedRepo, setExpandedRepo] = React.useState("jayfarei/opentraces");
  const [activeRepoChild, setActiveRepoChild] = React.useState(null);
  const [activeTab, setActiveTab] = React.useState("conversation");`;

const SEAMED_USESTATE = `  // App nav state
  // view: 'trace' (existing trace viewer), 'traces-landing', 'repo', 'dataset'
  /* @ot-embed-seam:start — boot initial nav state from the host (URL params),
     each falling back to the design's own default so standalone is unchanged. */
  const HUB_INIT = (typeof window !== "undefined" && window.__HUB_INIT__) || {};
  const embed = !!HUB_INIT.embed;
  const [view, setView] = React.useState(HUB_INIT.view || "traces-landing");
  const [activeNav, setActiveNav] = React.useState(HUB_INIT.activeNav ?? "traces");
  /* @ot-embed-seam:end */
  const [expandedSections, setExpandedSections] = React.useState(["datasets", "repositories"]);
  const [searchQuery, setSearchQuery] = React.useState("");
  const [activeTraceId, setActiveTraceId] = React.useState(HUB_INIT.activeTraceId ?? null);
  const [activeDatasetId, setActiveDatasetId] = React.useState(HUB_INIT.activeDatasetId ?? null);
  const [activeDatasetChild, setActiveDatasetChild] = React.useState(HUB_INIT.activeDatasetChild ?? null);
  const [expandedDataset, setExpandedDataset] = React.useState(HUB_INIT.expandedDataset ?? null);
  const [activeRepoId, setActiveRepoId] = React.useState(HUB_INIT.activeRepoId ?? null);
  const [expandedRepo, setExpandedRepo] = React.useState(HUB_INIT.expandedRepo || "jayfarei/opentraces");
  const [activeRepoChild, setActiveRepoChild] = React.useState(HUB_INIT.activeRepoChild ?? null);
  const [activeTab, setActiveTab] = React.useState(HUB_INIT.activeTab || "conversation");`;

const RAW_THEME = `    try { localStorage.setItem("ot-theme", theme); } catch (e) {}
    // remove the disable-transitions class after the swap commits`;

const SEAMED_THEME = `    try { localStorage.setItem("ot-theme", theme); } catch (e) {}
    /* @ot-embed-seam:start — the one genuinely web-only piece: when the Hub runs
       inside the marketing site's full-chrome iframe (not embed mode), push its
       own sun/moon toggle UP to the parent page so the site chrome stays in sync.
       Guarded + try/caught, so it is a no-op standalone or in any non-iframe host
       (e.g. a future desktop shell never triggers it). */
    try {
      if (!embed && window.parent && window.parent !== window) {
        const pdoc = window.parent.document.documentElement;
        pdoc.setAttribute("data-theme", theme);
        pdoc.classList.remove("theme-dark", "theme-light");
        pdoc.classList.add(theme === "dark" ? "theme-dark" : "theme-light");
        pdoc.style.colorScheme = theme;
        window.parent.localStorage.setItem("theme", theme);
      }
    } catch (e) {}
    /* @ot-embed-seam:end */
    // remove the disable-transitions class after the swap commits`;

const RAW_SHELL = `    <div className="app-shell" data-sb={sidebarCollapsed ? "collapsed" : "expanded"}>
      <Sidebar`;

const SEAMED_SHELL = `    <div className="app-shell" data-embed={embed ? "1" : "0"} data-sb={sidebarCollapsed ? "collapsed" : "expanded"}>
      {/* @ot-embed-seam: chrome is suppressed in embed mode so each /hub iframe
          shows only its feature panel. Standalone (embed=false) renders both. */}
      {!embed && (
      <Sidebar`;

const RAW_SIDEBAR_CLOSE = `        onSelectRepoChild={(rid, cid) => openRepo(rid, cid)}
      />

      <div style={{display: "flex", flexDirection: "column", minWidth: 0}}>
        <Topbar`;

const SEAMED_SIDEBAR_CLOSE = `        onSelectRepoChild={(rid, cid) => openRepo(rid, cid)}
      />
      )}

      <div style={{display: "flex", flexDirection: "column", minWidth: 0}}>
        {!embed && (
        <Topbar`;

const RAW_TOPBAR_CLOSE = `          onBack={() => setView("traces-landing")}
        />`;

const SEAMED_TOPBAR_CLOSE = `          onBack={() => setView("traces-landing")}
        />
        )}`;

function applySeam(html) {
  let out = html;
  const steps = [];

  // 1. _embed.css link (version-agnostic anchor on the theme-boot script).
  if (!out.includes('href="_embed.css')) {
    const anchor = `<script>\n  // Apply theme immediately to avoid flash of wrong theme`;
    if (!out.includes(anchor)) throw new Error("anchor missing: theme-boot <script> (head). Design structure changed — re-review seam.");
    out = out.replace(anchor, `<!-- @ot-embed-seam — site-owned chromeless-embed overlay (not in the design export) -->\n<link rel="stylesheet" href="_embed.css?v=1" />\n${anchor}`);
    steps.push("inject _embed.css link");
  } else steps.push("_embed.css link present (skip)");

  // 2. React dev → production builds (faster; matches the deployed baseline).
  // Idempotent: only acts while the dev builds are still present.
  if (out.includes("react.development.js") || out.includes("react-dom.development.js")) {
    out = out.replace(/<script src="https:\/\/unpkg\.com\/react@([\d.]+)\/umd\/react\.development\.js"[^>]*><\/script>/,
      '<!-- @ot-embed-seam: production React builds (the design exports the slower dev\n     builds; the deployed site has always used production). Swapped by sync-hub. -->\n<script src="https://unpkg.com/react@$1/umd/react.production.min.js" crossorigin="anonymous"></script>');
    out = out.replace(/<script src="https:\/\/unpkg\.com\/react-dom@([\d.]+)\/umd\/react-dom\.development\.js"[^>]*><\/script>/,
      '<script src="https://unpkg.com/react-dom@$1/umd/react-dom.production.min.js" crossorigin="anonymous"></script>');
    steps.push("swap React→production");
  } else steps.push("React already production (skip)");

  // 3. URL-param parser block before App.
  if (!out.includes("window.__HUB_INIT__")) {
    const anchor = `<script type="text/babel">\nfunction App() {`;
    if (!out.includes(anchor)) throw new Error("anchor missing: <script type=text/babel> function App(). Design structure changed — re-review seam.");
    out = out.replace(anchor, `${PARSER_BLOCK}${anchor}`);
    steps.push("inject URL-param parser");
  } else steps.push("parser present (skip)");

  // 4-7. App-internal edits (idempotent + loud-fail).
  for (const [name, raw, seamed, markerIfApplied] of [
    ["nav-state useStates", RAW_USESTATE, SEAMED_USESTATE, "const HUB_INIT ="],
    ["theme parent-bridge", RAW_THEME, SEAMED_THEME, "the one genuinely web-only piece"],
    ["app-shell + Sidebar open guard", RAW_SHELL, SEAMED_SHELL, 'data-embed={embed ? "1" : "0"}'],
    ["Sidebar close + Topbar open guard", RAW_SIDEBAR_CLOSE, SEAMED_SIDEBAR_CLOSE, "/>\n      )}\n\n      <div style={{display:"],
    ["Topbar close guard", RAW_TOPBAR_CLOSE, SEAMED_TOPBAR_CLOSE, 'setView("traces-landing")}\n        />\n        )}'],
  ]) {
    if (out.includes(markerIfApplied)) { steps.push(`${name} present (skip)`); continue; }
    if (!out.includes(raw)) throw new Error(`anchor missing: ${name}. Design structure changed — re-review seam.`);
    out = out.replace(raw, seamed);
    steps.push(`apply ${name}`);
  }

  return { out, steps };
}

function cmdApply() {
  if (!existsSync(INDEX)) { console.error(`✗ ${INDEX} not found — run the design pull first (see scripts/SYNC-HUB.md).`); process.exit(2); }
  if (!existsSync(EMBED_CSS)) { writeFileSync(EMBED_CSS, EMBED_CSS_BODY); console.log("· wrote public/hub-preview/_embed.css"); }
  const html = readFileSync(INDEX, "utf8");
  const { out, steps } = applySeam(html);
  for (const s of steps) console.log(`  · ${s}`);
  if (out !== html) { writeFileSync(INDEX, out); console.log("✓ embed seam applied to index.html"); }
  else console.log("✓ index.html already fully seamed (no change)");
}

// Contract check: every view/child referenced by /hub must be handled by the App.
function cmdCheck() {
  const html = readFileSync(INDEX, "utf8");
  const feats = readFileSync(HUB_FEATURES, "utf8");
  // Views the App actually switches on.
  const handled = new Set([...html.matchAll(/view === "([a-z-]+)"/g)].map(m => m[1]));
  // Views referenced by the showcase config.
  const referenced = new Set([...feats.matchAll(/\bview:\s*"([a-z-]+)"/g)].map(m => m[1]));
  const missing = [...referenced].filter(v => !handled.has(v));
  console.log(`  views handled by App : ${[...handled].sort().join(", ")}`);
  console.log(`  views used by /hub   : ${[...referenced].sort().join(", ")}`);
  if (missing.length) {
    console.error(`✗ /hub references views the design no longer handles: ${missing.join(", ")}`);
    console.error(`  → update src/lib/hub-features.ts or restore the view in the design.`);
    process.exit(1);
  }
  console.log("✓ /hub view contract holds — every referenced view is handled.");
}

const cmd = process.argv[2];
if (cmd === "apply") cmdApply();
else if (cmd === "check") cmdCheck();
else { console.log("usage: node scripts/sync-hub.mjs <apply|check>"); process.exit(2); }
