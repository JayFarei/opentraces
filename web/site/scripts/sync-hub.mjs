#!/usr/bin/env node
// sync-hub — re-apply the marketing-site embed seam to a freshly imported
// OpenTraces Hub design export, and verify the /hub view contract still holds.
//
// THE PROACTIVE SYNC JOB (run whenever the Claude design changes):
//   1. Pull the design's runtime files into public/hub-preview/ via the
//      claude_design MCP (DesignSync get_file), overwriting in place. The entry
//      file "OpenTraces Hub v2.html" is written as index.html. This step is
//      driven by the agent (the MCP is not a CLI); see scripts/SYNC-HUB.md.
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
//
// v2 NOTE (Hub v2 import, 2026-08): two v1-era patches were retired because the
// design absorbed them — conversation-tab header compaction (v2 wires
// compactFromScroll on both scrollers itself) and the pulls PR deep-link (v2's
// RepoPullsPageV2 takes pullId straight from the route, which the parser seeds).

import { readFileSync, writeFileSync, existsSync, readdirSync } from "node:fs";
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
/* v2 shell: .sb-flip (sidebar+CLUI rail) is not rendered in embed mode (JSX
   guard), so the content column just needs to own the full width, and the main
   scroll area reclaims the topbar's height. */
.app-shell[data-embed="1"] { grid-template-columns: 1fr; }
.app-shell[data-embed="1"] .app-content { width: 100%; }
.app-shell[data-embed="1"] .main { --main-chrome: 0px; height: 100vh; }
`;

// ── The seam, as ordered transforms over the raw export's index.html ──────────
// Each entry: { name, marker (present => already applied, skip), find, insert|replace }.

const PARSER_BLOCK = `<script>
/* @ot-embed-seam:start — site-owned host seam (deep-link + chromeless embed).
   Inert when opened standalone: with no ?embed/?view params it yields the exact
   full-chrome defaults the Claude design ships with, so the artifact renders as
   authored. The marketing site passes ?embed=1&view=… to boot one chromeless
   panel. This block is re-applied verbatim by scripts/sync-hub on every design
   re-import, and is structured to lift straight into the design source later.
   It emits window.__HUB_INIT__ = { embed, tab, benchTab, route } where route is
   a v2 canonical route object (the same shape App.navigate/applyRoute use). */
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
  var artifact = p.get("artifact");
  var evidence = p.get("evidence");
  var capsule = p.get("capsule");
  var benchtab = p.get("benchtab");
  var init = {
    embed: p.get("embed") === "1",
    tab: tab === "trail" ? "trail" : "conversation",
    benchTab: null,
    route: null,
  };
  var HOME_REPO = "jayfarei/opentraces";
  if (v === "repo") {
    var r = repo || HOME_REPO;
    var c = child || "overview";
    /* pseudo-children map onto the bench hub's inner tab, mirroring openRepo */
    if (c === "atlas" || c === "checks" || c === "environment") { init.benchTab = "atlas"; c = "bench"; }
    else if (c === "evidence") { init.benchTab = "evidence"; c = "bench"; }
    else if (c === "bench") { init.benchTab = benchtab || "runs"; }
    init.route = { view: "repo", repoId: r, repoChild: c,
      pullId: (c === "pulls" && pr) ? (pr.indexOf("pr-") === 0 ? pr : "pr-" + pr) : null };
  } else if (v === "dataset") {
    init.route = { view: "dataset", datasetId: dataset, datasetChild: child || "overview" };
  } else if (v === "trace") {
    init.route = { view: "trace", traceId: traceId || null };
  } else if (v === "artifact") {
    init.route = { view: "artifact", nav: "artifacts-index", artifactId: artifact || null };
  } else if (v === "evidence-detail") {
    init.route = { view: "evidence-detail", nav: "evidence", evidenceId: evidence || null };
  } else if (v === "capsule-detail") {
    init.route = { view: "capsule-detail", nav: "capsules", capsuleId: capsule || null };
  } else if (v) {
    init.route = { view: v, nav: v === "traces-landing" ? "traces" : v };
  }
  window.__HUB_INIT__ = init;
})();
/* @ot-embed-seam:end */
</script>

`;

// ── Anchors in the v2 export (exact strings; STRICT on drift) ────────────────

const RAW_USESTATE = `  const [view, setView] = React.useState("traces-landing");
  const [activeNav, setActiveNav] = React.useState("traces");
  const [activeTraceId, setActiveTraceId] = React.useState(null);
  const [activeDatasetId, setActiveDatasetId] = React.useState(null);
  const [activeDatasetChild, setActiveDatasetChild] = React.useState(null);
  const [expandedDataset, setExpandedDataset] = React.useState(null);
  const [activeRepoId, setActiveRepoId] = React.useState(null);
  const [expandedRepo, setExpandedRepo] = React.useState(window.ACCOUNT_HOME_REPO || "jayfarei/opentraces");
  const [activeRepoChild, setActiveRepoChild] = React.useState(null);
  const [activePullId, setActivePullId] = React.useState(null);
  const [activeArtifactId, setActiveArtifactId] = React.useState(null);
  const [activeEvidenceId, setActiveEvidenceId] = React.useState(null);
  const [activeCapsuleId, setActiveCapsuleId] = React.useState(null);
  const [atlasFocus, setAtlasFocus] = React.useState(null);
  const [benchTab, setBenchTab] = React.useState("atlas");`;

const SEAMED_USESTATE = `  /* @ot-embed-seam:start — boot initial nav state from the host (URL params),
     each falling back to the design's own default so standalone is unchanged. */
  const HUB_INIT = (typeof window !== "undefined" && window.__HUB_INIT__) || {};
  const embed = !!HUB_INIT.embed;
  const INIT_ROUTE = HUB_INIT.route || {};
  /* @ot-embed-seam:end */
  const [view, setView] = React.useState(INIT_ROUTE.view || "traces-landing");
  const [activeNav, setActiveNav] = React.useState(INIT_ROUTE.view ? (INIT_ROUTE.nav || "") : "traces");
  const [activeTraceId, setActiveTraceId] = React.useState(INIT_ROUTE.traceId ?? null);
  const [activeDatasetId, setActiveDatasetId] = React.useState(INIT_ROUTE.datasetId ?? null);
  const [activeDatasetChild, setActiveDatasetChild] = React.useState(INIT_ROUTE.datasetChild ?? null);
  const [expandedDataset, setExpandedDataset] = React.useState(INIT_ROUTE.datasetId ?? null);
  const [activeRepoId, setActiveRepoId] = React.useState(INIT_ROUTE.repoId ?? null);
  const [expandedRepo, setExpandedRepo] = React.useState(INIT_ROUTE.repoId || window.ACCOUNT_HOME_REPO || "jayfarei/opentraces");
  const [activeRepoChild, setActiveRepoChild] = React.useState(INIT_ROUTE.repoChild ?? null);
  const [activePullId, setActivePullId] = React.useState(INIT_ROUTE.pullId ?? null);
  const [activeArtifactId, setActiveArtifactId] = React.useState(INIT_ROUTE.artifactId ?? null);
  const [activeEvidenceId, setActiveEvidenceId] = React.useState(INIT_ROUTE.evidenceId ?? null);
  const [activeCapsuleId, setActiveCapsuleId] = React.useState(INIT_ROUTE.capsuleId ?? null);
  const [atlasFocus, setAtlasFocus] = React.useState(null);
  const [benchTab, setBenchTab] = React.useState(HUB_INIT.benchTab || "atlas");`;

const RAW_ACTIVETAB = `  const [activeTab, setActiveTab] = React.useState("conversation");`;

const SEAMED_ACTIVETAB = `  const [activeTab, setActiveTab] = React.useState(HUB_INIT.tab || "conversation"); /* @ot-embed-seam: ?tab=trail deep-link */`;

const RAW_THEME = `    try { localStorage.setItem("ot-theme", theme); } catch (e) {}
    requestAnimationFrame(() => requestAnimationFrame(() => html.classList.remove("theme-swap")));`;

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
    requestAnimationFrame(() => requestAnimationFrame(() => html.classList.remove("theme-swap")));`;

const RAW_SHELL_ATTRS = `    <div
      className="app-shell"
      data-sb={sidebarCollapsed ? "collapsed" : "expanded"}`;

const SEAMED_SHELL_ATTRS = `    <div
      className="app-shell"
      data-embed={embed ? "1" : "0"}
      data-sb={sidebarCollapsed ? "collapsed" : "expanded"}`;

const RAW_SBFLIP_OPEN = `    >
      <div className="sb-flip">`;

const SEAMED_SBFLIP_OPEN = `    >
      {/* @ot-embed-seam: chrome is suppressed in embed mode so each /hub iframe
          shows only its feature panel. Standalone (embed=false) renders both. */}
      {!embed && (
      <div className="sb-flip">`;

const RAW_SBFLIP_CLOSE = `        <div className="clui-resize" onMouseDown={startResize} title="Drag to resize"></div>
      </div>

      <div className="app-content">`;

const SEAMED_SBFLIP_CLOSE = `        <div className="clui-resize" onMouseDown={startResize} title="Drag to resize"></div>
      </div>
      )}

      <div className="app-content">`;

const RAW_TOPBAR_OPEN = `        <TopbarNavV2
          workspace={account === "org" ? "OpenMake" : "Jayfarei"}`;

const SEAMED_TOPBAR_OPEN = `        {!embed && (
        <TopbarNavV2
          workspace={account === "org" ? "OpenMake" : "Jayfarei"}`;

const RAW_TOPBAR_CLOSE = `          traceShortId={trace?.trace_id?.slice(0, 7) || ""}
        />

        <div className="main" key={account}`;

const SEAMED_TOPBAR_CLOSE = `          traceShortId={trace?.trace_id?.slice(0, 7) || ""}
        />
        )}

        <div className="main" key={account}`;

function applySeam(html) {
  let out = html;
  const steps = [];

  // 1. _embed.css link (anchored on the design's theme-boot script in <head>).
  if (!out.includes('href="_embed.css')) {
    const anchor = `<script>\n  try {\n    var t = localStorage.getItem('ot-theme') || 'dark';`;
    if (!out.includes(anchor)) throw new Error("anchor missing: theme-boot <script> (head). Design structure changed — re-review seam.");
    out = out.replace(anchor, `<!-- @ot-embed-seam — site-owned chromeless-embed overlay (not in the design export) -->\n<link rel="stylesheet" href="_embed.css?v=2" />\n${anchor}`);
    steps.push("inject _embed.css link");
  } else steps.push("_embed.css link present (skip)");

  // 2. React dev → production builds (faster; matches the deployed baseline).
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

  // 4+. App-internal edits (idempotent + loud-fail).
  for (const [name, raw, seamed, markerIfApplied] of [
    ["nav-state useStates", RAW_USESTATE, SEAMED_USESTATE, "const HUB_INIT ="],
    ["activeTab deep-link", RAW_ACTIVETAB, SEAMED_ACTIVETAB, "HUB_INIT.tab ||"],
    ["theme parent-bridge", RAW_THEME, SEAMED_THEME, "the one genuinely web-only piece"],
    ["app-shell data-embed attr", RAW_SHELL_ATTRS, SEAMED_SHELL_ATTRS, 'data-embed={embed ? "1" : "0"}'],
    ["sb-flip open guard", RAW_SBFLIP_OPEN, SEAMED_SBFLIP_OPEN, "chrome is suppressed in embed mode"],
    ["sb-flip close guard", RAW_SBFLIP_CLOSE, SEAMED_SBFLIP_CLOSE, '</div>\n      )}\n\n      <div className="app-content">'],
    ["TopbarNavV2 open guard", RAW_TOPBAR_OPEN, SEAMED_TOPBAR_OPEN, '{!embed && (\n        <TopbarNavV2'],
    ["TopbarNavV2 close guard", RAW_TOPBAR_CLOSE, SEAMED_TOPBAR_CLOSE, '/>\n        )}\n\n        <div className="main" key={account}'],
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
  writeFileSync(EMBED_CSS, EMBED_CSS_BODY);
  console.log("· wrote public/hub-preview/_embed.css");
  const html = readFileSync(INDEX, "utf8");
  const { out, steps } = applySeam(html);
  for (const s of steps) console.log(`  · ${s}`);
  if (out !== html) { writeFileSync(INDEX, out); console.log("✓ embed seam applied to index.html"); }
  else console.log("✓ index.html already fully seamed (no change)");
}

// Guard against a mis-mapped pull: a *.css that actually holds JS/JSX, a *.jsx
// that holds CSS, or a *.css byte-identical to its sibling *.jsx (the exact
// failure that once blanked the run-intelligence styles). Cheap, runs on every
// check so a bad pull is caught before deploy instead of in production.
function checkFileTypes() {
  const files = readdirSync(HUB);
  const problems = [];
  const jsHead = /\b(function\s|=>|React\.|window\.[A-Za-z]|className=)/;
  const cssRule = /^\s*[.#@][\w-]+[^\n]*\{/m;
  for (const f of files) {
    if (f.endsWith(".css")) {
      const head = readFileSync(join(HUB, f), "utf8").slice(0, 2000);
      if (jsHead.test(head) && !cssRule.test(head)) problems.push(`${f} looks like JS/JSX, not CSS`);
      const twin = f.replace(/\.css$/, ".jsx");
      if (files.includes(twin) && readFileSync(join(HUB, f), "utf8") === readFileSync(join(HUB, twin), "utf8"))
        problems.push(`${f} is byte-identical to ${twin} (mis-mapped pull)`);
    } else if (f.endsWith(".jsx")) {
      const head = readFileSync(join(HUB, f), "utf8").slice(0, 1500);
      if (cssRule.test(head) && !jsHead.test(head)) problems.push(`${f} looks like CSS, not JS`);
    }
  }
  if (problems.length) {
    console.error("✗ file-type mismatch in public/hub-preview (re-pull the named files):");
    for (const p of problems) console.error(`  - ${p}`);
    process.exit(1);
  }
  console.log(`✓ file types intact — ${files.filter(f => /\.(css|jsx)$/.test(f)).length} css/jsx files are the right kind.`);
}

// Contract check: every view/child referenced by /hub must be handled by the App.
function cmdCheck() {
  checkFileTypes();
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
