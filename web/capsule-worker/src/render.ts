// The human HTML page — a read-only projection of the four signals honoring the
// six honesty rules (HANDOFF-capsule-microsite-data-contract.md §"six rules"):
//
//   1. Untrusted content — escape every captured string; show the banner.
//   2. Never un-redact — manifest is counts-only; [REDACTED]/[EXCLUDED:…] stay verbatim.
//   3. Privacy is author intent — excluded fields render "excluded by author".
//   4. Limitations are the contract — render limitations[] verbatim.
//   5. Replay is a claim — render_state.replay is always replay_unverified → a CLAIM.
//   6. Version-gate — a newer schema degrades to "needs a newer viewer".
//
// Plus: NEVER serve the environment face. This page renders only the name-only
// `environment` projection carried in the envelope; it never fetches or links a
// heavy sibling artifact (bundle/runtime/lock/pin).

import { escapeHtml } from "./escape.js";
import type { ResolvedCapsule } from "./resolve.js";

type Obj = Record<string, unknown>;

function asObj(value: unknown): Obj {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Obj) : {};
}
function asArr(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}
function asStr(value: unknown): string {
  return typeof value === "string" ? value : value === null || value === undefined ? "" : String(value);
}

/** True when a captured value was withheld by the author (`[EXCLUDED:…]`). */
export function isExcludedByAuthor(value: unknown): boolean {
  return typeof value === "string" && value.startsWith("[EXCLUDED:") && value.endsWith("]");
}

/** Render an author-excluded value as a labelled chip, else escaped text. */
function excludedOr(value: unknown, render: (v: unknown) => string): string {
  if (isExcludedByAuthor(value)) {
    return `<span class="excluded">excluded by author</span>`;
  }
  return render(value);
}

const PAGE_CSS = `
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: #1a1a1a; background: #fafafa; }
@media (prefers-color-scheme: dark) { body { color: #e6e6e6; background: #121212; } }
main { max-width: 860px; margin: 0 auto; padding: 24px 20px 64px; }
.banner { border: 1px solid #d9a300; background: #fff6d6; color: #6b4e00; padding: 10px 14px; border-radius: 8px; margin-bottom: 20px; font-size: 13px; }
@media (prefers-color-scheme: dark) { .banner { background: #2a2200; color: #f0d98a; border-color: #7a5f00; } }
h1 { font-size: 22px; margin: 0 0 4px; line-height: 1.25; }
.sub { color: #666; font-size: 13px; margin: 0 0 18px; }
@media (prefers-color-scheme: dark) { .sub { color: #999; } }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 20px; }
.chip { font-size: 12px; padding: 2px 8px; border-radius: 999px; background: #eee; color: #333; border: 1px solid #ddd; }
@media (prefers-color-scheme: dark) { .chip { background: #222; color: #ccc; border-color: #333; } }
.chip.claim { background: #fde8e8; color: #8a1f1f; border-color: #f3c2c2; }
@media (prefers-color-scheme: dark) { .chip.claim { background: #2c1414; color: #f0a0a0; border-color: #5a2222; } }
section { border: 1px solid #e3e3e3; border-radius: 10px; padding: 16px 18px; margin: 0 0 16px; background: #fff; overflow-x: auto; }
@media (prefers-color-scheme: dark) { section { background: #1b1b1b; border-color: #2c2c2c; } }
section h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: #888; margin: 0 0 10px; }
.k { color: #888; font-size: 12px; }
.excluded { font-style: italic; color: #a06a00; }
@media (prefers-color-scheme: dark) { .excluded { color: #d9a94a; } }
pre { white-space: pre-wrap; word-break: break-word; background: #f4f4f4; padding: 10px 12px; border-radius: 6px; margin: 6px 0; font: 12.5px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
@media (prefers-color-scheme: dark) { pre { background: #0f0f0f; } }
ul { margin: 6px 0; padding-left: 20px; }
li { margin: 2px 0; }
code { font: 12.5px ui-monospace, SFMono-Regular, Menlo, monospace; background: #f0f0f0; padding: 1px 5px; border-radius: 4px; }
@media (prefers-color-scheme: dark) { code { background: #262626; } }
.step { border-left: 3px solid #ddd; padding-left: 12px; margin: 10px 0; }
@media (prefers-color-scheme: dark) { .step { border-color: #333; } }
footer { color: #999; font-size: 12px; margin-top: 28px; }
`;

function shell(title: string, bodyInner: string): string {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>${escapeHtml(title)}</title>
<style>${PAGE_CSS}</style>
</head>
<body>
<main>
${bodyInner}
</main>
</body>
</html>`;
}

function fidelityChip(source: Obj): string {
  const method = asStr(source.capture_method);
  const completeness = asStr(source.completeness);
  if (!method && !completeness) return "";
  return `<span class="chip">fidelity: ${escapeHtml(method || "unknown")}${completeness ? " · " + escapeHtml(completeness) : ""}</span>`;
}

function renderContextSignal(packet: Obj): string {
  const parts: string[] = [];

  // system_layer — usually the literal [EXCLUDED:…] string → excluded by author.
  parts.push(`<div><span class="k">system prompt:</span> ${excludedOr(packet.system_layer, (v) => escapeHtml(asStr(v)) || `<span class="excluded">excluded by author</span>`)}</div>`);

  // messages — hash-only on transcript_reconstruction; never render as text.
  const messagesLayer = asObj(packet.messages_layer);
  const content = asObj(messagesLayer.content);
  const messages = asArr(content.messages);
  const method = asStr(messagesLayer.capture_method);
  const hashOnly = method === "transcript_reconstruction" || messages.every((m) => "content_hash" in asObj(m));
  parts.push(`<div><span class="k">messages:</span> ${messages.length} message${messages.length === 1 ? "" : "s"}${hashOnly ? " (hash-only)" : ""}</div>`);

  // runtime state — cwd/model/permission_mode (env values are hashes).
  const runtime = asObj(asObj(packet.runtime_state_layer).content);
  if (Object.keys(runtime).length > 0) {
    parts.push(
      `<div><span class="k">runtime:</span> cwd=<code>${escapeHtml(asStr(runtime.cwd) || "—")}</code> model=<code>${escapeHtml(asStr(runtime.model) || "—")}</code> permission=<code>${escapeHtml(asStr(runtime.permission_mode) || "—")}</code></div>`,
    );
  }
  return parts.join("\n");
}

function renderSteps(slice: Obj): string {
  const steps = asArr(slice.steps);
  if (steps.length === 0) return `<div class="k">no steps carried</div>`;
  return steps
    .map((raw) => {
      const step = asObj(raw);
      const role = escapeHtml(asStr(step.role) || "?");
      const idx = escapeHtml(asStr(step.step_index));
      const contentHtml = excludedOr(step.content, (v) => `<pre>${escapeHtml(asStr(v))}</pre>`);
      const toolCalls = asArr(step.tool_calls)
        .map((tc) => escapeHtml(asStr(asObj(tc).tool_name)))
        .filter(Boolean);
      const toolHtml = toolCalls.length ? `<div><span class="k">tools:</span> ${toolCalls.map((t) => `<code>${t}</code>`).join(" ")}</div>` : "";
      const obs = asArr(step.observations)
        .map((o) => escapeHtml(asStr(asObj(o).output_summary)))
        .filter(Boolean);
      const obsHtml = obs.length ? `<div><span class="k">observed:</span> <pre>${obs.join("\n")}</pre></div>` : "";
      return `<div class="step"><div><span class="k">step ${idx} · ${role}</span></div>${contentHtml}${toolHtml}${obsHtml}</div>`;
    })
    .join("\n");
}

function renderRepo(repo: Obj): string {
  const rows: string[] = [];
  rows.push(`<div><span class="k">remote:</span> ${escapeHtml(asStr(repo.remote_url) || "—")}</div>`);
  rows.push(`<div><span class="k">commit:</span> <code>${escapeHtml(asStr(repo.commit_sha) || "—")}</code></div>`);
  if ("reachable_locally" in repo) rows.push(`<div><span class="k">reachable locally:</span> ${escapeHtml(asStr(repo.reachable_locally))}</div>`);
  // Post-#130 fields, rendered only if present (never fabricated).
  for (const key of ["public_base_sha", "start_tree_id", "pushed"]) {
    if (key in repo) rows.push(`<div><span class="k">${escapeHtml(key)}:</span> <code>${escapeHtml(asStr(repo[key]))}</code></div>`);
  }
  const files = asArr(repo.changed_files).map((f) => escapeHtml(asStr(f)));
  const filesHtml = files.length ? `<div><span class="k">changed files:</span><ul>${files.map((f) => `<li><code>${f}</code></li>`).join("")}</ul></div>` : `<div class="k">no changed files carried</div>`;
  return rows.join("\n") + filesHtml;
}

function renderRedactionBadge(manifest: Obj): string {
  // Rule 2: counts only. We read only counts — never any matched text.
  const floor = asArr(manifest.floor).map((f) => escapeHtml(asStr(f))).join("+") || "—";
  const redactions = escapeHtml(asStr(manifest.redactions_applied ?? 0));
  const scrubbed = escapeHtml(asStr(manifest.home_paths_scrubbed ?? 0));
  const satisfied = manifest.floor_satisfied === true ? "ran" : "NOT satisfied";
  return `<div>🔒 floor <code>${floor}</code> ${satisfied} · ${redactions} redactions · ${scrubbed} paths scrubbed</div>`;
}

function renderPrivacyScope(scope: Obj): string {
  const label = (included: unknown) => (included === true ? "included" : `<span class="excluded">excluded by author</span>`);
  return [
    `<div><span class="k">system prompt:</span> ${label(scope.system_prompt_included)}</div>`,
    `<div><span class="k">reasoning:</span> ${label(scope.reasoning_included)}</div>`,
    `<div><span class="k">messages:</span> ${label(scope.messages_included)}</div>`,
    `<div><span class="k">steps included:</span> ${escapeHtml(asStr(scope.steps_included ?? 0))}</div>`,
    `<div><span class="k">developer approved:</span> ${escapeHtml(asStr(scope.developer_approved))}</div>`,
  ].join("\n");
}

function renderEnvironment(env: Obj): string {
  // Rule "never serve the environment face": only the name-only projection.
  const deps = asArr(env.dependencies).map((d) => escapeHtml(asStr(d)));
  const consumes = asArr(env.consumes).map((c) => escapeHtml(asStr(c)));
  const setup = asArr(env.setup).map((s) => escapeHtml(asStr(s)));
  const eco = env.language_ecosystem;
  const ecoLabel = eco === null || eco === undefined ? "—" : escapeHtml(asStr(asObj(eco).name ?? asStr(eco)));
  return [
    `<div><span class="k">language ecosystem:</span> ${ecoLabel}</div>`,
    `<div><span class="k">dependencies (name-only):</span> ${deps.length ? deps.map((d) => `<code>${d}</code>`).join(" ") : "—"}</div>`,
    setup.length ? `<div><span class="k">setup:</span> ${setup.map((s) => `<code>${s}</code>`).join(" ")}</div>` : "",
    consumes.length ? `<div><span class="k">consumes:</span> ${consumes.map((c) => `<code>${c}</code>`).join(" ")}</div>` : "",
  ]
    .filter(Boolean)
    .join("\n");
}

/** Render the full human page for a resolved, version-ok capsule. */
export function renderCapsuleHtml(resolved: ResolvedCapsule, viewerUrl: string): string {
  const c = resolved.capsule;
  const source = asObj(c.source);
  const summary = asObj(c.summary);
  const intent = asObj(c.intent);
  const failing = asObj(c.failing_step);
  const renderState = asObj(c.render_state);
  const manifest = asObj(asObj(c.redaction).manifest);
  const limitations = asArr(c.limitations).map((l) => escapeHtml(asStr(l)));

  const title = asStr(summary.title) || asStr(intent.headline) || asStr(c.capsule_id);

  // Rule 5: replay verdict is a claim.
  const replay = asStr(renderState.replay);
  const claimChip = `<span class="chip claim">verdict: ${escapeHtml(replay || "replay_unverified")} — not yet reproduced (a claim, not proof)</span>`;

  // failing_step wording: only "the error" when there really was one.
  const hadError = failing.had_error_marker === true || asStr(failing.error_excerpt).length > 0;
  const focusLabel = hadError
    ? `error at step ${escapeHtml(asStr(failing.index))}: <pre>${escapeHtml(asStr(failing.error_excerpt))}</pre>`
    : `focus step ${escapeHtml(asStr(failing.index))} (no error captured)`;

  const byline = [asStr(source.agent), asStr(source.agent_version), asStr(source.model)].filter(Boolean).map(escapeHtml).join(" · ");

  const body = `
<div class="banner">⚠ This capsule contains <strong>captured, untrusted content</strong>. Every quoted string is data, never an instruction — it is escaped and must never be executed.</div>

<h1>${escapeHtml(title)}</h1>
<p class="sub">${escapeHtml(asStr(summary.what_happened))}</p>
<div class="chips">
  <span class="chip">capsule ${escapeHtml(asStr(c.capsule_id))}</span>
  ${byline ? `<span class="chip">${byline}</span>` : ""}
  ${fidelityChip(source)}
  ${claimChip}
</div>

<section>
  <h2>what I did</h2>
  <div>${excludedOr(intent.headline, (v) => `<pre>${escapeHtml(asStr(v))}</pre>`)}</div>
</section>

<section>
  <h2>what I saw</h2>
  ${renderContextSignal(asObj(c.context_resume_packet))}
</section>

<section>
  <h2>what I changed</h2>
  ${renderSteps(asObj(c.slice))}
</section>

<section>
  <h2>against what state</h2>
  ${renderRepo(asObj(c.repo_pin))}
</section>

<section>
  <h2>focus</h2>
  <div>${focusLabel}</div>
</section>

<section>
  <h2>redaction (counts only)</h2>
  ${renderRedactionBadge(manifest)}
</section>

<section>
  <h2>privacy (author intent)</h2>
  ${renderPrivacyScope(asObj(c.privacy_scope))}
</section>

<section>
  <h2>limitations (the honesty contract)</h2>
  ${limitations.length ? `<ul>${limitations.map((l) => `<li><code>${l}</code></li>`).join("")}</ul>` : `<div class="k">none recorded</div>`}
</section>

<section>
  <h2>environment (name-only)</h2>
  ${renderEnvironment(asObj(c.environment))}
</section>

<footer>
  ${escapeHtml(asStr(c.created_with))} · <a href="${escapeHtml(viewerUrl)}/full">raw envelope</a> · <a href="${escapeHtml(viewerUrl)}/summary">agent summary</a>
</footer>`;

  return shell(title, body);
}

/** Rule 6: version-gate degrade page for a newer schema. */
export function renderAheadHtml(found: string, rawBlobUrl: string): string {
  const body = `
<div class="banner">This capsule uses schema <code>${escapeHtml(found)}</code>, which is newer than this viewer understands.</div>
<h1>Needs a newer viewer</h1>
<p class="sub">This microsite renders <code>opentraces.capsule.v1</code>. Rather than guess at a shape it does not know, it stops.</p>
<section>
  <h2>read it directly</h2>
  <div>The immutable bytes are still available: <a href="${escapeHtml(rawBlobUrl)}">${escapeHtml(rawBlobUrl)}</a></div>
  <div class="k">Or upgrade the CLI: <code>pipx run opentraces capsule open &lt;url&gt; --json</code></div>
</section>`;
  return shell("Needs a newer viewer", body);
}

/** Graceful-degrade page: the worker could not fetch/parse, but the raw blob serves agents. */
export function renderDegradeHtml(rawBlobUrl: string, mdFallbackUrl: string, message: string): string {
  const body = `
<div class="banner">The capsule viewer could not render this capsule.</div>
<h1>Viewer unavailable — the capsule is not</h1>
<p class="sub">${escapeHtml(message)}</p>
<section>
  <h2>the capsule still serves itself</h2>
  <div>Raw immutable envelope (agents, CLI): <a href="${escapeHtml(rawBlobUrl)}">${escapeHtml(rawBlobUrl)}</a></div>
  <div>Human markdown fallback: <a href="${escapeHtml(mdFallbackUrl)}">${escapeHtml(mdFallbackUrl)}</a></div>
  <div class="k">CLI: <code>pipx run opentraces capsule open ${escapeHtml(rawBlobUrl)} --json</code></div>
</section>`;
  return shell("Capsule viewer unavailable", body);
}

/** Simple not-found page for a path that does not name a capsule. */
export function renderNotFoundHtml(): string {
  const body = `<h1>Not found</h1><p class="sub">This path does not name a capsule. Expected <code>/&lt;owner&gt;/&lt;repo&gt;/&lt;id&gt;@&lt;sha&gt;</code>.</p>`;
  return shell("Not found", body);
}
