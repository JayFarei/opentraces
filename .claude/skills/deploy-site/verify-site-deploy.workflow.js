// verify-site-deploy.workflow.js
//
// A frozen JIT harness (see kb concept-jit-harnesses-generic): the planner (the
// agent running /deploy-site) scopes THIS run via args, the Workflow runtime
// executes deterministically, and a SMALL number of workers run bounded,
// mechanical browser sweeps that return against typed schemas. State lives in
// this artifact's variables, never in a reasoner's head.
//
// Design notes (why it's light — tuned 2026-06-09 after the first heavy run):
//   - ONE worker per VIEWPORT (not per route x viewport). 2 workers, not ~18.
//   - Each worker runs a single DETERMINISTIC bash batch (curl + a fixed set of
//     agent-browser calls per route) and returns the parsed JSON. No open-ended
//     "verify everything" prompt, so cheap models don't loop.
//   - Each batch opens ONE browser session and `close --all`s it at the end, so
//     Chrome processes don't pile up.
//   - classify-and-route: `changed` routes get a full browser check; unchanged
//     routes are a cheap `curl` status only. A scoped deploy stays cheap.
//   - Failures get an independent adversarial re-check (clean session, default
//     model) so an agent-browser fumble can't masquerade as a site bug.
//
// Run it (ultracode / Workflow tool):
//   Workflow({ scriptPath: '.../deploy-site/verify-site-deploy.workflow.js',
//              args: { baseUrl, changed, pagesOnly } })
//
// args (all optional):
//   baseUrl    string    default 'https://www.opentraces.ai'
//   routes     string[]  override the route list
//   viewports  [{name,w,h}]  override the viewport list
//   changed    string[]  routes touched by THIS deploy -> deep browser check;
//                        all others are curl-only. Omit to deep-check everything.
//   pagesOnly  boolean   skip the email round-trip (read-only validation runs)
//   adminTokenEnv string env var holding the waitlist admin token (default WAITLIST_ADMIN_TOKEN)

export const meta = {
  name: 'verify-site-deploy',
  description: 'JIT harness: verify opentraces.ai across routes x viewports + email capture after a deploy',
  phases: [
    { title: 'Pages', detail: 'one deterministic browser sweep worker per viewport' },
    { title: 'Confirm', detail: 'independent skeptic re-checks any flagged failure' },
    { title: 'Email', detail: 'waitlist round-trip + dedup + honeypot, then clean up' },
  ],
}

const BROWSER = '/opt/homebrew/bin/agent-browser'
const BASE = (args && args.baseUrl) || 'https://www.opentraces.ai'
const PAGES_ONLY = !!(args && args.pagesOnly)
const ADMIN_ENV = (args && args.adminTokenEnv) || 'WAITLIST_ADMIN_TOKEN'

const ROUTES = (args && args.routes) || [
  '/', '/explorer', '/hub', '/schema', '/docs',
  '/schema/0.6.0', '/docs/getting-started/installation',
  '/docs/cli/commands', '/docs/schema/trace-record',
]
const VIEWPORTS = (args && args.viewports) || [
  { name: 'desktop', w: 1440, h: 900 },
  { name: 'mobile', w: 390, h: 844 },
]

// Classify-and-route (deterministic control flow the runtime owns).
const changed = (args && args.changed) || null
const depthFor = (r) => (!changed ? 'deep' : changed.includes(r) ? 'deep' : 'smoke')
const slug = (r) => r.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '') || 'home'

const PAGE_VERDICT = {
  type: 'object',
  required: ['route', 'viewport', 'httpOk', 'pass'],
  properties: {
    route: { type: 'string' },
    viewport: { type: 'string' },
    httpOk: { type: 'boolean' },
    http: { type: 'number' },
    errorCount: { type: 'number' },
    overflow: { type: 'boolean' },
    pass: { type: 'boolean' },
    issues: { type: 'array', items: { type: 'string' } },
  },
}
const VP_SWEEP = {
  type: 'object',
  required: ['pages'],
  properties: { pages: { type: 'array', items: PAGE_VERDICT } },
}
const EMAIL_VERDICT = {
  type: 'object',
  required: ['roundTripOk', 'dedupOk', 'cleanedUp', 'pass'],
  properties: {
    roundTripOk: { type: 'boolean' },
    dedupOk: { type: 'boolean' },
    honeypotOk: { type: 'boolean' },
    cleanedUp: { type: 'boolean' },
    countBefore: { type: 'number' },
    countAfter: { type: 'number' },
    pass: { type: 'boolean' },
    issues: { type: 'array', items: { type: 'string' } },
  },
}

// Build the deterministic one-line bash batch for a whole viewport. It emits a
// JSON array (one object per route). `deep` routes get a real browser open +
// console/overflow/screenshot; `smoke` routes are a curl status only.
const sweepCmd = (vp) => {
  const specs = ROUTES.map((r) => `${r}|${slug(r)}|${depthFor(r)}`).join(' ')
  return (
    `AB=${BROWSER}; B="${BASE}"; $AB close --all >/dev/null 2>&1; ` +
    `$AB set viewport ${vp.w} ${vp.h} >/dev/null 2>&1; printf '['; f=1; ` +
    `for s in ${specs}; do r=\${s%%|*}; rest=\${s#*|}; n=\${rest%%|*}; d=\${rest##*|}; ` +
    `h=$(curl -s -o /dev/null -w '%{http_code}' -L "$B$r"); ` +
    `if [ "$d" = deep ]; then $AB open "$B$r" >/dev/null 2>&1; $AB wait 1000 >/dev/null 2>&1; ` +
    `e=$($AB errors 2>/dev/null | grep -viE 'favicon|no (page )?errors|^$' | wc -l | tr -d ' '); ` +
    `o=$($AB eval 'document.documentElement.scrollWidth>window.innerWidth+2?1:0' 2>/dev/null | tail -1); ` +
    `$AB screenshot "/tmp/verify-${vp.name}-$n.png" >/dev/null 2>&1; else e=0; o=0; fi; ` +
    `ho=$([ "$h" = 200 ] && echo true || echo false); ov=$([ "$o" = 1 ] && echo true || echo false); ` +
    `ps=$([ "$h" = 200 ] && [ "$e" = 0 ] && [ "$o" != 1 ] && echo true || echo false); ` +
    `[ $f -eq 0 ] && printf ','; f=0; ` +
    `printf '{"route":"%s","viewport":"${vp.name}","httpOk":%s,"http":%s,"errorCount":%s,"overflow":%s,"pass":%s}' "$r" "$ho" "$h" "$e" "$ov" "$ps"; ` +
    `done; printf ']'; $AB close --all >/dev/null 2>&1`
  )
}

phase('Pages')
log(`Sweeping ${ROUTES.length} routes x ${VIEWPORTS.length} viewports against ${BASE}` +
  (changed ? ` (deep: ${changed.join(', ')}; rest curl-only)` : ' (all deep)'))

const sweeps = await parallel(VIEWPORTS.map((vp) => () =>
  agent(
    `Run this EXACT bash command once and return its stdout, which is a JSON array, as {"pages": <that array>}. ` +
    `Do not run any other browser commands, do not improvise, do not retry. The command is deterministic:\n\n` +
    sweepCmd(vp),
    { label: `sweep:${vp.name}`, phase: 'Pages', schema: VP_SWEEP, model: 'haiku' }
  )
))

let pages = sweeps.filter(Boolean).flatMap((s) => s.pages || [])

// Independent adversarial confirm — only the failures, one focused worker each.
const flagged = pages.filter((p) => !p.pass)
if (flagged.length) {
  phase('Confirm')
  const confirms = await parallel(flagged.map((f) => () =>
    agent(
      `A sweep reported ${BASE}${f.route} at viewport ${f.viewport} FAILED ` +
      `(http=${f.http}, errorCount=${f.errorCount}, overflow=${f.overflow}). ` +
      `Independently reproduce with a clean agent-browser session at ${BROWSER}: set the viewport, open the URL, ` +
      `wait, check page errors and horizontal overflow, screenshot. Close the session when done. ` +
      `Only set pass=false if you can actually reproduce a real problem; if the page is fine, set pass=true ` +
      `and note the sweep was a false negative. Return the structured verdict.`,
      { label: `confirm:${f.route}@${f.viewport}`, phase: 'Confirm', schema: PAGE_VERDICT }
    )
  ))
  // A failure survives only if its independent confirm also failed.
  const survived = new Set()
  confirms.filter(Boolean).forEach((c) => { if (!c.pass) survived.add(`${c.route}@${c.viewport}`) })
  pages = pages.map((p) => (!p.pass && !survived.has(`${p.route}@${p.viewport}`)) ? { ...p, pass: true, issues: ['sweep false-negative, confirmed OK'] } : p)
}

// Email capture (independent verification of the write via admin read).
let email = null
if (!PAGES_ONLY) {
  phase('Email')
  email = await agent(
    `Use the agent-browser CLI at ${BROWSER} against ${BASE} to verify the early-access email form ` +
    `(renders on / and /hub; email input has aria-label "email address", submit is button.ea-btn, ` +
    `hidden honeypot is input[name=company]). ` +
    `1) Real round-trip on desktop: submit deploy-verify-<unique>@opentraces.ai, expect HTTP 200 {ok:true} + success UI; ` +
    `confirm it persisted via GET ${BASE}/api/waitlist with header "Authorization: Bearer $${ADMIN_ENV}" ` +
    `(record count before/after); then REMOVE that row from the store (Upstash ZREM) and re-read to confirm the ` +
    `count is restored (cleanedUp=true). ` +
    `2) Dedup: submitting the same address twice shows alreadyOnList:true, count unchanged. ` +
    `3) Honeypot/mobile: fill input[name=company] and submit at 390x844; expect success UI but NO new row. ` +
    `If /api/waitlist returns 503, set pass=false and note the store is unconfigured (BLOCKED, not a code bug). ` +
    `Return the structured verdict.`,
    { label: 'email-capture', phase: 'Email', schema: EMAIL_VERDICT }
  )
}

// Merge structured results (no model in the seam).
const failures = pages.filter((p) => !p.pass)
  .map((p) => `${p.route}@${p.viewport}: http=${p.http} errors=${p.errorCount} overflow=${p.overflow}`)
if (email && !email.pass) failures.push(`email-capture: ${(email.issues || []).join('; ') || 'failed'}`)
if (email && email.pass && email.cleanedUp === false) failures.push('email-capture: test row NOT cleaned up')

return {
  base: BASE,
  scoped: { routes: ROUTES.length, viewports: VIEWPORTS.length, workers: VIEWPORTS.length, deepRoutes: changed || 'all', emailTested: !PAGES_ONLY },
  failures,
  allGreen: failures.length === 0,
}
