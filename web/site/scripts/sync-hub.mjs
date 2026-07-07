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
.app-shell[data-embed="1"] { grid-template-columns: 1fr; }
/* No topbar in embed mode, so the main scroll area reclaims its 56px and the
 * conversation scrollers (which subtract --main-chrome) fill the full canvas. */
.app-shell[data-embed="1"] .main { --main-chrome: 0px; height: 100vh; }
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

// Conversation-tab header compaction. The design's sticky header (description +
// spine) collapses on scroll, but only the trail tab (which scrolls .main) wires
// it up; the conversation tab scrolls its own .conv-main, so the effect never
// fired there. We extract one shared handler and drive it from BOTH scrollers,
// gating .main off in the conversation tab so the two don't fight over the state.
// (CSS half lives in applyConvCssPatch.) Anchored on `handleJumpLatest`, which is
// stable design code present across exports.
const RAW_JUMPLATEST = `  const handleJumpLatest = () => {
    const stream = document.getElementById("conv-stream");
    stream?.scrollTo({ top: stream.scrollHeight, behavior: "smooth" });
  };`;

const SEAMED_JUMPLATEST = `  const handleJumpLatest = () => {
    const stream = document.getElementById("conv-stream");
    stream?.scrollTo({ top: stream.scrollHeight, behavior: "smooth" });
  };
  /* @ot-embed-seam:start — sticky-header compaction shared by both trace
     scrollers. The trail tab scrolls .main; the conversation tab scrolls its own
     .conv-main, so wire both to one handler. Hysteresis: compact past 48px,
     restore below 12px. */
  const applyHeaderCompact = (y) => {
    setHeaderCompact(prev => {
      if (!prev && y > 48) return true;
      if (prev && y < 12) return false;
      return prev;
    });
  };
  /* @ot-embed-seam:end */`;

const RAW_MAIN_ONSCROLL = `        <div className="main" ref={mainScrollRef} onScroll={(e) => {
          const y = e.currentTarget.scrollTop;
          // Hysteresis: enter compact past 48px, leave only below 12px — a
          // single threshold flip-flops while the header height animates.
          setHeaderCompact(prev => {
            if (!prev && y > 48) return true;
            if (prev && y < 12) return false;
            return prev;
          });
        }}>`;

const SEAMED_MAIN_ONSCROLL = `        <div className="main" ref={mainScrollRef} onScroll={(e) => {
          /* @ot-embed-seam: the conversation tab scrolls .conv-main, not .main;
             let it own compaction so the two scrollers do not fight. */
          if (view === "trace" && activeTab === "conversation") return;
          applyHeaderCompact(e.currentTarget.scrollTop);
        }}>`;

const RAW_CONVMAIN = `                  <div className="conv-main" id="conv-main">`;

const SEAMED_CONVMAIN = `                  <div className="conv-main" id="conv-main"
                    /* @ot-embed-seam: drive header compaction from the conversation scroller */
                    onScroll={(e) => applyHeaderCompact(e.currentTarget.scrollTop)}>`;

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
    ["pulls deep-link wiring",
      `activeRepoChild === "pulls" ? <RepoPullsPage repoId={activeRepoId} />`,
      `activeRepoChild === "pulls" ? <RepoPullsPage repoId={activeRepoId} initialPull={HUB_INIT.pullId} /* @ot-embed-seam: deep-link opens a PR detail */ />`,
      "initialPull={HUB_INIT.pullId}"],
    ["conv-compaction shared handler", RAW_JUMPLATEST, SEAMED_JUMPLATEST, "const applyHeaderCompact ="],
    ["conv-compaction .main gate", RAW_MAIN_ONSCROLL, SEAMED_MAIN_ONSCROLL,
      "the conversation tab scrolls .conv-main, not .main"],
    ["conv-compaction .conv-main wiring", RAW_CONVMAIN, SEAMED_CONVMAIN,
      "drive header compaction from the conversation scroller"],
  ]) {
    if (out.includes(markerIfApplied)) { steps.push(`${name} present (skip)`); continue; }
    if (!out.includes(raw)) throw new Error(`anchor missing: ${name}. Design structure changed — re-review seam.`);
    out = out.replace(raw, seamed);
    steps.push(`apply ${name}`);
  }

  return { out, steps };
}

// Small component-level behavior the site keeps on top of the design export,
// re-applied on every import until/unless it is folded into the design source.
// (RepoPullsPage opens straight into a PR detail when deep-linked via ?pr=…)
const PULLS_FILE = join(HUB, "landing-repo-pulls.jsx");
const RAW_PULLS = `function RepoPullsPage({ repoId }) {
  const [openPullId, setOpenPullId] = React.useState(null);`;
const SEAMED_PULLS = `// \`initialPull\` (optional) boots straight into a PR detail instead of the list —
// the marketing /hub pulls card passes it via ?pr=… so the embed opens on an
// open PR; standalone sidebar nav passes nothing and lands on the list.
function RepoPullsPage({ repoId, initialPull }) {
  const [openPullId, setOpenPullId] = React.useState(initialPull || null);`;

function applyPullsPatch() {
  if (!existsSync(PULLS_FILE)) return "landing-repo-pulls.jsx missing (skip)";
  const src = readFileSync(PULLS_FILE, "utf8");
  if (src.includes("function RepoPullsPage({ repoId, initialPull })")) return "pulls PR-default present (skip)";
  if (!src.includes(RAW_PULLS)) throw new Error("anchor missing: RepoPullsPage signature. Design structure changed — re-review the pulls PR-default patch.");
  writeFileSync(PULLS_FILE, src.replace(RAW_PULLS, SEAMED_PULLS));
  return "apply pulls PR-default";
}

// CSS half of the conversation-tab header-compaction fix (the JS half is in
// applySeam). Ties the bounded conversation scrollers to the live sticky-header
// height (--gh-top, maintained by the design's own ResizeObserver) so they fill
// exactly below the header and grow as it compacts — making the stream "move up"
// like the trail view. --main-chrome carries the topbar height (0 in embed mode,
// set by _embed.css). Idempotent + loud-fail, like the index.html seam.
const APP_CSS = join(HUB, "app.css");
const RAW_MAIN_CSS = `.main {
  height: calc(100vh - 56px);
  overflow-y: auto;`;
const SEAMED_MAIN_CSS = `.main {
  /* @ot-embed-seam: topbar height reserved above the scroll area; 0 in embed
     mode (set by _embed.css). The conversation scrollers subtract this + the
     live sticky-header height so they grow as the header compacts on scroll. */
  --main-chrome: 56px;
  height: calc(100vh - var(--main-chrome));
  overflow-y: auto;`;
const RAW_CONV_MAXH = `max-height: calc(100vh - 280px);`;
const SEAMED_CONV_MAXH = `max-height: calc(100vh - var(--main-chrome, 56px) - var(--gh-top, 396px));`;

function applyConvCssPatch() {
  if (!existsSync(APP_CSS)) return "app.css missing (skip)";
  let css = readFileSync(APP_CSS, "utf8");
  const before = css;
  if (css.includes("--main-chrome:")) return "conv-compaction CSS present (skip)";
  if (!css.includes(RAW_MAIN_CSS)) throw new Error("anchor missing: .main height rule (app.css). Design structure changed — re-review the conv-compaction CSS patch.");
  if (!css.includes(RAW_CONV_MAXH)) throw new Error("anchor missing: .conv-main/.conv-side max-height (app.css). Design structure changed — re-review the conv-compaction CSS patch.");
  css = css.replace(RAW_MAIN_CSS, SEAMED_MAIN_CSS).split(RAW_CONV_MAXH).join(SEAMED_CONV_MAXH);
  if (css === before) return "conv-compaction CSS no-op";
  writeFileSync(APP_CSS, css);
  return "apply conv-compaction CSS";
}

function cmdApply() {
  if (!existsSync(INDEX)) { console.error(`✗ ${INDEX} not found — run the design pull first (see scripts/SYNC-HUB.md).`); process.exit(2); }
  if (!existsSync(EMBED_CSS)) { writeFileSync(EMBED_CSS, EMBED_CSS_BODY); console.log("· wrote public/hub-preview/_embed.css"); }
  const html = readFileSync(INDEX, "utf8");
  const { out, steps } = applySeam(html);
  for (const s of steps) console.log(`  · ${s}`);
  if (out !== html) { writeFileSync(INDEX, out); console.log("✓ embed seam applied to index.html"); }
  else console.log("✓ index.html already fully seamed (no change)");
  console.log(`  · ${applyPullsPatch()}`);
  console.log(`  · ${applyConvCssPatch()}`);
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
