// ─────────────────────────────────────────────────────────────
// CLUI — Claude as an alternative sidebar for the OpenTraces Hub.
// The nav sidebar and the Claude chat live side-by-side in a
// horizontal strip (.sb-flip); you slide between them with the
// edge handle, the dock pill, or ⌘J.
// Two modes: USE — Claude drives the product itself (capture,
// grading, replay, watching); anything it visualizes lives as a
// draft chat card until saved as an artifact. EDIT — Claude
// changes the Hub app on a scratch branch, keep-or-discard.
// The agent session is itself captured to the bucket (trace:step).
// Powered by window.claude.complete when available.
// ─────────────────────────────────────────────────────────────

const CLUI_PAGES = ["traces", "intents", "evals", "spotlight", "capsules", "alerts", "settings", "datasets-index", "projects-index", "artifacts-index", "traces-index"];
const CLUI_REPO_PAGES = ["overview", "traces", "pulls", "environment", "settings"];

// The agent's map of the app is DERIVED from OtRegistry — the app
// describes itself; nothing here is hand-maintained.
function cluiSiteMap() {
  let map = "";
  try { if (OtRegistry.routes().length) map = OtRegistry.describe(); } catch (e) {}
  if (!map) map = "Site map: " + CLUI_PAGES.join(", ") + ", compare (open with navigate).";
  return map + "\n· Don't guess what exists — query_hub lists the real repos, pulls, datasets, traces and artifacts before you claim something isn't there.\n· Within the current page, list_page_actions shows clickable controls (buttons, toggles) and do_page_action clicks one — use these for anything the routes don't cover.";
}

// (site map is generated — see cluiSiteMap above)

// Selector cheat-sheet given to Claude in EDIT mode so its CSS
// lands on real elements. inspect_ui is the escape hatch for the rest.
const CLUI_UI_GUIDE = [
  "UI map for edit_hub CSS — these selectors exist in the running app:",
  ":root vars: --bg --surface --surface-2 --surface-3 --border --border-strong --fg --fg-dim --fg-mute --radius --sidebar-w --row-h --font-body --font-mono --font-display. Theme attr: html[data-theme='dark'|'light'].",
  "Shell .app-shell · sidebar .sidebar .sb-nav-item .sb-group-label .sb-repo-head .sb-search-input · topbar .topbar .breadcrumb .bc-item .tb-icon-btn .tb-avatar.",
  "Your CSS loads after every stylesheet, so plain rules usually win — reserve !important for stubborn cases. One coherent change per edit_hub call; each call is one checkpoint the user can revert.",
].join("\n");

function cluiTraceCatalog() {
  try {
    return RECENT_TRACES.slice(0, 14).map(t => ({
      id: t.id, title: t.title, repo: t.repo, agent: t.agent.name, status: t.status,
    }));
  } catch (e) { return []; }
}

function cluiSystemPrompt(context, mode) {
  const traces = cluiTraceCatalog();
  return [
    "You are Claude operating the OpenTraces Hub — a local-first evidence layer for agent work: capture (Trace/Trail/Ctx), evaluation (evals, skill verifiers), observation (spotlight, alerts, survival) and training material (capsules, datasets).",
    "You have two modes. USE: drive the product for the user — navigate, open traces and repos, search, and run product actions with hub_action (no CLI or live fleet is attached in this preview, so hub_action results are simulated — always say so). When the user asks for a dashboard, report, chart or analysis, BUILD it with create_artifact: real HTML grounded in query_hub data — it opens on the canvas as a session DRAFT (a card in this chat); only when the user saves it does it join the workspace Artifacts index, where it can be shared or pinned. EDIT: change the Hub app itself with edit_hub — you write REAL CSS that is injected into the running app immediately and recorded as a checkpoint on a scratch branch; the user can revert to any checkpoint, or keep/discard the whole branch. Verify selectors with inspect_ui before writing CSS. Structural or JS changes aren't possible in this preview — say so and offer the closest CSS-only version.",
    "Requests to view, show, open, list or visualize something (\"show me the trail\", \"show me all the datasets\", \"open X\") are USE actions — SHOW the page with navigate/open_* tools instead of reciting data as text. Answer in text only when the user asks a question the UI can't show. Viewing needs no permission; only edit_hub changes the app.",
    "Current mode: " + (mode === "edit" ? "EDIT" : "USE") + ". Prefer tools that match the mode, but you may suggest switching.",
    mode === "edit" ? CLUI_UI_GUIDE : "",
    "This conversation is itself captured to the user's bucket as a trace (unless they turned capture off) — you are both operator and evidence.",
    "Be terse: 1-3 short sentences. No markdown headings or lists unless asked. Use the tools to act — don't describe actions you could take, take them.",
    "",
    "Current app state: " + JSON.stringify(context),
    (() => {
      // When an artifact page is open, give Claude its source so
      // "edit this" works on the real thing.
      try {
        const rid = window.__otRoute && window.__otRoute.artifactId;
        if (!rid) return "";
        const a = OtArtifacts.find(rid);
        if (!a) return "";
        return "Open artifact (id " + a.id + ", \"" + a.name + "\", kind " + a.kind + ") — current html source:\n" + String(a.html).slice(0, 6000) + "\nTo change it, call update_artifact with this id and the FULL modified html.";
      } catch (e) { return ""; }
    })(),
    cluiSiteMap(),
    "Recent traces (id, title, repo, agent, status):",
    JSON.stringify(traces),
  ].join("\n");
}

// Module-level hooks so tool handlers can reach component state
let cluiSetActivity = () => {};
let cluiRequestPermission = async () => true;
let cluiPushActionLine = () => {};
let cluiPushCard = () => {};
let cluiGetProvenance = () => ({});
let cluiSaveArtifact = () => {};

// Theme-compliance lint for generated artifact HTML: hardcoded
// colors won't follow light/dark, so flag them back to the model.
function cluiArtifactThemeLint(html) {
  const s = String(html);
  const hard = (s.match(/:\s*#[0-9a-fA-F]{3,8}\b|:\s*rgba?\(|,\s*#[0-9a-fA-F]{6}\b/g) || []).length;
  const varsOk = (s.match(/var\(--/g) || []).length;
  if (!hard) return "";
  return " WARNING: found " + hard + " hardcoded color(s) (hex/rgb). These will NOT adapt to light/dark mode — call update_artifact replacing every hardcoded color with theme vars (accents: var(--c-git) var(--c-error) var(--c-user) var(--c-push) var(--c-plan) var(--c-exec); surfaces/text: var(--surface*) var(--fg*)). Currently " + varsOk + " var() usages.";
}

// ── App commands on the bus (idempotent; called on pane mount and
// again at tool-build time so the bus is live before any send) ──
function cluiRegisterCommands(actions) {
  OtCommands.register([
    { id: "navigate", label: "Open a route", taxonomy: "reads", run: ({ route, params }) => OtRegistry.open(route, params) },
    { id: "open-trace", label: "Open a trace", taxonomy: "reads", run: ({ id, tab }) => { actions.openTrace(id, tab); return "Opened trace " + String(id).slice(0, 7) + (tab === "trail" ? " on the Trail visualization" : ""); } },
    { id: "show-trail", label: "Show the Trail", taxonomy: "reads", run: ({ id }) => { if (id) actions.openTrace(id, "trail"); else actions.setTraceTab("trail"); return "Trail visualization is showing"; } },
    { id: "open-repo", label: "Open a repository page", taxonomy: "reads", run: ({ id, page }) => { actions.openRepo(id, page); return "Opened " + id + (page ? " → " + page : ""); } },
    { id: "open-pull", label: "Open a pull request", taxonomy: "reads", run: ({ repo, number }) => {
      const pulls = (window.REPO_PULLS && window.REPO_PULLS[repo]) || [];
      if (!pulls.length) throw new Error("No traced PRs for " + repo);
      const p = number != null ? pulls.find(x => x.number === Number(number)) : pulls[0];
      if (!p) throw new Error("PR #" + number + " not found in " + repo + " — have: " + pulls.map(x => x.number).join(", "));
      actions.openPull(repo, p.id);
      return "Opened PR #" + p.number + " — " + p.title;
    } },
    { id: "run-spotlight", label: "Run a Spotlight search", taxonomy: "reads", run: ({ query }) => { actions.runSpotlight(query); return "Spotlight query running: " + query; } },
    { id: "set-theme", label: "Switch the theme", taxonomy: "writes", run: ({ theme }) => { actions.setTheme(theme); return "Theme set to " + theme; } },
  ]);
}

function cluiBuildTools(actions) {
  const act = (label, fn) => async (input) => {
    cluiSetActivity(label);
    const out = await fn(input);
    cluiPushActionLine(label);
    return out || "done";
  };
  // ── Permission gate keyed off the command taxonomy ──
  const gate = async (taxonomy, label) => {
    if (taxonomy === "reads") return;
    cluiSetActivity("Waiting for permission");
    const ok = await cluiRequestPermission(label, taxonomy === "destructive");
    if (!ok) throw new Error("User denied the action");
  };
  cluiRegisterCommands(actions);
  const tools = [
    {
      name: "navigate",
      description: "Open any route in the Hub. Pass the route id from the site map, plus params when the route takes them — e.g. {route:'datasets-index'}, {route:'repo', params:{id:'jayfarei/opentraces', page:'pulls'}}, {route:'trace', params:{id:'…', tab:'trail'}}.",
      input_schema: {
        type: "object",
        properties: {
          route: { type: "string", description: "route id from the site map" },
          params: { type: "object", description: "route params, when the site map lists them" },
        },
        required: ["route"],
      },
      run: act("Navigating", async ({ route, params }) => OtCommands.dispatch("navigate", { route, params })),
    },
    {
      name: "list_page_actions",
      description: "List the interactable controls on the CURRENT view — every visible labeled button/link/input is auto-discovered; curated ones carry richer descriptions. Returns id, description, kind (button|input), and mutates (needs user permission). ALWAYS call this before do_page_action.",
      input_schema: { type: "object", properties: {} },
      run: async () => {
        cluiSetActivity("Reading the page");
        const acts = OtAgentActions.list().filter(a => a.visible);
        return acts.length ? JSON.stringify(acts.slice(0, 80)) : "No controls on this view — use routes, or inspect_ui to look at the DOM.";
      },
    },
    {
      name: "do_page_action",
      description: "Operate a control from list_page_actions by id. For kind=button it clicks; for kind=input pass text to type (submits with Enter). Actions marked mutates:true ask the user for permission first.",
      input_schema: { type: "object", properties: { id: { type: "string" }, text: { type: "string", description: "for inputs — the text to type" } }, required: ["id"] },
      run: async ({ id, text }) => {
        const acts = OtAgentActions.list();
        const meta = acts.find(a => a.id === id);
        if (meta && meta.mutates) {
          cluiSetActivity("Waiting for permission");
          const ok = await cluiRequestPermission((meta.description || id) + "?");
          if (!ok) throw new Error("User denied the action");
        }
        cluiSetActivity("Operating the page");
        const out = (meta && meta.kind === "input" && text != null)
          ? OtAgentActions.type(id, text)
          : OtAgentActions.invoke(id);
        cluiPushActionLine(out);
        return out;
      },
    },
    {
      name: "open_trace",
      description: "Open a trace in the trace viewer by its id (from the recent traces list). Optional tab: 'trail' for the Trail (Visual) timeline, 'conversation' (default) for the transcript.",
      input_schema: { type: "object", properties: { id: { type: "string" }, tab: { type: "string", enum: ["trail", "conversation"] } }, required: ["id"] },
      run: act("Opening trace", async ({ id, tab }) => OtCommands.dispatch("open-trace", { id, tab })),
    },
    {
      name: "show_trail",
      description: "Show the Trail (Visual) timeline — the visualization of a trace's steps. Switches the currently open trace to the Trail tab; pass id to open a different trace's trail. Read-only, no permission needed.",
      input_schema: { type: "object", properties: { id: { type: "string" } } },
      run: act("Opening trail", async ({ id }) => OtCommands.dispatch("show-trail", { id })),
    },
    {
      name: "open_repo",
      description: "Open a repository page, e.g. jayfarei/opentraces. Optional page: " + CLUI_REPO_PAGES.join(" | ") + " (default overview). Pull requests are at page 'pulls'.",
      input_schema: { type: "object", properties: { id: { type: "string" }, page: { type: "string", enum: CLUI_REPO_PAGES } }, required: ["id"] },
      run: act("Opening repo", async ({ id, page }) => OtCommands.dispatch("open-repo", { id, page })),
    },
    {
      name: "query_hub",
      description: "Look up what actually exists in the Hub before answering or navigating. kind: repos | pulls | datasets | traces | artifacts | capsules. For pulls, pass repo (defaults to jayfarei/opentraces). Returns real entities with ids you can pass to open_* tools.",
      input_schema: { type: "object", properties: { kind: { type: "string", enum: ["repos", "pulls", "datasets", "traces", "artifacts", "capsules"] }, repo: { type: "string" } }, required: ["kind"] },
      run: async ({ kind, repo }) => {
        cluiSetActivity("Querying " + kind);
        const safe = (fn) => { try { return fn(); } catch (e) { return null; } };
        if (kind === "repos") return JSON.stringify((safe(() => REPOS) || []).map(r => r.id));
        if (kind === "datasets") return JSON.stringify((safe(() => DATASETS) || []).map(d => ({ id: d.id, name: d.name, rows: d.rows, remote: d.remote })));
        if (kind === "traces") return JSON.stringify(cluiTraceCatalog());
        if (kind === "artifacts") return JSON.stringify(OtArtifacts.get().map(a => ({ id: a.id, name: a.name, kind: a.kind, pinned: a.pinned, shared: a.shared, draft: !!a.draft })));
        if (kind === "capsules") return JSON.stringify((safe(() => CAPSULES) || []).map(c => ({ id: c.id, cid: c.cid, title: c.title, repo: c.repo, lifecycle: c.lifecycle, published: c.publishedAt, views: c.stats && c.stats.views })));
        if (kind === "pulls") {
          const rid = repo || "jayfarei/opentraces";
          const pulls = (window.REPO_PULLS && window.REPO_PULLS[rid]) || [];
          if (!pulls.length) return JSON.stringify({ repo: rid, pulls: [], note: "No traced PRs for this repo" });
          return JSON.stringify({ repo: rid, pulls: pulls.map(p => ({ id: p.id, number: p.number, title: p.title, status: p.status, verdict: p.verdictLabel, updated: p.updated })) });
        }
        throw new Error("Unknown kind: " + kind);
      },
    },
    {
      name: "open_pull",
      description: "Open a pull request's detail page. Pass repo and the PR number (from query_hub kind=pulls); omit number for the most recent PR.",
      input_schema: { type: "object", properties: { repo: { type: "string" }, number: { type: "number" } }, required: ["repo"] },
      run: act("Opening pull request", async ({ repo, number }) => OtCommands.dispatch("open-pull", { repo, number })),
    },
    {
      name: "run_spotlight",
      description: "Run a Spotlight search across all traces with a natural-language query.",
      input_schema: { type: "object", properties: { query: { type: "string" } }, required: ["query"] },
      run: act("Searching", async ({ query }) => OtCommands.dispatch("run-spotlight", { query })),
    },
    {
      name: "set_theme",
      description: "Switch the Hub theme. Mutating — may require user permission.",
      input_schema: { type: "object", properties: { theme: { type: "string", enum: ["dark", "light"] } }, required: ["theme"] },
      run: async ({ theme }) => {
        await gate("writes", "Switch theme to " + theme + "?");
        cluiSetActivity("Applying theme");
        const out = await OtCommands.dispatch("set-theme", { theme });
        cluiPushActionLine("Theme → " + theme);
        return out;
      },
    },
    {
      name: "create_artifact",
      description: "Create a generative artifact — a dashboard, report, chart or view. It opens on the canvas as a session DRAFT (a card in this chat); the user can save it to the workspace Artifacts index, then share or pin it. html is a fragment (no <html>/<head>, NO <script>). It renders in a themed sandbox that defines the Hub's CSS variables — you MUST use them for ALL colors (never hex/rgb — hardcoded colors break light/dark mode): var(--bg) var(--surface) var(--surface-2) var(--surface-3) var(--border) var(--fg) var(--fg-dim) var(--fg-mute) var(--radius); accents var(--c-git) (green/good) var(--c-error) (red/bad) var(--c-user) (amber/warn) var(--c-push) (blue) var(--c-plan) var(--c-exec); fonts var(--font-body), var(--font-display) for headings, var(--font-mono) for ids and numbers. Prefer the built-in classes: ot-card (surface card), ot-label (uppercase section label), ot-kpi (big number), ot-sub (small muted), ot-mono, ot-track/ot-fill (bar chart track+fill), ot-grid (responsive card grid). Build charts with CSS (flex/grid bars, proportional widths/heights) — no external resources, no images. For opacity variants use color-mix(in oklab, var(--c-git) 30%, transparent). Make it responsive (grid auto-fill, flexible widths). Ground every number in query_hub data — never invent stats.",
      input_schema: {
        type: "object",
        properties: {
          name: { type: "string" },
          kind: { type: "string", enum: ["dashboard", "report", "chart", "table", "view", "note"] },
          html: { type: "string" },
          prompt: { type: "string", description: "The user ask this answers, one short line" },
        },
        required: ["name", "kind", "html"],
      },
      run: async ({ name, kind, html, prompt }) => {
        cluiSetActivity("Creating artifact");
        if (/<\s*script/i.test(String(html))) throw new Error("No <script> allowed in artifacts — use static HTML/CSS");
        const a = OtArtifacts.add({ name, kind, html, prompt, draft: true });
        if (actions.openArtifact) actions.openArtifact(a.id);
        cluiPushCard({ id: a.id, name: a.name, kind: a.kind });
        return "Artifact \"" + a.name + "\" created as a session DRAFT (id " + a.id + ") — it's open on the canvas and lives as a card in this chat. It is NOT in the workspace Artifacts index until the user saves it (Save button on the card or the page)." + cluiArtifactThemeLint(html);
      },
    },
    {
      name: "update_artifact",
      description: "Update an existing artifact — same html rules as create_artifact (theme vars, no scripts). Pass the artifact id (the open one is in 'Current app state'; others via query_hub kind=artifacts) and the fields to change: html (FULL replacement, not a diff), name, and/or kind. When the user asks to change an artifact they're viewing, read its current html from the context and modify it rather than rebuilding from scratch.",
      input_schema: {
        type: "object",
        properties: {
          id: { type: "string" },
          html: { type: "string", description: "full replacement html fragment" },
          name: { type: "string" },
          kind: { type: "string", enum: ["dashboard", "report", "chart", "table", "view", "note"] },
        },
        required: ["id"],
      },
      run: async ({ id, html, name, kind }) => {
        cluiSetActivity("Updating artifact");
        if (html != null && /<\s*script/i.test(String(html))) throw new Error("No <script> allowed in artifacts");
        const patch = {};
        if (html != null) patch.html = String(html);
        if (name != null) patch.name = String(name);
        if (kind != null) patch.kind = String(kind);
        const a = OtArtifacts.update(id, patch);
        if (!a) throw new Error("No artifact " + id);
        if (actions.openArtifact) actions.openArtifact(a.id);
        cluiPushActionLine("Artifact updated · " + a.name);
        return "Updated \"" + a.name + "\" — the page re-rendered with the new version." + (html != null ? cluiArtifactThemeLint(html) : "");
      },
    },
    {
      name: "delete_artifact",
      description: "Delete an artifact by id. Mutating — asks the user for permission.",
      input_schema: { type: "object", properties: { id: { type: "string" } }, required: ["id"] },
      run: async ({ id }) => {
        const a = OtArtifacts.find(id);
        if (!a) throw new Error("No artifact " + id);
        await gate("destructive", 'Delete artifact “' + a.name + '”?');
        OtArtifacts.remove(id);
        cluiPushActionLine("Artifact deleted · " + a.name);
        return "Deleted \"" + a.name + "\".";
      },
    },
    {
      name: "inspect_ui",
      description: "Inspect the live DOM before editing. Give a CSS selector; returns match count, tag, classes, a text sample, key computed styles of the first match, and its children's class names. Use this to verify selectors before edit_hub.",
      input_schema: { type: "object", properties: { selector: { type: "string" } }, required: ["selector"] },
      run: async ({ selector }) => {
        cluiSetActivity("Inspecting " + selector);
        let els;
        try { els = document.querySelectorAll(selector); } catch (e) { throw new Error("Invalid selector: " + selector); }
        if (!els.length) return JSON.stringify({ matches: 0, hint: "No match — inspect a parent like .app-shell and read childClasses" });
        const el = els[0];
        const cs = getComputedStyle(el);
        const childClasses = [...new Set(Array.prototype.slice.call(el.children).flatMap(k => Array.prototype.slice.call(k.classList)))].slice(0, 20);
        return JSON.stringify({
          matches: els.length,
          tag: el.tagName.toLowerCase(),
          classes: Array.prototype.slice.call(el.classList),
          text: (el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 80),
          computed: { color: cs.color, background: cs.backgroundColor, fontSize: cs.fontSize, padding: cs.padding, borderRadius: cs.borderRadius, display: cs.display },
          childClasses,
        });
      },
    },
    {
      name: "hub_action",
      description: "Run a product action: grade, mint_capsule, replay, watch, or capture — optionally on a target (trace id, run, fleet segment). No CLI or live fleet is attached in this preview, so the result is SIMULATED; tell the user that.",
      input_schema: { type: "object", properties: { action: { type: "string", enum: ["grade", "mint_capsule", "replay", "watch", "capture"] }, target: { type: "string" } }, required: ["action"] },
      run: async ({ action, target }) => {
        cluiSetActivity("Running " + action);
        await new Promise(r => setTimeout(r, 400));
        const t = target ? " on " + target : "";
        const results = {
          grade: "Graded" + t + ": 7/9 verifier claims pass, 2 provisional — B+.",
          mint_capsule: "Capsule minted" + t + ": captured, scrubbed, sealed — 42 steps, 3 claims.",
          replay: "Replay queued" + t + " — deterministic seed pinned.",
          watch: "Watch armed" + t + " — alert fires on survival drop.",
          capture: "Capture session started" + t + " — recording to the bucket.",
        };
        cluiPushActionLine(action + t + " · simulated");
        return (results[action] || ("Did " + action + t + ".")) + " (Simulated — no CLI attached.)";
      },
    },
    {
      name: "edit_hub",
      description: "Change the Hub's UI by writing REAL CSS. The css is injected into the running app immediately and recorded as a checkpoint on a scratch branch; the user can revert to any checkpoint or keep/discard the branch. Verify selectors with inspect_ui first. One coherent change per call. Mutating — may require user permission.",
      input_schema: {
        type: "object",
        properties: {
          change: { type: "string", description: "Short human description of the change" },
          css: { type: "string", description: "Plain CSS applied live to the app. No @import." },
        },
        required: ["change", "css"],
      },
      run: async ({ change, css }) => {
        await gate("writes", "Edit the Hub: " + change + "?");
        cluiSetActivity("Applying edit");
        const branch = CluiEdits.pendingBranch() ||
          ("dev/" + String(change).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 28));
        const cp = CluiEdits.add({ branch, change: String(change), css: String(css), provenance: cluiGetProvenance() });
        cluiPushActionLine("Checkpoint " + cp.id.slice(-4) + " · " + change);
        return "Applied live and checkpointed on " + branch + ". The user sees the change now and can revert this checkpoint or keep/discard the branch from the chip below the chat.";
      },
    },
  ];
  // Count executions so the reply fallback can tell "acted silently"
  // from "nothing happened" (e.g. a truncated tool call).
  return tools.map(t => ({
    ...t,
    run: async (input) => {
      const out = await t.run(input);
      window.__cluiToolRuns = (window.__cluiToolRuns || 0) + 1;
      return out;
    },
  }));
}

// Offline fallback: no window.claude — still demo the two modes
function cluiOfflineReply(text, actions, mode) {
  const t = text.toLowerCase();
  if (mode === "edit") {
    const branch = CluiEdits.pendingBranch() || ("dev/" + t.replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 28));
    CluiEdits.add({ branch, change: text, css: ".topbar { box-shadow: inset 0 -2px 0 rgba(245, 158, 11, 0.55); }" });
    cluiPushActionLine("Checkpoint on " + branch);
    return "Claude isn't reachable, so I staged a placeholder checkpoint (amber topbar accent) on " + branch + " — try revert, keep and discard from the chip below.";
  }
  if (t.includes("chart") || t.includes("plot") || t.includes("graph") || t.includes("dashboard")) {
    cluiSaveArtifact({
      name: "Survival by harness — 30d",
      kind: "chart",
      html: "<div style='background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:18px;display:flex;flex-direction:column;gap:12px'><div style='font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--fg-mute)'>Survival by harness — 30d</div>" +
        [["Claude Code", 86], ["Cursor", 71], ["OpenClaw", 64]].map(r =>
          "<div style='display:grid;grid-template-columns:110px 1fr 44px;gap:12px;align-items:center'><div style='font-size:13px;color:var(--fg-dim)'>" + r[0] + "</div><div style='height:8px;border-radius:4px;background:var(--surface-3)'><div style='height:100%;width:" + r[1] + "%;border-radius:4px;background:var(--c-git)'></div></div><div style='font-family:var(--font-mono);font-size:12px;text-align:right'>" + r[1] + "%</div></div>").join("") +
        "<div style='font-size:11px;color:var(--fg-mute)'>Offline placeholder — numbers are illustrative</div></div>",
      prompt: text,
    });
    cluiPushActionLine("Artifact created · Survival by harness — 30d");
    if (actions.openArtifact) { const list = OtArtifacts.get(); if (list[0]) actions.openArtifact(list[0].id); }
    return "Charted line survival by harness and saved it as an artifact — it's open now and listed under Artifacts. (Claude isn't reachable, so the numbers are placeholders.)";
  }
  if (t.includes("alive") || t.includes("surviv")) {
    actions.navigate("traces");
    return "Opened traces — survival is the right-hand column. Most of yesterday's changes are still alive at HEAD; two died in this morning's refactor.";
  }
  if (t.includes("grade") || t.includes("eval") || t.includes("verifier")) {
    actions.navigate("evals");
    return "Opened evals. The latest run grades against its skill verifier with two claims still provisional — they mature as the world answers.";
  }
  if (t.includes("capsule")) {
    actions.navigate("capsules");
    return "Opened capsules. Minting from a failing trace needs Claude — capture, scrub and seal run locally.";
  }
  for (const p of CLUI_PAGES) {
    if (t.includes(p)) { actions.navigate(p); return "Opened " + p + ". (Claude isn't reachable in this preview — I can still navigate.)"; }
  }
  if (t.includes("compare")) { actions.openCompare(); return "Opened compare. (Claude isn't reachable in this preview.)"; }
  return "Claude isn't reachable in this preview. Try \"chart survival by harness\" or \"open capsules\" — the demo flows still work.";
}

function CluiContextLabel({ context }) {
  const label =
    context.view === "trace" ? "trace " + (context.traceShortId || "") :
    context.view === "repo" ? (context.activeRepoId || "repo") :
    context.view === "dataset" ? (context.activeDatasetId || "dataset") :
    context.view;
  return <span className="clui-ctx-chip" title="What Claude currently sees">{"◉ " + label}</span>;
}

// Inline chat card for a generated artifact draft — the artifact
// lives with the session until the user saves it to the workspace.
function CluiArtifactCard({ card, artifacts, onOpen }) {
  const live = artifacts.find(x => x.id === card.id);
  const save = (e) => { e.stopPropagation(); OtArtifacts.update(card.id, { draft: false }); };
  if (!live) {
    return (
      <div className="clui-art-card gone">
        <Icon name="swatch" size={13} />
        <span className="cac-name">{card.name}</span>
        <span className="cac-kind">removed</span>
      </div>
    );
  }
  return (
    <button className="clui-art-card" data-draft={live.draft ? "true" : "false"} title="Open on the canvas" onClick={() => onOpen(card.id)}>
      <Icon name="swatch" size={13} />
      <span className="cac-name">{live.name}</span>
      <span className="cac-kind">{live.kind}</span>
      <span className="cac-spring"></span>
      {live.draft ? (
        <span className="cac-save" role="button" title="Save to the workspace — appears under Artifacts" onClick={save}>Save</span>
      ) : (
        <span className="cac-saved">✓ saved</span>
      )}
    </button>
  );
}

function CluiBranchChip({ branch, count, open, onToggle, onEnd, pending }) {
  return (
    <div className="clui-branch-chip clui-glass">
      <button className="b-branch" title={open ? "Hide checkpoints" : "Show checkpoints — revert to any point"} onClick={onToggle}>
        <span>⎇ {branch}</span>
        <span className="b-count mono">{count}</span>
      </button>
      {pending && <button className="b-btn keep" title="Keep changes (merge into main — survives reload)" onClick={() => onEnd(true)}>✓</button>}
      {pending && <button className="b-btn discard" title="Discard pending changes" onClick={() => onEnd(false)}>✕</button>}
    </div>
  );
}

// The chat sidebar pane. Always mounted inside .sb-flip; also
// portals the dock pill to <body> while the pane is hidden.
function CluiSidebar({ open, onToggle, context, actions }) {
  const [busy, setBusy] = React.useState(false);
  const [activity, setActivity] = React.useState("");
  const [unread, setUnread] = React.useState(false);
  const [perm, setPerm] = React.useState(null); // {label, resolve}
  const [permMode, setPermMode] = React.useState("ask");
  const [mode, setModeRaw] = React.useState(() => {
    try { return localStorage.getItem("ot-clui-mode") || "use"; } catch (e) { return "use"; }
  });
  const [captured, setCapturedRaw] = React.useState(() => {
    try { return localStorage.getItem("ot-clui-captured") !== "off"; } catch (e) { return true; }
  });
  const [artifacts, setArtifacts] = React.useState(() => OtArtifacts.get());
  React.useEffect(() => OtArtifacts.subscribe(setArtifacts), []);
  // Live-edit checkpoints (CluiEdits owns the CSS + persistence)
  const [editCps, setEditCps] = React.useState(() => CluiEdits.get());
  const [cpOpen, setCpOpen] = React.useState(false);
  React.useEffect(() => CluiEdits.subscribe(setEditCps), []);
  const pendingCps = editCps.filter(c => c.state !== "kept");
  const editSession = pendingCps.length ? { branch: pendingCps[pendingCps.length - 1].branch, count: pendingCps.length } : null;
  const [input, setInput] = React.useState("");
  // ── Sessions: persisted list + active session ──
  const [activeId, setActiveId] = React.useState(() => {
    try { return localStorage.getItem("ot-clui-active") || ("s" + Date.now()); } catch (e) { return "s" + Date.now(); }
  });
  const [sessions, setSessions] = React.useState(() => {
    try { const s = JSON.parse(localStorage.getItem("ot-clui-sessions") || "[]"); return Array.isArray(s) ? s : []; } catch (e) { return []; }
  });
  const [messages, setMessages] = React.useState(() => {
    try {
      const sess = JSON.parse(localStorage.getItem("ot-clui-sessions") || "[]");
      const aid = localStorage.getItem("ot-clui-active");
      const s = Array.isArray(sess) && sess.find(x => x.id === aid);
      return (s && s.messages) || [];
    } catch (e) { return []; }
  });
  const [sessOpen, setSessOpen] = React.useState(false);
  const convRef = React.useRef(null);
  const inputRef = React.useRef(null);
  const permModeRef = React.useRef(permMode);
  permModeRef.current = permMode;
  const modeRef = React.useRef(mode);
  modeRef.current = mode;
  const contextRef = React.useRef(context);
  contextRef.current = context;
  const activeIdRef = React.useRef(null);
  activeIdRef.current = activeId;

  const actionsRef = React.useRef(actions);
  actionsRef.current = actions;
  React.useEffect(() => {
    cluiRegisterCommands({
      openTrace: (...a) => actionsRef.current.openTrace(...a),
      setTraceTab: (...a) => actionsRef.current.setTraceTab(...a),
      openRepo: (...a) => actionsRef.current.openRepo(...a),
      openPull: (...a) => actionsRef.current.openPull(...a),
      runSpotlight: (...a) => actionsRef.current.runSpotlight(...a),
      setTheme: (...a) => actionsRef.current.setTheme(...a),
    });
  }, []);

  const setMode = (m) => {
    setModeRaw(m);
    try { localStorage.setItem("ot-clui-mode", m); } catch (e) {}
  };
  const setCaptured = (on) => {
    setCapturedRaw(on);
    try { localStorage.setItem("ot-clui-captured", on ? "on" : "off"); } catch (e) {}
    setMessages(m => [...m, { role: "action", content: on ? "Session capture on — recording to your bucket" : "Session capture off — this conversation leaves no record" }]);
  };
  const removeArtifact = (id) => OtArtifacts.remove(id);

  React.useEffect(() => {
    // "Edit" on an artifact page: open the panel with the artifact as context
    const onEditArtifact = (e) => {
      const d = e.detail || {};
      if (!open) { const b = document.querySelector(".btn-claude"); if (b) b.click(); }
      setInput('Update the artifact “' + (d.name || "") + '”: ');
      setTimeout(() => inputRef.current && inputRef.current.focus(), 500);
    };
    window.addEventListener("ot-edit-artifact", onEditArtifact);
    return () => {
      window.removeEventListener("ot-edit-artifact", onEditArtifact);
    };
  }, [open]);

  React.useEffect(() => {
    cluiSetActivity = (label) => setActivity(label);
    cluiPushActionLine = (label) => setMessages(m => [...m, { role: "action", content: label }]);
    cluiPushCard = (card) => setMessages(m => [...m, { role: "card", card, content: "[artifact draft: " + card.name + "]" }]);
    cluiGetProvenance = () => ({ capsule_ref: activeIdRef.current, message_ref: (messagesRef.current || []).length });
    cluiSaveArtifact = (a) => OtArtifacts.add(a);
    cluiRequestPermission = (label, force) => {
      if (!force && permModeRef.current === "auto") return Promise.resolve(true);
      return new Promise((resolve) => setPerm({ label, resolve }));
    };
    return () => {
      cluiSetActivity = () => {};
      cluiPushActionLine = () => {};
      cluiPushCard = () => {};
      cluiGetProvenance = () => ({});
      cluiSaveArtifact = () => {};
      cluiRequestPermission = async () => true;
    };
  }, []);

  // Short LLM-summarized session titles, keyed by session id
  const [titles, setTitles] = React.useState(() => {
    try { const t = JSON.parse(localStorage.getItem("ot-clui-titles") || "{}"); return t && typeof t === "object" ? t : {}; } catch (e) { return {}; }
  });
  const titleReqRef = React.useRef(null);
  React.useEffect(() => {
    const firstUser = messages.find(m => m.role === "user" && typeof m.content === "string");
    if (!firstUser || titles[activeId]) return;
    const key = activeId + "|" + firstUser.content;
    if (titleReqRef.current === key) return;
    titleReqRef.current = key;
    if (!(window.claude && window.claude.complete)) return;
    window.claude.complete({
      system: "Summarize the user's request as a short session title: 3-5 plain words. No quotes, no trailing punctuation, no markdown.",
      messages: [{ role: "user", content: String(firstUser.content).slice(0, 400) }],
      max_tokens: 24,
    }).then(r => {
      const t = String(r || "").trim().replace(/^["'\s]+|["'\s.]+$/g, "").split("\n")[0].slice(0, 48);
      if (t && titleReqRef.current === key) {
        setTitles(prev => {
          const next = { ...prev, [activeId]: t };
          try { localStorage.setItem("ot-clui-titles", JSON.stringify(next)); } catch (e) {}
          return next;
        });
      }
    }).catch(() => {});
  }, [messages, activeId, titles]);

  // Persist the active session into the session list
  React.useEffect(() => {
    const firstUser = messages.find(m => m.role === "user");
    const title = titles[activeId] || (firstUser ? String(firstUser.content).slice(0, 60) : "");
    setSessions(prev => {
      const next = prev.filter(s => s.id !== activeId);
      next.unshift({ id: activeId, title, ts: Date.now(), messages: messages.slice(-40) });
      const capped = next.slice(0, 12);
      try {
        localStorage.setItem("ot-clui-sessions", JSON.stringify(capped));
        localStorage.setItem("ot-clui-active", activeId);
      } catch (e) {}
      return capped;
    });
  }, [messages, activeId, titles]);

  React.useEffect(() => {
    const el = convRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, busy, open]);

  // Surface Claude's activity on the shell so the window frame can react
  React.useEffect(() => {
    const shell = document.querySelector(".app-shell");
    if (shell) shell.setAttribute("data-clui-busy", busy ? "true" : "false");
    return () => { if (shell) shell.removeAttribute("data-clui-busy"); };
  }, [busy]);

  // Action lines pushed from outside (window chrome: Save, etc.)
  React.useEffect(() => {
    const onLine = (e) => setMessages(m => [...m, { role: "action", content: String(e.detail || "") }]);
    window.addEventListener("clui-action-line", onLine);
    return () => window.removeEventListener("clui-action-line", onLine);
  }, []);

  // Annotations handed over from the window chrome ("Add to chat") —
  // arrive as a user message where each note points at its source file.
  React.useEffect(() => {
    const annotsToText = (items) => items.map(a =>
      a.kind === "pin"
        ? "#" + a.n + " \"" + a.text + "\"" + (a.label ? " (" + a.label + ")" : "") + " \u2192 " + a.file
        : a.n + " sketch mark" + (a.n > 1 ? "s" : "") + " \u2192 " + a.file
    ).join("\n");
    const onAnnots = async (e) => {
      const d = e.detail || {};
      const items = d.items || [];
      if (!items.length) return;
      const asText = annotsToText(items);
      setMessages(m => [...m, { role: "user", kind: "annots", annots: items, content: asText }]);
      setBusy(true);
      setActivity("Reading annotations");
      let reply;
      try {
        if (window.claude && window.claude.complete) {
          reply = await window.claude.complete({
            system: cluiSystemPrompt(contextRef.current, modeRef.current),
            messages: [{ role: "user", content: "I annotated the view I'm looking at (" + (d.ctx || "") + "). Each note points at the source file to change:\n" + asText + "\nAcknowledge briefly and say what you'd change in those files. Don't make changes yet." }],
            max_tokens: 400,
          });
        } else {
          await new Promise(r => setTimeout(r, 700));
          const files = [...new Set(items.map(i => i.file))];
          reply = "Got it \u2014 " + items.length + " note" + (items.length > 1 ? "s" : "") + " on " + files.join(", ") + ". Say \"apply them\" and I'll stage the edits in a dev session.";
        }
      } catch (err) {
        reply = "Couldn't read the annotations: " + (err && err.message ? err.message : String(err));
      }
      setMessages(m => [...m, { role: "assistant", content: reply }]);
      setBusy(false);
      setActivity("");
    };
    window.addEventListener("clui-annotations", onAnnots);
    return () => window.removeEventListener("clui-annotations", onAnnots);
  }, []);

  // ⌘J flips, Esc slides back to the nav
  React.useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "j" || e.key === "J")) {
        e.preventDefault();
        onToggle();
      } else if (e.key === "Escape" && open && !perm && !window.__cluiAnnotMode && !window.__cluiMenuOpen) {
        onToggle();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, perm, onToggle]);

  React.useEffect(() => {
    if (open) {
      setUnread(false);
      const t = setTimeout(() => inputRef.current && inputRef.current.focus(), 460);
      return () => clearTimeout(t);
    }
  }, [open]);

  const sendSeq = React.useRef(0);
  const send = async (textArg) => {
    const text = String(textArg != null ? textArg : input).trim();
    if (!text || busy) return;
    const token = ++sendSeq.current;
    setInput("");
    if (inputRef.current) inputRef.current.style.height = "auto";
    setMessages(m => [...m, { role: "user", content: text }]);
    setBusy(true);
    setActivity("Thinking");

    let reply;
    try {
      if (window.claude && window.claude.complete) {
        const history = messages
          .filter(m => m.role === "user" || m.role === "assistant")
          .slice(-12)
          .map(m => ({ role: m.role, content: m.content }));
        window.__cluiToolRuns = 0;
        reply = await window.claude.complete({
          system: cluiSystemPrompt(contextRef.current, mode),
          messages: [...history, { role: "user", content: text }],
          tools: cluiBuildTools(actions),
          max_tokens: 8000,
        });
      } else {
        await new Promise(r => setTimeout(r, 500));
        reply = cluiOfflineReply(text, actions, mode);
      }
    } catch (e) {
      reply = "Something went wrong: " + (e && e.message ? e.message : String(e));
    }

    if (sendSeq.current !== token) return; // interrupted or superseded
    if (reply == null || !String(reply).trim()) {
      reply = (window.__cluiToolRuns || 0) > 0
        ? "Done — see the actions above."
        : "That didn't go through — the response was cut off before I could act. Try again, or break the request into a smaller step.";
    }
    setMessages(m => [...m, { role: "assistant", content: reply }]);
    setBusy(false);
    setActivity("");
    setPerm(null);
    if (!open) setUnread(true);
    return reply;
  };

  // Programmatic drive hook — lets tests (and the window chrome)
  // exercise the real pipeline: send a message, poke checkpoints.
  const sendRef = React.useRef(null);
  sendRef.current = send;
  const messagesRef = React.useRef(messages);
  messagesRef.current = messages;
  React.useEffect(() => {
    window.__cluiDrive = {
      send: (t) => (sendRef.current ? sendRef.current(t) : Promise.resolve(null)),
      edits: CluiEdits,
      setPermMode: (m) => setPermMode(m),
      setMode: (m) => setMode(m),
      getMessages: () => messagesRef.current,
      hasClaude: () => !!(window.claude && window.claude.complete),
    };
    return () => { delete window.__cluiDrive; };
  }, []);

  const interrupt = () => {
    sendSeq.current++;
    setBusy(false);
    setActivity("");
    if (perm) { perm.resolve(false); setPerm(null); }
    setMessages(m => [...m, { role: "system", content: "interrupted" }]);
  };

  const answerPerm = (ok) => {
    if (perm) { perm.resolve(ok); setPerm(null); }
  };

  const endEditSession = (keep) => {
    const b = editSession && editSession.branch;
    setCpOpen(false);
    if (keep) CluiEdits.keepAll(); else CluiEdits.discardPending();
    setMessages(m => [...m, {
      role: "system",
      content: keep ? "merged " + b + " into main — edits persist across reloads" : "discarded " + b + " — app restored",
    }]);
  };

  const revertToCheckpoint = (id) => {
    const n = CluiEdits.revertTo(id);
    setCpOpen(false);
    setMessages(m => [...m, {
      role: "action",
      content: (id == null ? "Reverted to baseline" : "Rolled back") + " — removed " + n + " checkpoint" + (n === 1 ? "" : "s"),
    }]);
  };

  const newChat = () => {
    setSessOpen(false);
    if (messages.length === 0) return;
    setActiveId("s" + Date.now());
    setMessages([]);
    setInput("");
    requestAnimationFrame(() => inputRef.current && inputRef.current.focus());
  };

  const selectSession = (id) => {
    setSessOpen(false);
    if (id === activeId) return;
    const s = sessions.find(x => x.id === id);
    if (!s) return;
    setActiveId(id);
    setMessages(s.messages || []);
  };

  const sessionTitle = (() => {
    if (titles[activeId]) return titles[activeId];
    const firstUser = messages.find(m => m.role === "user");
    return firstUser ? String(firstUser.content) : "New session";
  })();

  // Publish session meta for the window chrome
  React.useEffect(() => {
    window.dispatchEvent(new CustomEvent("clui-meta", { detail: { title: sessionTitle, busy, activity } }));
  }, [sessionTitle, busy, activity]);
  const pastSessions = sessions.filter(s => (s.messages && s.messages.length > 0)).slice(0, 10);

  const pillLabel = null; // dock pill removed — sidebar + edge handle are the entry points

  return (
    <div className="clui-side" data-comment-anchor="clui-island">
      {editSession && ReactDOM.createPortal(<div className="clui-edit-ring"></div>, document.body)}

      <div className="clui-side-head">
        <span className="clui-spark" style={{ fontSize: 15 }}>✳</span>
        <div className="clui-session-title" title={sessionTitle}>{sessionTitle}</div>
        <div className="clui-head-spacer"></div>
        <button className="clui-icon-btn" title="Sessions — previous chats or start new" onClick={() => setSessOpen(o => !o)}>
          <Icon name="conversation" size={15} />
        </button>
        {sessOpen && (
          <React.Fragment>
            <div className="clui-pop-backdrop" onClick={() => setSessOpen(false)}></div>
            <div className="clui-sessions-pop clui-glass">
              <button className="sess-item sess-new" onClick={newChat}>
                <Icon name="plus" size={13} />
                <span>New chat</span>
              </button>
              {pastSessions.length > 0 && <div className="sess-div"></div>}
              {pastSessions.map(s => (
                <button key={s.id} className="sess-item" data-active={s.id === activeId} onClick={() => selectSession(s.id)}>
                  <span className="t">{s.title || "New session"}</span>
                </button>
              ))}
            </div>
          </React.Fragment>
        )}
      </div>

      <div className="clui-side-conv" ref={convRef}>
        {messages.length === 0 && !busy && (
          <div className="clui-empty">
            {mode === "edit" ? (
              <React.Fragment>
                <div className="ce-lbl">Editing the Hub — real CSS edits, applied live and checkpointed. Revert, keep or discard from the branch chip.</div>
                <div className="ce-chips">
                  {[
                    "Give the topbar an amber accent",
                    "Round off every corner in the app",
                    "Make the sidebar more compact",
                  ].map(s => (
                    <button className="ce-chip" key={s} onClick={() => { setInput(s); inputRef.current && inputRef.current.focus(); }}>{s}</button>
                  ))}
                </div>
              </React.Fragment>
            ) : (
              <React.Fragment>
                <div className="ce-lbl">Claude drives the Hub for you — capture, grading, replay, watching. Try:</div>
                <div className="ce-chips">
                  {[
                    "Which of yesterday's changes are still alive?",
                    "Build me a dashboard of PR review load",
                    "Mint a capsule from the failing trace",
                    "Chart survival by harness",
                  ].map(s => (
                    <button className="ce-chip" key={s} onClick={() => { setInput(s); inputRef.current && inputRef.current.focus(); }}>{s}</button>
                  ))}
                </div>
                <div className="ce-foot">Anything Claude builds lives in this chat as a draft — save it to keep it under Artifacts.</div>
              </React.Fragment>
            )}
          </div>
        )}
        {messages.map((m, i) => (
          m.role === "action" ? (
            <div className="clui-action-line" key={i}><span className="tick">✓</span>{m.content}</div>
          ) : m.role === "card" ? (
            <CluiArtifactCard key={i} card={m.card} artifacts={artifacts} onOpen={(id) => actions.openArtifact && actions.openArtifact(id)} />
          ) : m.kind === "annots" ? (
            <div className="clui-msg" data-role="user" key={i}>
              <div className="body annots">
                {(m.annots || []).map((a, j) => (
                  <div className="annot-row" key={j}>
                    <div className="a-body">
                      {a.kind === "pin" ? <span className="a-dot">{a.n}</span> : <span className="a-ink">✎</span>}
                      <span className="a-text">{a.kind === "pin" ? a.text : a.n + " sketch mark" + (a.n > 1 ? "s" : "")}</span>
                    </div>
                    <div className="a-chip" title={(a.label ? a.label + " · " : "") + a.file}>
                      {a.label ? <span className="a-chip-el">{a.label}</span> : null}
                      {a.label ? <span className="a-chip-sep">·</span> : null}
                      <span className="a-chip-file">{a.file}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="clui-msg" data-role={m.role} key={i}>
              {m.role === "assistant" && <div className="who">Claude</div>}
              <div className="body">{m.content}</div>
            </div>
          )
        ))}
        {busy && (
          <div className="clui-thinking"><span className="clui-dot"></span>{(activity || "Thinking") + "…"}</div>
        )}
      </div>

      {editCps.length > 0 && (
        <div className="clui-side-branch">
          <CluiBranchChip
            branch={editSession ? editSession.branch : "main"}
            count={editCps.length}
            open={cpOpen}
            onToggle={() => setCpOpen(o => !o)}
            onEnd={endEditSession}
            pending={!!editSession}
          />
          {cpOpen && (
            <CluiCheckpointTimeline
              checkpoints={editCps}
              onRevert={revertToCheckpoint}
              onClose={() => setCpOpen(false)}
              onSeal={() => {
                const cap = CluiEdits.sealAsCapsule({ sessions: [activeId] });
                setCpOpen(false);
                if (cap) setMessages(m => [...m, { role: "action", content: "Sealed " + cap.cid + " · " + cap.checkpoints.length + " change" + (cap.checkpoints.length > 1 ? "s" : "") + " on " + cap.base_version + " — hub_change feedback capsule, ready to share upstream" }]);
              }}
            />
          )}
        </div>
      )}

      {perm && (
        <div className="clui-perm-float">
          <div className="clui-perm-card clui-glass">
            <div className="q">
              {perm.label}
              <span className="hint">Claude wants to make a change — allow it?</span>
            </div>
            <button className="clui-perm-btn allow" title="Allow" onClick={() => answerPerm(true)}>✓</button>
            <button className="clui-perm-btn deny" title="Deny" onClick={() => answerPerm(false)}>✕</button>
          </div>
        </div>
      )}

      <CluiComposer
        value={input}
        onChange={setInput}
        inputRef={inputRef}
        busy={busy}
        onSend={send}
        onInterrupt={interrupt}
        permMode={permMode}
        onPermMode={setPermMode}
        mode={mode}
        onMode={setMode}
        artifacts={artifacts.filter(a => !a.draft)}
        onRemoveArtifact={removeArtifact}
        captured={captured}
        onCaptured={setCaptured}
        traceAddr={"trace:" + String(activeId).slice(0, 8) + "…/s" + Math.max(1, messages.filter(m => m.role === "user" || m.role === "assistant").length)}
      />
    </div>
  );
}

// Persistent switcher below the panes — dock-style magnification:
// the active mode's button is wide (icon + label), the other shrinks
// to an icon; selecting the small one flips the panes and the widths.
// When the sidebar is collapsed to a rail (and Claude is closed) the
// same two buttons morph into a vertical stack — opening Claude from
// the rail widens the column and morphs them back horizontal in one
// continuous motion. The panel button in rail mode re-expands the nav.
function CluiSwitch({ open, onSelect, collapsed, onSetCollapsed }) {
  const rail = collapsed && !open;
  // The menu segment is the sidebar's window control, cycling its
  // three states: claude-open → menu, menu → rail, rail → menu.
  const clickMenu = () => {
    if (rail) onSetCollapsed(false);
    else if (open) onSelect("menu");
    else onSetCollapsed(true);
  };
  const clickClaude = () => {
    if (open) { onSelect("menu"); onSetCollapsed(true); }
    else { onSelect("claude"); onSetCollapsed(false); }
  };
  const menuTitle = rail ? "Expand sidebar (⌘B)" : open ? "Menu (⌘J)" : "Collapse to rail (⌘B)";
  return (
    <div className="clui-switch" data-rail={rail ? "true" : "false"}>
      <button
        className="clui-switch-btn btn-claude"
        data-active={open}
        onClick={clickClaude}
        title={open ? "Collapse to rail (⌘B)" : "Claude (⌘J)"}
      >
        <span className="ic ic-swap">
          <span className="i-a clui-spark" style={{ fontSize: 15 }}>✳</span>
          <span className="i-b"><Icon name="chevron-left" size={15} /></span>
        </span>
        <span className="lbl lbl-swap">
          <span className="t-a">Claude</span>
          <span className="t-b">Collapse</span>
        </span>
      </button>
      <button
        className="clui-switch-btn btn-menu"
        data-active={!open}
        onClick={clickMenu}
        title={menuTitle}
      >
        <span className="ic ic-swap">
          <span className="i-a"><Icon name="panel" size={15} /></span>
          <span className="i-b"><Icon name="chevron-left" size={15} /></span>
        </span>
        <span className="lbl lbl-swap">
          <span className="t-a">Menu</span>
          <span className="t-b">Collapse</span>
        </span>
      </button>
    </div>
  );
}

Object.assign(window, { CluiSidebar, CluiSwitch });
